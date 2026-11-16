"""Stock financial ratio calculations, CAGRs, efficiency & bank metrics (FA Course Notes)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from wealth_advisor.data.stocks.provider import StockFinancialYear, StockSnapshot


def compute_cagr(
    start_value: float | None,
    end_value: float | None,
    periods: int,
) -> float | None:
    """Compute Compound Annual Growth Rate (CAGR) over `periods` years.

    Returns None when periods <= 0 or either value is None or <= 0.
    """
    if (
        periods <= 0
        or start_value is None
        or end_value is None
        or start_value <= 0
        or end_value <= 0
    ):
        return None
    return float((end_value / start_value) ** (1.0 / periods) - 1.0)


def compute_series_cagr(values: list[float | None]) -> float | None:
    """Compute CAGR from an ordered chronological series (oldest -> newest)."""
    valid = [(idx, v) for idx, v in enumerate(values) if v is not None and v > 0]
    if len(valid) < 2:
        return None
    start_idx, start_val = valid[0]
    end_idx, end_val = valid[-1]
    periods = end_idx - start_idx
    if periods <= 0:
        return None
    return compute_cagr(start_val, end_val, periods)


@dataclass
class ExpertAcademyStatements:
    """Worked example from FA Course Notes Section 3 (Expert Academy financial statements)."""

    revenue_from_operations: float = 500_000.0
    other_income: float = 100_000.0
    electricity_expense: float = 15_000.0
    salary_expense: float = 225_000.0
    interest_expense: float = 60_000.0
    depreciation_expense: float = 90_000.0
    tax_rate: float = 0.10

    @property
    def total_income(self) -> float:
        return self.revenue_from_operations + self.other_income

    @property
    def ebitda(self) -> float:
        return (
            self.total_income
            - self.electricity_expense
            - self.salary_expense
        )

    @property
    def ebit(self) -> float:
        return self.ebitda - self.depreciation_expense

    @property
    def total_expenses(self) -> float:
        return (
            self.electricity_expense
            + self.salary_expense
            + self.interest_expense
            + self.depreciation_expense
        )

    @property
    def pbt(self) -> float:
        return self.total_income - self.total_expenses

    @property
    def tax(self) -> float:
        return max(0.0, self.pbt * self.tax_rate)

    @property
    def pat(self) -> float:
        return self.pbt - self.tax


@dataclass
class StockComputedRatios:
    """Full set of fundamental ratios computed for a stock across its 5Y financials + snapshot."""

    symbol: str
    is_bank: bool
    # 5Y CAGRs (decimals, e.g. 0.12 = 12%)
    revenue_cagr_5y: float | None = None
    pat_cagr_5y: float | None = None
    eps_cagr_5y: float | None = None
    dps_cagr_5y: float | None = None
    nii_cagr_5y: float | None = None

    # YoY growth (decimals)
    revenue_growth_yoy: float | None = None
    earnings_growth_yoy: float | None = None

    # Profitability (decimals, e.g. 0.18 = 18%)
    roe: float | None = None
    roa: float | None = None
    roce: float | None = None
    ebitda_margin: float | None = None
    pat_margin: float | None = None

    # Leverage (ratios)
    debt_to_equity: float | None = None
    interest_coverage: float | None = None

    # Liquidity (ratios)
    current_ratio: float | None = None
    quick_ratio: float | None = None

    # Cash flow & efficiency
    cfo_to_pat: float | None = None
    cfo_5y_sum_cr: float | None = None
    pat_5y_sum_cr: float | None = None
    fcf_cr: float | None = None
    capex_cr: float | None = None
    working_capital_cr: float | None = None
    asset_turnover: float | None = None
    inventory_turnover: float | None = None
    receivable_days: float | None = None
    payable_days: float | None = None
    cash_conversion_cycle_days: float | None = None

    # Dividend metrics (decimals)
    dividend_yield: float | None = None
    payout_ratio: float | None = None
    retention_ratio: float | None = None
    sustainable_growth_rate: float | None = None
    dividends_increasing: bool | None = None

    # Valuation multiples
    pe: float | None = None
    pb: float | None = None
    ps: float | None = None
    ev_ebitda: float | None = None
    peg_ratio: float | None = None

    # Ownership & Governance
    promoter_holding_pct: float | None = None
    pledge_pct: float | None = None
    auditor_flags: int = 0
    insider_net_buy_lakhs: float = 0.0

    # Market & liquidity
    price: float | None = None
    market_cap_cr: float | None = None
    avg_daily_traded_value_cr: float | None = None
    return_1y: float | None = None
    volatility_1y: float | None = None
    max_drawdown_1y: float | None = None

    # Bank-specific metrics (percentage points as stored, e.g. 4.1 = 4.1%, 1.2 = 1.2%)
    nim_pct: float | None = None
    gnpa_pct: float | None = None
    nnpa_pct: float | None = None
    casa_pct: float | None = None
    car_pct: float | None = None
    credit_cost_pct: float | None = None
    provision_coverage_pct: float | None = None
    cd_ratio: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_year_ratios(fy: StockFinancialYear) -> dict[str, float | None]:
    """Compute single-year financial ratios from a StockFinancialYear row."""
    current_ratio: float | None = None
    if (
        fy.current_assets_cr is not None
        and fy.current_liabilities_cr is not None
        and fy.current_liabilities_cr > 0
    ):
        current_ratio = fy.current_assets_cr / fy.current_liabilities_cr

    quick_ratio: float | None = None
    if (
        fy.current_assets_cr is not None
        and fy.current_liabilities_cr is not None
        and fy.current_liabilities_cr > 0
    ):
        inv = fy.inventory_cr or 0.0
        quick_ratio = max(0.0, fy.current_assets_cr - inv) / fy.current_liabilities_cr

    working_capital_cr: float | None = None
    if fy.current_assets_cr is not None and fy.current_liabilities_cr is not None:
        working_capital_cr = fy.current_assets_cr - fy.current_liabilities_cr

    debt_to_equity: float | None = None
    if (
        fy.total_debt_cr is not None
        and fy.net_worth_cr is not None
        and fy.net_worth_cr > 0
    ):
        debt_to_equity = fy.total_debt_cr / fy.net_worth_cr

    interest_coverage: float | None = None
    if fy.ebit_cr is not None and fy.interest_cr is not None:
        if fy.interest_cr > 0:
            interest_coverage = fy.ebit_cr / fy.interest_cr
        elif fy.ebit_cr > 0:
            interest_coverage = 100.0

    roe: float | None = None
    if fy.pat_cr is not None and fy.net_worth_cr is not None and fy.net_worth_cr > 0:
        roe = fy.pat_cr / fy.net_worth_cr

    roa: float | None = None
    if (
        fy.pat_cr is not None
        and fy.total_assets_cr is not None
        and fy.total_assets_cr > 0
    ):
        roa = fy.pat_cr / fy.total_assets_cr

    roce: float | None = None
    if (
        fy.ebit_cr is not None
        and fy.total_assets_cr is not None
        and fy.current_liabilities_cr is not None
    ):
        cap_employed = fy.total_assets_cr - fy.current_liabilities_cr
        if cap_employed > 0:
            roce = fy.ebit_cr / cap_employed

    ebitda_margin: float | None = None
    if fy.ebitda_cr is not None and fy.revenue_cr is not None and fy.revenue_cr > 0:
        ebitda_margin = fy.ebitda_cr / fy.revenue_cr

    pat_margin: float | None = None
    if fy.pat_cr is not None and fy.revenue_cr is not None and fy.revenue_cr > 0:
        pat_margin = fy.pat_cr / fy.revenue_cr

    cfo_to_pat: float | None = None
    if fy.cfo_cr is not None and fy.pat_cr is not None and fy.pat_cr > 0:
        cfo_to_pat = fy.cfo_cr / fy.pat_cr

    fcf_cr = fy.fcf_cr
    if fcf_cr is None and fy.cfo_cr is not None and fy.capex_cr is not None:
        fcf_cr = fy.cfo_cr - abs(fy.capex_cr)

    asset_turnover: float | None = None
    if (
        fy.revenue_cr is not None
        and fy.total_assets_cr is not None
        and fy.total_assets_cr > 0
    ):
        asset_turnover = fy.revenue_cr / fy.total_assets_cr

    inventory_turnover: float | None = None
    if (
        fy.revenue_cr is not None
        and fy.inventory_cr is not None
        and fy.inventory_cr > 0
    ):
        inventory_turnover = fy.revenue_cr / fy.inventory_cr

    receivable_days: float | None = None
    if (
        fy.receivables_cr is not None
        and fy.revenue_cr is not None
        and fy.revenue_cr > 0
    ):
        receivable_days = (fy.receivables_cr / fy.revenue_cr) * 365.0

    payable_days: float | None = None
    if (
        fy.payables_cr is not None
        and fy.revenue_cr is not None
        and fy.revenue_cr > 0
    ):
        payable_days = (fy.payables_cr / fy.revenue_cr) * 365.0

    payout_ratio: float | None = None
    if fy.dps is not None and fy.eps is not None and fy.eps > 0:
        payout_ratio = min(1.5, max(0.0, fy.dps / fy.eps))
    elif (
        fy.dividends_paid_cr is not None
        and fy.pat_cr is not None
        and fy.pat_cr > 0
    ):
        payout_ratio = min(1.5, max(0.0, fy.dividends_paid_cr / fy.pat_cr))

    retention_ratio: float | None = (
        1.0 - payout_ratio if payout_ratio is not None else None
    )

    cd_ratio: float | None = None
    if (
        fy.advances_cr is not None
        and fy.deposits_cr is not None
        and fy.deposits_cr > 0
    ):
        cd_ratio = (fy.advances_cr / fy.deposits_cr) * 100.0

    return {
        "current_ratio": current_ratio,
        "quick_ratio": quick_ratio,
        "working_capital_cr": working_capital_cr,
        "debt_to_equity": debt_to_equity,
        "interest_coverage": interest_coverage,
        "roe": roe,
        "roa": roa,
        "roce": roce,
        "ebitda_margin": ebitda_margin,
        "pat_margin": pat_margin,
        "cfo_to_pat": cfo_to_pat,
        "fcf_cr": fcf_cr,
        "asset_turnover": asset_turnover,
        "inventory_turnover": inventory_turnover,
        "receivable_days": receivable_days,
        "payable_days": payable_days,
        "payout_ratio": payout_ratio,
        "retention_ratio": retention_ratio,
        "cd_ratio": cd_ratio,
    }


def compute_stock_ratios(
    snapshot: StockSnapshot,
    financials: list[StockFinancialYear],
    *,
    is_bank: bool = False,
) -> StockComputedRatios:
    """Compute comprehensive multi-year and TTM/latest ratios for a stock."""
    ordered = sorted(financials, key=lambda x: x.fiscal_year)
    latest = ordered[-1] if ordered else None
    prev = ordered[-2] if len(ordered) >= 2 else None

    revenue_cagr_5y = compute_series_cagr([f.revenue_cr for f in ordered])
    pat_cagr_5y = compute_series_cagr([f.pat_cr for f in ordered])
    eps_cagr_5y = compute_series_cagr([f.eps for f in ordered])
    dps_cagr_5y = compute_series_cagr([f.dps for f in ordered])
    nii_cagr_5y = compute_series_cagr([f.nii_cr for f in ordered])

    revenue_growth_yoy = snapshot.revenue_growth_yoy
    if (
        revenue_growth_yoy is None
        and latest is not None
        and prev is not None
        and latest.revenue_cr is not None
        and prev.revenue_cr is not None
        and prev.revenue_cr > 0
    ):
        revenue_growth_yoy = (latest.revenue_cr - prev.revenue_cr) / prev.revenue_cr

    earnings_growth_yoy = snapshot.earnings_growth_yoy
    if (
        earnings_growth_yoy is None
        and latest is not None
        and prev is not None
        and latest.pat_cr is not None
        and prev.pat_cr is not None
        and prev.pat_cr > 0
    ):
        earnings_growth_yoy = (latest.pat_cr - prev.pat_cr) / prev.pat_cr

    yr = compute_year_ratios(latest) if latest is not None else {}

    roe = snapshot.roe if snapshot.roe is not None else yr.get("roe")
    roa = snapshot.roa if snapshot.roa is not None else yr.get("roa")
    roce = snapshot.roce if snapshot.roce is not None else yr.get("roce")
    ebitda_margin = yr.get("ebitda_margin")
    pat_margin = yr.get("pat_margin")

    debt_to_equity = (
        snapshot.debt_to_equity
        if snapshot.debt_to_equity is not None
        else yr.get("debt_to_equity")
    )
    interest_coverage = (
        snapshot.interest_coverage
        if snapshot.interest_coverage is not None
        else yr.get("interest_coverage")
    )
    current_ratio = (
        snapshot.current_ratio
        if snapshot.current_ratio is not None
        else yr.get("current_ratio")
    )
    quick_ratio = yr.get("quick_ratio")

    # 5Y cumulative CFO / PAT (standard management efficiency check in FA Course Notes)
    cfo_vals = [f.cfo_cr for f in ordered if f.cfo_cr is not None]
    pat_vals = [f.pat_cr for f in ordered if f.pat_cr is not None]
    cfo_5y_sum_cr = sum(cfo_vals) if cfo_vals else None
    pat_5y_sum_cr = sum(pat_vals) if pat_vals else None

    cfo_to_pat: float | None = None
    if cfo_5y_sum_cr is not None and pat_5y_sum_cr is not None and pat_5y_sum_cr > 0:
        cfo_to_pat = cfo_5y_sum_cr / pat_5y_sum_cr
    elif yr.get("cfo_to_pat") is not None:
        cfo_to_pat = yr.get("cfo_to_pat")

    fcf_cr = yr.get("fcf_cr")
    capex_cr = latest.capex_cr if latest is not None else None
    working_capital_cr = yr.get("working_capital_cr")
    asset_turnover = yr.get("asset_turnover")
    inventory_turnover = yr.get("inventory_turnover")
    receivable_days = yr.get("receivable_days")
    payable_days = yr.get("payable_days")

    ccc_days: float | None = None
    if receivable_days is not None and payable_days is not None:
        inv_days = (365.0 / inventory_turnover) if (inventory_turnover and inventory_turnover > 0) else 0.0
        ccc_days = receivable_days + inv_days - payable_days

    payout_ratio = yr.get("payout_ratio")
    retention_ratio = yr.get("retention_ratio")
    sgr: float | None = None
    if roe is not None and retention_ratio is not None:
        sgr = roe * retention_ratio

    # Check if dividends are increasing or growing over the 5Y window
    dps_series = [f.dps for f in ordered if f.dps is not None]
    div_paid_series = [f.dividends_paid_cr for f in ordered if f.dividends_paid_cr is not None]
    dividends_increasing: bool | None = None
    if len(dps_series) >= 2:
        non_decreasing = sum(
            1 for i in range(1, len(dps_series)) if dps_series[i] >= dps_series[i - 1] * 0.95
        )
        dividends_increasing = (
            dps_series[-1] > dps_series[0]
            and non_decreasing >= max(1, len(dps_series) - 2)
        )
    elif len(div_paid_series) >= 2:
        dividends_increasing = div_paid_series[-1] > div_paid_series[0]
    elif snapshot.dividend_yield is not None:
        dividends_increasing = snapshot.dividend_yield >= 0.005

    # PEG ratio: P/E divided by (EPS CAGR % or YoY earnings growth %)
    peg_ratio: float | None = None
    growth_pct: float | None = None
    if eps_cagr_5y is not None and eps_cagr_5y > 0:
        growth_pct = eps_cagr_5y * 100.0
    elif pat_cagr_5y is not None and pat_cagr_5y > 0:
        growth_pct = pat_cagr_5y * 100.0
    elif earnings_growth_yoy is not None and earnings_growth_yoy > 0:
        growth_pct = earnings_growth_yoy * 100.0
    if snapshot.pe is not None and snapshot.pe > 0 and growth_pct is not None and growth_pct > 0:
        peg_ratio = snapshot.pe / growth_pct

    # Bank-specific metrics from latest fiscal year
    nim_pct = latest.nim_pct if latest is not None else None
    gnpa_pct = latest.gnpa_pct if latest is not None else None
    nnpa_pct = latest.nnpa_pct if latest is not None else None
    casa_pct = latest.casa_pct if latest is not None else None
    car_pct = latest.car_pct if latest is not None else None
    credit_cost_pct = latest.credit_cost_pct if latest is not None else None
    provision_coverage_pct = latest.provision_coverage_pct if latest is not None else None
    cd_ratio = yr.get("cd_ratio")

    return StockComputedRatios(
        symbol=snapshot.symbol,
        is_bank=is_bank,
        revenue_cagr_5y=revenue_cagr_5y,
        pat_cagr_5y=pat_cagr_5y,
        eps_cagr_5y=eps_cagr_5y,
        dps_cagr_5y=dps_cagr_5y,
        nii_cagr_5y=nii_cagr_5y,
        revenue_growth_yoy=revenue_growth_yoy,
        earnings_growth_yoy=earnings_growth_yoy,
        roe=roe,
        roa=roa,
        roce=roce,
        ebitda_margin=ebitda_margin,
        pat_margin=pat_margin,
        debt_to_equity=debt_to_equity,
        interest_coverage=interest_coverage,
        current_ratio=current_ratio,
        quick_ratio=quick_ratio,
        cfo_to_pat=cfo_to_pat,
        cfo_5y_sum_cr=cfo_5y_sum_cr,
        pat_5y_sum_cr=pat_5y_sum_cr,
        fcf_cr=fcf_cr,
        capex_cr=capex_cr,
        working_capital_cr=working_capital_cr,
        asset_turnover=asset_turnover,
        inventory_turnover=inventory_turnover,
        receivable_days=receivable_days,
        payable_days=payable_days,
        cash_conversion_cycle_days=ccc_days,
        dividend_yield=snapshot.dividend_yield,
        payout_ratio=payout_ratio,
        retention_ratio=retention_ratio,
        sustainable_growth_rate=sgr,
        dividends_increasing=dividends_increasing,
        pe=snapshot.pe,
        pb=snapshot.pb,
        ps=snapshot.ps,
        ev_ebitda=snapshot.ev_ebitda,
        peg_ratio=peg_ratio,
        promoter_holding_pct=snapshot.promoter_holding_pct,
        pledge_pct=snapshot.pledge_pct,
        auditor_flags=snapshot.auditor_flags,
        insider_net_buy_lakhs=snapshot.insider_net_buy_lakhs,
        price=snapshot.price,
        market_cap_cr=snapshot.market_cap_cr,
        avg_daily_traded_value_cr=snapshot.avg_daily_traded_value_cr,
        return_1y=snapshot.return_1y,
        volatility_1y=snapshot.volatility_1y,
        max_drawdown_1y=snapshot.max_drawdown_1y,
        nim_pct=nim_pct,
        gnpa_pct=gnpa_pct,
        nnpa_pct=nnpa_pct,
        casa_pct=casa_pct,
        car_pct=car_pct,
        credit_cost_pct=credit_cost_pct,
        provision_coverage_pct=provision_coverage_pct,
        cd_ratio=cd_ratio,
    )
