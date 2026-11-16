"""Stock data provider protocol and normalized Pydantic models (all ₹ values in Crores)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field, model_validator


class StockMasterItem(BaseModel):
    symbol: str
    isin: str | None = ""
    name: str
    series: str = "EQ"
    exchange: str = "NSE"
    sector: str = "Unknown"
    industry: str = "Unknown"
    is_bank: bool = False
    listing_date: str | None = None
    active: bool = True
    is_active: bool = True
    updated_at: str = ""

    @model_validator(mode="before")
    @classmethod
    def _sync_active(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            if "is_active" in d and "active" not in d:
                d["active"] = bool(d["is_active"])
            elif "active" in d and "is_active" not in d:
                d["is_active"] = bool(d["active"])
            if d.get("isin") is None:
                d["isin"] = ""
            return d
        return data

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class StockSnapshot(BaseModel):
    symbol: str
    name: str = ""
    isin: str = ""
    sector: str = "Unknown"
    industry: str = "Unknown"
    is_bank: bool = False
    as_of: str = ""
    as_of_date: str = ""
    price: float | None = None
    cmp: float | None = None
    market_cap_cr: float | None = None
    enterprise_value_cr: float | None = None
    pe: float | None = None
    pb: float | None = None
    ps: float | None = None
    ev_ebitda: float | None = None
    roe: float | None = None  # decimal (e.g. 0.245 for 24.5%)
    roa: float | None = None  # decimal
    roce: float | None = None  # decimal
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    interest_coverage: float | None = None
    dividend_yield: float | None = None  # decimal (e.g. 0.021 for 2.1%)
    eps_ttm: float | None = None
    book_value_per_share: float | None = None
    shares_outstanding: float | None = None  # in Crores
    promoter_holding_pct: float | None = None
    pledge_pct: float | None = None
    auditor_flags: int = 0
    avg_daily_traded_value_cr: float | None = None
    beta: float | None = None
    currency: str = "INR"
    last_filing_date: str | None = None
    fetched_at: str = ""
    source: str = "synthetic"
    insider_net_buy_lakhs: float = 0.0
    revenue_growth_yoy: float | None = None  # decimal
    earnings_growth_yoy: float | None = None  # decimal
    return_1y: float | None = None  # decimal
    volatility_1y: float | None = None  # decimal
    max_drawdown_1y: float | None = None  # decimal
    raw_json: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _sync_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            if d.get("price") is None and d.get("cmp") is not None:
                d["price"] = d["cmp"]
            elif d.get("cmp") is None and d.get("price") is not None:
                d["cmp"] = d["price"]

            as_of_val = d.get("as_of_date") or d.get("as_of") or ""
            if not as_of_val and d.get("fetched_at"):
                as_of_val = str(d["fetched_at"])[:10]
            d["as_of"] = str(as_of_val)
            d["as_of_date"] = str(as_of_val)
            if d.get("raw_json") is None:
                d["raw_json"] = {}
            return d
        return data

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class StockFinancialYear(BaseModel):
    """Normalized annual or quarterly financial statement in ₹ Crores."""

    symbol: str
    fiscal_year: int = 2025
    period_end: str = ""  # YYYY-MM-DD or YYYY
    period_type: Literal["annual", "quarterly"] = "annual"
    revenue_cr: float | None = None
    other_income_cr: float | None = None
    ebitda_cr: float | None = None
    ebit_cr: float | None = None
    interest_cr: float | None = None
    interest_expense_cr: float | None = None
    depreciation_cr: float | None = None
    pbt_cr: float | None = None
    tax_cr: float | None = None
    pat_cr: float | None = None
    eps: float | None = None
    dps: float | None = None
    bvps: float | None = None
    dividends_paid_cr: float | None = None
    # Balance sheet (₹ Cr)
    total_assets_cr: float | None = None
    net_worth_cr: float | None = None
    total_equity_cr: float | None = None
    total_debt_cr: float | None = None
    cash_and_equivalents_cr: float | None = None
    current_assets_cr: float | None = None
    current_liabilities_cr: float | None = None
    inventory_cr: float | None = None
    receivables_cr: float | None = None
    payables_cr: float | None = None
    fixed_assets_cr: float | None = None
    # Cash flow (₹ Cr)
    cfo_cr: float | None = None
    capex_cr: float | None = None
    cfi_cr: float | None = None
    cff_cr: float | None = None
    fcf_cr: float | None = None
    # Banking specific metrics (Session 16)
    nii_cr: float | None = None
    nim_pct: float | None = None
    gnpa_pct: float | None = None
    nnpa_pct: float | None = None
    casa_pct: float | None = None
    car_pct: float | None = None
    credit_cost_pct: float | None = None
    provision_coverage_pct: float | None = None
    advances_cr: float | None = None
    deposits_cr: float | None = None
    borrowings_cr: float | None = None
    as_of_date: str = ""
    source: str = "synthetic"

    @model_validator(mode="before")
    @classmethod
    def _sync_fy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            # Sync fiscal_year <-> period_end
            if "fiscal_year" not in d and d.get("period_end"):
                try:
                    d["fiscal_year"] = int(str(d["period_end"])[:4])
                except ValueError:
                    d["fiscal_year"] = 2025
            elif "period_end" not in d or not d.get("period_end"):
                fy = d.get("fiscal_year", 2025)
                d["period_end"] = f"{fy}-03-31"

            # Sync interest_cr <-> interest_expense_cr
            if d.get("interest_cr") is None and d.get("interest_expense_cr") is not None:
                d["interest_cr"] = d["interest_expense_cr"]
            elif d.get("interest_expense_cr") is None and d.get("interest_cr") is not None:
                d["interest_expense_cr"] = d["interest_cr"]

            # Sync net_worth_cr <-> total_equity_cr
            if d.get("net_worth_cr") is None and d.get("total_equity_cr") is not None:
                d["net_worth_cr"] = d["total_equity_cr"]
            elif d.get("total_equity_cr") is None and d.get("net_worth_cr") is not None:
                d["total_equity_cr"] = d["net_worth_cr"]

            if not d.get("as_of_date") and d.get("period_end"):
                d["as_of_date"] = str(d["period_end"])
            return d
        return data

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class StockPricePoint(BaseModel):
    symbol: str
    price_date: str = ""
    date: str = ""
    close: float
    volume: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _sync_date(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            dt = d.get("price_date") or d.get("date") or ""
            d["price_date"] = str(dt)
            d["date"] = str(dt)
            return d
        return data


class StockOHLCV(BaseModel):
    """One daily bar (prices in ₹, volume in shares)."""

    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class CorporateAction(BaseModel):
    symbol: str
    date: str
    type: Literal["split", "dividend", "bonus"]
    value: float = 0.0  # split ratio (new/old shares) or dividend per share
    detail: str = ""
    source: str = "yahoo_chart"


class StockSectorMedian(BaseModel):
    sector: str
    is_bank: bool = False
    peer_count: int = 0
    sample_size: int = 0
    as_of: str = ""
    median_pe: float | None = None
    median_pb: float | None = None
    median_ps: float | None = None
    median_ev_ebitda: float | None = None
    median_roe: float | None = None
    median_roce: float | None = None
    median_debt_to_equity: float | None = None
    median_nim_pct: float | None = None
    median_gnpa_pct: float | None = None
    median_nnpa_pct: float | None = None
    computed_at: str = ""
    updated_at: str = ""

    @model_validator(mode="before")
    @classmethod
    def _sync_sector_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            cnt = d.get("peer_count") or d.get("sample_size") or 0
            d["peer_count"] = int(cnt)
            d["sample_size"] = int(cnt)
            ts = d.get("updated_at") or d.get("computed_at") or d.get("as_of") or ""
            d["updated_at"] = str(ts)
            d["computed_at"] = str(ts)
            d["as_of"] = str(ts)
            return d
        return data

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class StockSyncRunRecord(BaseModel):
    run_id: str
    started_at: str
    finished_at: str | None = None
    mode: Literal["full", "retry_failed", "rescreen"] = "full"
    data_mode: str = "synthetic"
    status: Literal["running", "completed", "failed", "skipped"] = "running"
    total_symbols: int = 0
    processed_symbols: int = 0
    succeeded_symbols: int = 0
    failed_symbols: int = 0
    skipped_symbols: int = 0
    screen_passed_set1: int = 0
    screen_passed_set2: int = 0
    errors: list[str] = Field(default_factory=list)


@runtime_checkable
class StockDataProvider(Protocol):
    """Pluggable provider for stock universe, snapshots, financials, and prices."""

    def list_universe(self) -> list[StockMasterItem]:
        ...

    def fetch_snapshot(self, symbol: str) -> StockSnapshot | None:
        ...

    def fetch_financials(self, symbol: str) -> list[StockFinancialYear]:
        ...

    def fetch_prices(self, symbol: str) -> list[StockPricePoint]:
        ...

    def get_snapshot(self, symbol: str) -> StockSnapshot | None:
        ...

    def get_financials(self, symbol: str) -> list[StockFinancialYear]:
        ...

    def get_price_history(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[StockPricePoint]:
        ...
