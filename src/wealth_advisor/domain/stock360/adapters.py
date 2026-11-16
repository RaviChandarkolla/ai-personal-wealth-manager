"""Sector adapters: general / bfsi / cyclical, cycle position and normalized EPS."""

from __future__ import annotations

from typing import Literal

from ...data.stocks.provider import StockFinancialYear
from .fundamentals import opm_pct, sort_fy

CYCLICAL_SECTORS = {
    "automobile", "automobile and auto components", "metals & mining", "metals", "oil gas & consumable fuels",
    "chemicals", "construction materials", "cement", "capital goods", "realty", "textiles",
    "commodities", "power",
}
BFSI_METRICS = ["nim_pct", "gnpa_pct", "nnpa_pct", "credit_cost_pct", "casa_pct",
                "provision_coverage_pct", "cost_to_income_pct", "roa", "p_abv"]
NON_BANK_ONLY = ["ebitda", "current_ratio", "debt_to_equity", "fcf"]

Adapter = Literal["general", "bfsi", "cyclical"]


def pick_adapter(sector: str, industry: str = "", is_bank: bool = False) -> Adapter:
    if is_bank:
        return "bfsi"
    text = f"{sector} {industry}".lower()
    if any(k in text for k in ("bank", "nbfc", "financial services", "insurance")):
        return "bfsi"
    if sector.strip().lower() in CYCLICAL_SECTORS or any(
        k in text for k in ("auto", "steel", "metal", "cement", "commodity", "commercial vehicle")
    ):
        return "cyclical"
    return "general"


def cycle_position(rows: list[StockFinancialYear]) -> tuple[str, float | None]:
    """Latest OPM percentile within available years: Peak >= 75th, Trough <= 25th, else Mid.
    Needs >= 4 years, otherwise 'Insufficient history'."""
    opms = [m for m in (opm_pct(r) for r in sort_fy(rows)) if m is not None]
    if len(opms) < 4:
        return "Insufficient history", None
    latest = opms[-1]
    pct = sum(1 for m in opms if m <= latest) / len(opms) * 100
    label = "Peak" if pct >= 75 else "Trough" if pct <= 25 else "Mid"
    return label, round(pct, 1)


def normalized_eps(rows: list[StockFinancialYear]) -> float | None:
    eps = [r.eps for r in sort_fy(rows) if r.eps is not None]
    return sum(eps) / len(eps) if len(eps) >= 3 else None
