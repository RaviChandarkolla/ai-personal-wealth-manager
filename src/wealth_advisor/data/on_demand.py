"""On-demand NAV fetching and FundProfile computation for any scheme code."""

import logging
from datetime import date
from functools import lru_cache

import pandas as pd
from pydantic import BaseModel

from ..config import get_settings
from ..domain.categories import CATEGORIES, bucket_for, normalize_category
from ..domain.metrics import (
    alpha,
    annualized_volatility,
    beta,
    cagr,
    calendar_returns,
    max_drawdown,
    rolling_consistency,
    rolling_returns,
    sharpe,
    sortino,
    tracking_error,
)
from ..models.core import Citation, Fact
from ..models.enums import Bucket
from .amfi import default_ter_aum_for_master
from .real_fetcher import fetch_mfapi_json, parse_mfapi_payload
from .store import FundRecord, MetadataStore, load_funds_csv

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _curated_csv_lookup() -> dict[str, FundRecord]:
    csv_path = get_settings().curated_funds_csv
    if not csv_path.exists():
        return {}
    try:
        records, _ = load_funds_csv(csv_path)
        return {r.code: r for r in records}
    except Exception:
        return {}


class FundProfile(BaseModel):
    code: str
    name: str
    amc: str = ""
    category: str
    sebi_category: str = ""
    bucket: Bucket | None = None
    plan: str = "direct"
    option: str = "growth"
    source: str = "AMFI"
    as_of: date
    is_curated: bool = False

    # Metadata metrics
    expense_ratio: float | None = None
    aum_cr: float | None = None
    fund_age_years: float | None = None
    manager: str = ""
    manager_tenure_years: float | None = None
    benchmark: str = ""
    ytm: float | None = None
    modified_duration: float | None = None
    avg_maturity_years: float | None = None

    # NAV-derived metrics (returns/vol/drawdown in %, ratios as numbers)
    cagr_1y: float | None = None
    cagr_3y: float | None = None
    cagr_5y: float | None = None
    roll_3y_median: float | None = None
    rolling_consistency_3y: float | None = None
    volatility: float | None = None
    max_drawdown: float | None = None  # positive % magnitude of peak-to-trough drop
    sharpe: float | None = None
    sortino: float | None = None
    alpha: float | None = None
    beta: float | None = None
    tracking_error: float | None = None  # annualized %

    calendar_returns: dict[int, float] = {}
    nav_history: list[dict[str, str | float]] = []
    facts: list[Fact] = []


def _fetch_and_save_mfapi(store: MetadataStore, code: str) -> pd.Series:
    payload = fetch_mfapi_json(code)
    info, series = parse_mfapi_payload(code, payload)
    if not series.empty:
        store.save_nav(code, series)
        master = store.get_master(code)
        if master is not None:
            updates: dict = {}
            if info.isin_growth and not master.isin_growth:
                updates["isin_growth"] = info.isin_growth
            if info.fund_house and not master.amc:
                updates["amc"] = info.fund_house
            if info.latest_date and (master.nav_date is None or info.latest_date >= master.nav_date):
                updates["nav_date"] = info.latest_date
                updates["latest_nav"] = float(series.iloc[-1])
            if updates:
                store.upsert_master([master.model_copy(update=updates)])
    return store.nav_series(code)


def ensure_nav(
    store: MetadataStore,
    code: str,
    *,
    fetch_benchmark: bool = False,
) -> pd.Series:
    """Return NAV series for `code`, fetching from mfapi.in if missing or stale."""
    series = store.nav_series(code)
    master = store.get_master(code)
    fund = store.get_fund(code)

    is_demo = code.startswith("DEMO_")
    needs_fetch = False
    if not is_demo:
        if series.empty:
            needs_fetch = True
        elif master and master.nav_date and series.index[-1].date() < master.nav_date:
            needs_fetch = True

    if needs_fetch:
        try:
            series = _fetch_and_save_mfapi(store, code)
        except Exception as exc:
            log.warning("On-demand mfapi NAV fetch failed for scheme %s: %s", code, exc)
            series = store.nav_series(code)

    # Ensure benchmark proxy NAV is also available when requested
    category = (fund.category if fund else None) or (master.category if master else "")
    bucket = (fund.bucket if fund else None) or bucket_for(category)
    if fetch_benchmark and bucket is not None and not is_demo:
        s = get_settings()
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
            b_series = store.nav_series(proxy_code)
            if b_series.empty:
                try:
                    _fetch_and_save_mfapi(store, proxy_code)
                except Exception as exc:
                    log.warning("On-demand benchmark NAV fetch failed for %s: %s", proxy_code, exc)

    return series


def _resolve_benchmark_nav(
    store: MetadataStore,
    code: str,
    category: str,
    bucket: Bucket | None,
) -> pd.Series:
    if bucket is None:
        return pd.Series(dtype=float)
    s = get_settings()
    is_demo = code.startswith("DEMO_")
    if not is_demo:
        proxy_code = (
            s.benchmark_proxy_codes.get(category)
            or s.benchmark_proxy_codes.get(bucket.value)
        )
        if proxy_code:
            b_series = store.nav_series(proxy_code)
            if not b_series.empty:
                return b_series
    idx_funds = [x for x in store.query_funds(bucket=bucket) if x.category in ("Index", "Index Fund")]
    if idx_funds:
        b_series = store.nav_series(idx_funds[0].code)
        if not b_series.empty:
            return b_series
    bucket_funds = store.query_funds(bucket=bucket)
    for bf in bucket_funds:
        if bf.code == code:
            continue
        b_series = store.nav_series(bf.code)
        if not b_series.empty:
            return b_series
    return pd.Series(dtype=float)


def compute_fund_profile(store: MetadataStore, code: str, *, fetch_if_missing: bool = True) -> FundProfile:
    """Build a complete FundProfile with Facts and Citations for `code`."""
    fund = store.get_fund(code)
    master = store.get_master(code)
    if fund is None and fetch_if_missing and not code.startswith("DEMO_"):
        csv_fund = _curated_csv_lookup().get(code)
        if csv_fund is not None:
            store.add_funds([csv_fund])
            fund = csv_fund
    if fund is None and master is None:
        raise KeyError(f"Unknown scheme code: {code}")

    nav = ensure_nav(store, code, fetch_benchmark=True) if fetch_if_missing else store.nav_series(code)
    master = store.get_master(code)

    name = (fund.name if fund else None) or (master.name if master else code)
    amc = (fund.amc if fund and fund.amc else None) or (master.amc if master else "")
    raw_category = (fund.category if fund else None) or (master.category if master else "Uncategorized")
    category = normalize_category(raw_category) if raw_category not in CATEGORIES else raw_category
    sebi_category = (master.sebi_category if master and master.sebi_category else raw_category)
    bucket = (fund.bucket if fund else None) or bucket_for(category)
    plan = (fund.plan if fund else None) or (master.plan if master else "direct")
    option = (fund.option if fund else None) or (master.option if master else "growth")
    source = (fund.source if fund else None) or (master.source if master else "AMFI")

    nav_as_of = nav.index[-1].date() if not nav.empty else None
    as_of = (
        nav_as_of
        or (master.nav_date if master and master.nav_date else None)
        or (fund.as_of if fund else None)
        or (master.as_of if master else date.today())
    )

    ter_def, aum_def = default_ter_aum_for_master(code, category, plan)
    expense_ratio = (
        fund.expense_ratio
        if (fund and fund.expense_ratio is not None)
        else (master.expense_ratio if (master and master.expense_ratio is not None) else ter_def)
    )
    ter_as_of = (master.ter_as_of if master and master.ter_as_of else as_of)

    aum_cr = (
        fund.aum_cr
        if (fund and fund.aum_cr is not None)
        else (master.aum_cr if (master and master.aum_cr is not None) else aum_def)
    )
    aum_as_of = (master.aum_as_of if master and master.aum_as_of else as_of)

    if master is not None and (master.expense_ratio is None or master.aum_cr is None):
        store.upsert_master(
            [
                master.model_copy(
                    update={
                        "expense_ratio": expense_ratio,
                        "ter_as_of": ter_as_of,
                        "aum_cr": aum_cr,
                        "aum_as_of": aum_as_of,
                    }
                )
            ]
        )

    # Fund age: prefer curated launch_date, fallback to first NAV date
    fund_age_years: float | None = None
    if fund and fund.launch_date:
        days = (as_of - fund.launch_date).days
        if days > 0:
            fund_age_years = round(days / 365.25, 2)
    elif not nav.empty and len(nav) >= 2:
        days = (nav.index[-1].date() - nav.index[0].date()).days
        if days > 0:
            fund_age_years = round(days / 365.25, 2)

    # Curated-only fields
    manager = fund.manager if fund and fund.manager else ""
    manager_tenure_years: float | None = None
    if fund and fund.manager_since:
        t_days = (as_of - fund.manager_since).days
        if t_days >= 0:
            manager_tenure_years = round(t_days / 365.25, 2)

    benchmark = (fund.benchmark if fund and fund.benchmark else "") or (
        getattr(CATEGORIES[category], "benchmark", "") if category in CATEGORIES else ""
    )
    ytm = fund.ytm if fund else None
    modified_duration = fund.modified_duration if fund else None
    avg_maturity_years = fund.avg_maturity_years if fund else None

    cagr_1y = cagr_3y = cagr_5y = roll_3y_med = roll_cons_3y = None
    vol = mdd = sh = so = al = be = te = None
    cal_ret: dict[int, float] = {}
    nav_hist: list[dict[str, str | float]] = []

    rf = get_settings().risk_free_rate
    if len(nav) >= 2:
        span_years = (nav.index[-1] - nav.index[0]).days / 365.25

        def _window_cagr(yrs: int) -> float | None:
            if span_years < yrs - 0.05:
                return None
            sub = nav[nav.index >= nav.index[-1] - pd.DateOffset(years=yrs)]
            return round(cagr(sub) * 100, 2) if len(sub) >= 2 else None

        cagr_1y = _window_cagr(1)
        cagr_3y = _window_cagr(3)
        cagr_5y = _window_cagr(5)

        if span_years >= 3 - 0.05:
            r3 = rolling_returns(nav, 3)
            if not r3.empty:
                roll_3y_med = round(float(r3.median()) * 100, 2)
                roll_cons_3y = round(rolling_consistency(nav, 3, rf) * 100, 2)

        vol = round(annualized_volatility(nav) * 100, 2)
        mdd = round(abs(max_drawdown(nav)) * 100, 2)
        sh = round(sharpe(nav, rf), 2)
        so = round(sortino(nav, rf), 2)

        raw_cal = calendar_returns(nav)
        cal_ret = {yr: round(v * 100, 2) for yr, v in raw_cal.items()}

        b_series = _resolve_benchmark_nav(store, code, category, bucket)
        if len(b_series) >= 2:
            al = alpha(nav, b_series, rf)
            be = round(beta(nav, b_series), 2)
            if category in ("Index", "Gold ETF / FoF") or "index" in category.lower() or "etf" in category.lower():
                te = round(tracking_error(nav, b_series) * 100, 3)

        # Sample up to ~120 points for chart performance
        step = max(1, len(nav) // 120)
        sampled = nav.iloc[::step]
        if sampled.index[-1] != nav.index[-1]:
            sampled = pd.concat([sampled, nav.iloc[[-1]]])
        nav_hist = [
            {"date": dt.strftime("%Y-%m-%d"), "nav": round(float(val), 4)}
            for dt, val in sampled.items()
        ]

    facts: list[Fact] = []

    def _add_fact(metric: str, val: float | str | int | None, src: str = source, dt: date = as_of) -> None:
        if val is None or val == "":
            return
        facts.append(
            Fact(
                id=f"{code}:{metric}",
                name=f"{name} {metric}",
                value=val,
                citation=Citation(source=src, as_of=dt, url_or_doc_id=f"scheme:{code}"),
            )
        )

    _add_fact("plan_type", plan)
    _add_fact("expense_ratio", expense_ratio, src=f"{source}:TER", dt=ter_as_of)
    _add_fact("aum_cr", aum_cr, src=f"{source}:AUM", dt=aum_as_of)
    _add_fact("fund_age_years", fund_age_years)
    _add_fact("manager_tenure_years", manager_tenure_years)
    _add_fact("cagr_1y", cagr_1y)
    _add_fact("cagr_3y", cagr_3y)
    _add_fact("cagr_5y", cagr_5y)
    _add_fact("roll_3y_median", roll_3y_med)
    _add_fact("rolling_consistency_3y", roll_cons_3y)
    _add_fact("volatility", vol)
    _add_fact("max_drawdown", mdd)
    _add_fact("sharpe", sh)
    _add_fact("sortino", so)
    _add_fact("alpha", al)
    _add_fact("beta", be)
    _add_fact("tracking_error", te)
    _add_fact("ytm", ytm)
    _add_fact("modified_duration", modified_duration)
    _add_fact("avg_maturity_years", avg_maturity_years)

    return FundProfile(
        code=code,
        name=name,
        amc=amc,
        category=category,
        sebi_category=sebi_category,
        bucket=bucket,
        plan=plan,
        option=option,
        source=source,
        as_of=as_of,
        is_curated=fund is not None,
        expense_ratio=expense_ratio,
        aum_cr=aum_cr,
        fund_age_years=fund_age_years,
        manager=manager,
        manager_tenure_years=manager_tenure_years,
        benchmark=benchmark,
        ytm=ytm,
        modified_duration=modified_duration,
        avg_maturity_years=avg_maturity_years,
        cagr_1y=cagr_1y,
        cagr_3y=cagr_3y,
        cagr_5y=cagr_5y,
        roll_3y_median=roll_3y_med,
        rolling_consistency_3y=roll_cons_3y,
        volatility=vol,
        max_drawdown=mdd,
        sharpe=sh,
        sortino=so,
        alpha=al,
        beta=be,
        tracking_error=te,
        calendar_returns=cal_ret,
        nav_history=nav_hist,
        facts=facts,
    )
