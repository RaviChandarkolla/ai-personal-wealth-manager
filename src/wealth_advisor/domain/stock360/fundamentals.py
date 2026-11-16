"""Sections 2A-2D: financial statement ratios and forensics. Pure functions over annual rows."""

from __future__ import annotations

from ...data.stocks.provider import StockFinancialYear
from .metric import safe_div


def sort_fy(rows: list[StockFinancialYear]) -> list[StockFinancialYear]:
    return sorted(rows, key=lambda r: r.period_end)


def cagr(first: float | None, last: float | None, years: int) -> float | None:
    """Compound growth in %; None unless both ends are positive."""
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return ((last / first) ** (1 / years) - 1) * 100


def growth_pct(prev: float | None, cur: float | None) -> float | None:
    if prev is None or cur is None or prev <= 0:
        return None
    return (cur / prev - 1) * 100


def series_cagr(rows: list[StockFinancialYear], field: str, years: int = 3) -> float | None:
    r = sort_fy(rows)
    if len(r) < years + 1:
        return None
    return cagr(getattr(r[-1 - years], field), getattr(r[-1], field), years)


def opm_pct(fy: StockFinancialYear) -> float | None:
    v = safe_div(fy.ebitda_cr, fy.revenue_cr)
    return None if v is None else v * 100


def other_income_pct_pbt(fy: StockFinancialYear) -> float | None:
    v = safe_div(fy.other_income_cr, fy.pbt_cr)
    return None if v is None or (fy.pbt_cr or 0) <= 0 else v * 100


def net_debt(fy: StockFinancialYear) -> float | None:
    if fy.total_debt_cr is None or fy.cash_and_equivalents_cr is None:
        return None
    return fy.total_debt_cr - fy.cash_and_equivalents_cr


def net_debt_to_ebitda(fy: StockFinancialYear) -> float | None:
    nd = net_debt(fy)
    return None if nd is None or (fy.ebitda_cr or 0) <= 0 else nd / fy.ebitda_cr


def quick_ratio(fy: StockFinancialYear) -> float | None:
    if fy.current_assets_cr is None or fy.current_liabilities_cr in (None, 0):
        return None
    return (fy.current_assets_cr - (fy.inventory_cr or 0)) / fy.current_liabilities_cr


def receivable_days(fy: StockFinancialYear) -> float | None:
    v = safe_div(fy.receivables_cr, fy.revenue_cr)
    return None if v is None else v * 365


def inventory_days(fy: StockFinancialYear) -> float | None:
    """On COGS proxy = revenue - EBITDA (falls back to revenue)."""
    if fy.inventory_cr is None or not fy.revenue_cr:
        return None
    cogs = fy.revenue_cr - (fy.ebitda_cr or 0)
    base = cogs if cogs > 0 else fy.revenue_cr
    return fy.inventory_cr / base * 365


def payable_days(fy: StockFinancialYear) -> float | None:
    if fy.payables_cr is None or not fy.revenue_cr:
        return None
    cogs = fy.revenue_cr - (fy.ebitda_cr or 0)
    base = cogs if cogs > 0 else fy.revenue_cr
    return fy.payables_cr / base * 365


def cash_conversion_cycle(fy: StockFinancialYear) -> float | None:
    r, i, p = receivable_days(fy), inventory_days(fy), payable_days(fy)
    if r is None or p is None:
        return None
    return r + (i or 0) - p


def fcf_yield_pct(fcf_cr: float | None, market_cap_cr: float | None) -> float | None:
    v = safe_div(fcf_cr, market_cap_cr)
    return None if v is None else v * 100


def cash_conversion_ratio(rows: list[StockFinancialYear], years: int = 3) -> float | None:
    """3Y cumulative CFO / 3Y cumulative EBITDA (needs all years)."""
    r = sort_fy(rows)[-years:]
    if len(r) < years or any(x.cfo_cr is None or x.ebitda_cr is None for x in r):
        return None
    eb = sum(x.ebitda_cr for x in r)  # type: ignore[misc]
    return None if eb <= 0 else sum(x.cfo_cr for x in r) / eb  # type: ignore[misc]


def roic_pct(fy: StockFinancialYear, tax_rate: float = 0.25) -> float | None:
    """NOPAT / (debt + equity - cash), NOPAT = EBIT x (1 - effective tax)."""
    if fy.ebit_cr is None or fy.net_worth_cr is None:
        return None
    if fy.tax_cr is not None and fy.pbt_cr and fy.pbt_cr > 0:
        tax_rate = min(max(fy.tax_cr / fy.pbt_cr, 0.0), 0.5)
    ic = (fy.total_debt_cr or 0) + fy.net_worth_cr - (fy.cash_and_equivalents_cr or 0)
    return None if ic <= 0 else fy.ebit_cr * (1 - tax_rate) / ic * 100


def roe_pct(fy: StockFinancialYear) -> float | None:
    v = safe_div(fy.pat_cr, fy.net_worth_cr)
    return None if v is None or (fy.net_worth_cr or 0) <= 0 else v * 100


def roce_pct(fy: StockFinancialYear) -> float | None:
    if fy.ebit_cr is None or fy.total_assets_cr is None or fy.current_liabilities_cr is None:
        return None
    ce = fy.total_assets_cr - fy.current_liabilities_cr
    return None if ce <= 0 else fy.ebit_cr / ce * 100


def roa_pct(fy: StockFinancialYear) -> float | None:
    v = safe_div(fy.pat_cr, fy.total_assets_cr)
    return None if v is None else v * 100


def asset_turnover(fy: StockFinancialYear) -> float | None:
    return safe_div(fy.revenue_cr, fy.total_assets_cr)
