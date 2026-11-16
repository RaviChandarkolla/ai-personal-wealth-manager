"""Pluggable real market-data providers (mfapi.in + AMFI NAVAll.txt, AMFI-only, yfinance hook)."""

from __future__ import annotations

import json
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

import pandas as pd
from pydantic import BaseModel, Field

from ..config import get_settings
from .amfi import enrich_master_metadata, fetch_nav_all, load_nav_all, sync_scheme_master
from .store import FundRecord, MetadataStore, load_funds_csv

log = logging.getLogger(__name__)


class SchemeNavData(BaseModel):
    code: str
    isin_growth: str | None = None
    fund_house: str | None = None
    scheme_name: str | None = None
    earliest_date: date | None = None
    latest_date: date | None = None
    nav_points: int = 0


class RealSyncReport(BaseModel):
    provider: str
    funds_loaded: int = 0
    schemes_synced: int = 0
    nav_points_saved: int = 0
    amfi_rows_loaded: int = 0
    latest_nav_date: date | None = None
    errors: list[str] = Field(default_factory=list)


class MarketDataProvider(Protocol):
    name: str

    def sync(
        self,
        store: MetadataStore,
        *,
        csv_path: Path | None = None,
        refresh_amfi: bool = False,
    ) -> RealSyncReport: ...


def fetch_mfapi_json(code: str, base_url: str | None = None, timeout: int = 25) -> dict:
    """Fetch raw JSON payload for a single AMFI scheme code from mfapi.in."""
    s = get_settings()
    root = (base_url or s.mfapi_base_url).rstrip("/")
    url = f"{root}/{code}"
    if not url.startswith("https://"):
        raise ValueError("only https URLs are allowed for mfapi")
    req = urllib.request.Request(url, headers={"User-Agent": "wealth-advisor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def parse_mfapi_payload(
    code: str,
    payload: dict,
    *,
    history_years: float | None = None,
) -> tuple[SchemeNavData, pd.Series]:
    """Parse an mfapi.in response into metadata and a chronological pandas Series of NAVs."""
    meta = payload.get("meta") or {}
    raw_rows = payload.get("data") or []
    points: dict[pd.Timestamp, float] = {}

    for item in raw_rows:
        date_s = (item.get("date") or "").strip()
        nav_s = (item.get("nav") or "").strip()
        if not date_s or not nav_s:
            continue
        try:
            dt = datetime.strptime(date_s, "%d-%m-%Y").date()
            val = float(nav_s)
        except ValueError:
            continue
        if val > 0:
            points[pd.Timestamp(dt)] = val

    if not points:
        return (
            SchemeNavData(
                code=str(code),
                isin_growth=meta.get("isin_growth") or None,
                fund_house=meta.get("fund_house") or None,
                scheme_name=meta.get("scheme_name") or None,
            ),
            pd.Series(dtype=float),
        )

    series = pd.Series(points, dtype=float).sort_index()
    earliest = series.index[0].date()
    latest = series.index[-1].date()

    if history_years is not None and history_years > 0:
        cutoff = pd.Timestamp(latest - timedelta(days=int(history_years * 365.25)))
        series = series[series.index >= cutoff]

    info = SchemeNavData(
        code=str(code),
        isin_growth=(meta.get("isin_growth") or "").strip() or None,
        fund_house=(meta.get("fund_house") or "").strip() or None,
        scheme_name=(meta.get("scheme_name") or "").strip() or None,
        earliest_date=earliest,
        latest_date=latest,
        nav_points=len(series),
    )
    return info, series


class MfapiAmfiProvider:
    """Default real-data provider: curated CSV metadata + scheme_master Direct-Growth universe + mfapi.in historical NAVs + AMFI sync."""

    name = "mfapi_amfi"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        history_years: float | None = None,
        max_workers: int = 8,
        master_per_category_limit: int = 15,
        allowed_plans: Sequence[str] | None = None,
        allowed_options: Sequence[str] | None = None,
    ) -> None:
        s = get_settings()
        self.base_url = base_url or s.mfapi_base_url
        self.history_years = history_years if history_years is not None else s.nav_history_years
        self.max_workers = max_workers
        self.master_per_category_limit = master_per_category_limit
        self.allowed_plans = (
            allowed_plans if allowed_plans is not None else s.master_allowed_plans
        )
        self.allowed_options = (
            allowed_options if allowed_options is not None else s.master_allowed_options
        )

    def sync(
        self,
        store: MetadataStore,
        *,
        csv_path: Path | None = None,
        refresh_amfi: bool = False,
        allowed_plans: Sequence[str] | None = None,
        allowed_options: Sequence[str] | None = None,
    ) -> RealSyncReport:
        s = get_settings()
        target_csv = csv_path or s.curated_funds_csv
        report = RealSyncReport(provider=self.name)
        plans = allowed_plans if allowed_plans is not None else self.allowed_plans
        options = allowed_options if allowed_options is not None else self.allowed_options

        if target_csv.exists():
            records, csv_errors = load_funds_csv(target_csv)
            report.errors.extend(csv_errors)
            if records:
                store.add_funds(records)
                report.funds_loaded = len(records)
        else:
            existing = store.query_funds()
            if not existing:
                raise FileNotFoundError(f"Curated funds CSV not found at {target_csv}")
            report.funds_loaded = len(existing)

        if refresh_amfi:
            try:
                amfi_text = fetch_nav_all()
                sync_scheme_master(
                    store, amfi_text, allowed_plans=plans, allowed_options=options
                )
                amfi_rep = load_nav_all(store, amfi_text)
                report.amfi_rows_loaded = amfi_rep.rows_loaded
            except Exception as exc:  # noqa: BLE001
                log.warning("AMFI NAVAll refresh failed: %s", exc)
                report.errors.append(f"amfi_refresh: {exc}")

            from .amfi_aum import sync_latest_aum
            from .amfi_ter import sync_latest_ter

            try:
                sync_latest_ter(store)
            except Exception as exc:  # noqa: BLE001
                log.warning("AMFI TER refresh skipped/failed: %s", exc)
            try:
                sync_latest_aum(store)
            except Exception as exc:  # noqa: BLE001
                log.warning("AMFI AUM refresh skipped/failed: %s", exc)

        enrich_master_metadata(store)

        curated_funds = store.query_funds(include_master=False)
        curated_codes = {f.code for f in curated_funds}
        master_candidates = (
            store.eligible_master_funds(
                per_category_limit=self.master_per_category_limit,
                only_unseeded=True,
            )
            if self.master_per_category_limit > 0
            else []
        )
        funds = curated_funds + [m for m in master_candidates if m.code not in curated_codes]
        if not funds:
            report.latest_nav_date = store.latest_nav_date()
            return report

        def _fetch_one(f: FundRecord) -> tuple[FundRecord, SchemeNavData, pd.Series]:
            payload = fetch_mfapi_json(f.code, base_url=self.base_url)
            info, nav_s = parse_mfapi_payload(f.code, payload, history_years=self.history_years)
            return f, info, nav_s

        updated_funds: list[FundRecord] = []
        workers = max(1, min(self.max_workers, len(funds)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_one, f): f for f in funds}
            for fut in as_completed(futures):
                f = futures[fut]
                try:
                    fund_rec, info, series = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to fetch mfapi NAV for %s (%s): %s", f.code, f.name, exc)
                    report.errors.append(f"{f.code}: {exc}")
                    continue

                if len(series) == 0:
                    report.errors.append(f"{f.code}: empty NAV series from mfapi")
                    continue

                store.save_nav(f.code, series)
                report.schemes_synced += 1
                report.nav_points_saved += len(series)

                if fund_rec.code in curated_codes:
                    updates: dict = {}
                    if info.isin_growth and info.isin_growth != fund_rec.isin:
                        updates["isin"] = info.isin_growth
                    if info.fund_house and not fund_rec.amc:
                        updates["amc"] = info.fund_house
                    if info.earliest_date and fund_rec.launch_date is None:
                        updates["launch_date"] = info.earliest_date
                    if info.latest_date:
                        updates["as_of"] = info.latest_date
                    if updates:
                        updated_funds.append(fund_rec.model_copy(update=updates))

        if updated_funds:
            store.add_funds(updated_funds)

        enrich_master_metadata(store)
        report.latest_nav_date = store.latest_nav_date()
        return report


class AmfiOnlyProvider:
    """Curated CSV metadata + latest daily NAV snapshot from AMFI NAVAll.txt."""

    name = "amfi_only"

    def __init__(
        self,
        *,
        allowed_plans: Sequence[str] | None = None,
        allowed_options: Sequence[str] | None = None,
    ) -> None:
        s = get_settings()
        self.allowed_plans = (
            allowed_plans if allowed_plans is not None else s.master_allowed_plans
        )
        self.allowed_options = (
            allowed_options if allowed_options is not None else s.master_allowed_options
        )

    def sync(
        self,
        store: MetadataStore,
        *,
        csv_path: Path | None = None,
        refresh_amfi: bool = True,
        allowed_plans: Sequence[str] | None = None,
        allowed_options: Sequence[str] | None = None,
    ) -> RealSyncReport:
        s = get_settings()
        target_csv = csv_path or s.curated_funds_csv
        report = RealSyncReport(provider=self.name)
        plans = allowed_plans if allowed_plans is not None else self.allowed_plans
        options = allowed_options if allowed_options is not None else self.allowed_options

        if target_csv.exists():
            records, csv_errors = load_funds_csv(target_csv)
            report.errors.extend(csv_errors)
            if records:
                store.add_funds(records)
                report.funds_loaded = len(records)

        if refresh_amfi:
            amfi_text = fetch_nav_all()
            sync_scheme_master(
                store, amfi_text, allowed_plans=plans, allowed_options=options
            )
            amfi_rep = load_nav_all(store, amfi_text)
            report.amfi_rows_loaded = amfi_rep.rows_loaded
            report.schemes_synced = amfi_rep.rows_loaded
            report.nav_points_saved = amfi_rep.rows_loaded
            report.errors.extend(amfi_rep.isin_mismatch)

        enrich_master_metadata(store)
        report.latest_nav_date = store.latest_nav_date()
        return report


class YFinanceProvider:
    """Placeholder provider for future Yahoo Finance ETF / index / global ticker integration."""

    name = "yfinance"

    def sync(
        self,
        store: MetadataStore,
        *,
        csv_path: Path | None = None,
        refresh_amfi: bool = False,
    ) -> RealSyncReport:
        raise NotImplementedError(
            "YFinanceProvider is registered as a pluggable extension point; "
            "use WEALTH_REAL_DATA_PROVIDER=mfapi_amfi or amfi_only."
        )


def get_market_data_provider(name: str | None = None) -> MarketDataProvider:
    s = get_settings()
    chosen = (name or s.real_data_provider).lower()
    if chosen == "mfapi_amfi":
        return MfapiAmfiProvider()
    if chosen == "amfi_only":
        return AmfiOnlyProvider()
    if chosen == "yfinance":
        return YFinanceProvider()
    raise ValueError(f"Unknown real_data_provider: {chosen!r}")
