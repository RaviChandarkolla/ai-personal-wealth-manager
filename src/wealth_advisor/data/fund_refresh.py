"""Single-fund live refresh orchestrator (mfapi NAV + optional AMFI TER/AUM + news + stats)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel

from ..config import get_settings
from ..domain.categories import bucket_for
from .amfi_aum import sync_latest_aum
from .amfi_ter import sync_latest_ter
from .news import get_fund_news
from .real_fetcher import fetch_mfapi_json, parse_mfapi_payload
from .stats import compute_all_stats
from .store import FundRefreshRecord, MetadataStore

log = logging.getLogger(__name__)


class FundRefreshReport(BaseModel):
    id: int | None = None
    code: str
    refreshed_at: str
    data_mode: str = "real"
    sources: list[str] = []
    status: Literal["ok", "partial", "failed"] = "ok"
    nav_points_before: int = 0
    nav_points_after: int = 0
    nav_date_before: date | None = None
    nav_date_after: date | None = None
    latest_nav: float | None = None
    ter_before: float | None = None
    ter_after: float | None = None
    aum_before: float | None = None
    aum_after: float | None = None
    news_score: float | None = None
    sentiment_score: float | None = None
    changed_fields: list[str] = []
    errors: list[str] = []


def refresh_fund(
    store: MetadataStore,
    code: str,
    *,
    include_ter_aum: bool = False,
    include_news: bool = True,
    data_mode: str = "real",
) -> FundRefreshReport:
    """Pull latest NAV, optional AMFI TER/AUM, and live news for `code`, recomputing stats and logging the run."""
    s = get_settings()
    fund = store.get_fund(code)
    master = store.get_master(code)

    nav_before = store.nav_series(code)
    nav_points_before = len(nav_before)
    nav_date_before: date | None = (
        nav_before.index[-1].date()
        if not nav_before.empty
        else (master.nav_date if master else None)
    )
    ter_before: float | None = (
        fund.expense_ratio
        if (fund and fund.expense_ratio is not None)
        else (master.expense_ratio if master else None)
    )
    aum_before: float | None = (
        fund.aum_cr
        if (fund and fund.aum_cr is not None)
        else (master.aum_cr if master else None)
    )

    sources: list[str] = []
    errors: list[str] = []
    mfapi_ok = False

    # 1. Unconditional mfapi NAV + metadata fetch
    try:
        payload = fetch_mfapi_json(code)
        info, series = parse_mfapi_payload(code, payload, history_years=s.nav_history_years)
        if series.empty:
            errors.append(f"mfapi: returned empty NAV series for {code}")
        else:
            store.save_nav(code, series)
            sources.append("mfapi")
            mfapi_ok = True

            if master is not None:
                m_updates: dict = {}
                if info.isin_growth:
                    m_updates["isin_growth"] = info.isin_growth
                if info.fund_house:
                    m_updates["amc"] = info.fund_house
                if info.scheme_name:
                    m_updates["name"] = info.scheme_name
                if info.latest_date:
                    m_updates["nav_date"] = info.latest_date
                    m_updates["latest_nav"] = float(series.iloc[-1])
                if m_updates:
                    store.upsert_master([master.model_copy(update=m_updates)])

            if fund is not None:
                f_updates: dict = {}
                if info.isin_growth:
                    f_updates["isin"] = info.isin_growth
                if info.fund_house and not fund.amc:
                    f_updates["amc"] = info.fund_house
                if info.earliest_date and fund.launch_date is None:
                    f_updates["launch_date"] = info.earliest_date
                if info.latest_date:
                    f_updates["as_of"] = info.latest_date
                if f_updates:
                    store.add_funds([fund.model_copy(update=f_updates)])
    except Exception as exc:
        log.warning("Live mfapi refresh failed for %s: %s", code, exc)
        errors.append(f"mfapi: {exc}")

    # 2. Optional TER + AUM refresh from AMFI
    if include_ter_aum:
        try:
            sync_latest_ter(store, only_codes={code})
            sources.append("amfi_ter")
        except Exception as exc:
            log.warning("Live AMFI TER refresh failed for %s: %s", code, exc)
            errors.append(f"amfi_ter: {exc}")

        try:
            sync_latest_aum(store, only_codes={code})
            sources.append("amfi_aum")
        except Exception as exc:
            log.warning("Live AMFI AUM refresh failed for %s: %s", code, exc)
            errors.append(f"amfi_aum: {exc}")

    # Re-read updated scheme / master rows
    fund = store.get_fund(code)
    master = store.get_master(code)
    category = (fund.category if fund else None) or (master.category if master else "Uncategorized")
    bucket = (fund.bucket if fund else None) or bucket_for(category)

    # 3. Benchmark proxy NAV ensure (if missing)
    if bucket is not None and not code.startswith("DEMO_"):
        proxy_code = (
            s.benchmark_proxy_codes.get(category)
            or s.benchmark_proxy_codes.get(bucket.value)
        )
        if (
            proxy_code
            and proxy_code != code
            and not proxy_code.startswith("DEMO_")
            and (store.get_fund(proxy_code) is not None or store.get_master(proxy_code) is not None)
        ):
            if store.nav_series(proxy_code).empty:
                try:
                    b_payload = fetch_mfapi_json(proxy_code)
                    _, b_series = parse_mfapi_payload(
                        proxy_code, b_payload, history_years=s.nav_history_years
                    )
                    if not b_series.empty:
                        store.save_nav(proxy_code, b_series)
                except Exception as exc:
                    log.warning("Benchmark proxy NAV fetch failed for %s: %s", proxy_code, exc)

    # 4. Recompute fund_stats & category percentiles
    try:
        compute_all_stats(store)
    except Exception as exc:
        log.warning("compute_all_stats failed during refresh of %s: %s", code, exc)
        errors.append(f"stats: {exc}")

    # Snapshot "after" NAV & TER/AUM
    nav_after = store.nav_series(code)
    nav_points_after = len(nav_after)
    nav_date_after: date | None = (
        nav_after.index[-1].date()
        if not nav_after.empty
        else (master.nav_date if master else None)
    )
    latest_nav: float | None = (
        round(float(nav_after.iloc[-1]), 4)
        if not nav_after.empty
        else (master.latest_nav if master else None)
    )
    ter_after: float | None = (
        fund.expense_ratio
        if (fund and fund.expense_ratio is not None)
        else (master.expense_ratio if master else None)
    )
    aum_after: float | None = (
        fund.aum_cr
        if (fund and fund.aum_cr is not None)
        else (master.aum_cr if master else None)
    )

    # 5. Live news & sentiment refresh (bypassing 15-min TTL cache)
    news_score: float | None = None
    sentiment_score: float | None = None
    if include_news:
        try:
            name = (fund.name if fund else None) or (master.name if master else code)
            amc = (fund.amc if fund and fund.amc else None) or (master.amc if master else "")
            news_summary = get_fund_news(
                code=code,
                name=name,
                category=category,
                amc=amc,
                as_of=nav_date_after or date.today(),
                fetch_live=True,
                refresh=True,
            )
            news_score = news_summary.news_score
            sentiment_score = news_summary.sentiment_score
            sources.append("google_news_rss")
        except Exception as exc:
            log.warning("News refresh failed for %s: %s", code, exc)
            errors.append(f"news: {exc}")

    changed_fields: list[str] = []
    if nav_points_after != nav_points_before:
        changed_fields.append("nav_points")
    if nav_date_after != nav_date_before:
        changed_fields.append("nav_date")
    if ter_after != ter_before:
        changed_fields.append("expense_ratio")
    if aum_after != aum_before:
        changed_fields.append("aum_cr")
    if news_score is not None:
        changed_fields.append("news")

    status: Literal["ok", "partial", "failed"]
    if not mfapi_ok and errors:
        status = "failed" if not sources else "partial"
    elif errors:
        status = "partial"
    else:
        status = "ok"

    refreshed_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rec = store.log_refresh(
        FundRefreshRecord(
            code=code,
            refreshed_at=refreshed_at,
            data_mode=data_mode,
            sources=sources,
            status=status,
            nav_points_before=nav_points_before,
            nav_points_after=nav_points_after,
            nav_date_before=nav_date_before,
            nav_date_after=nav_date_after,
            latest_nav=latest_nav,
            ter_before=ter_before,
            ter_after=ter_after,
            aum_before=aum_before,
            aum_after=aum_after,
            news_score=news_score,
            sentiment_score=sentiment_score,
            errors=errors,
        )
    )

    return FundRefreshReport(
        **rec.model_dump(),
        changed_fields=changed_fields,
    )
