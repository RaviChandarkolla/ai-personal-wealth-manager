"""Section 8C / 14B: transparent base-case projections. Assumptions are always returned."""

from __future__ import annotations

from dataclasses import dataclass, field

from ...data.stocks.provider import StockFinancialYear
from .fundamentals import opm_pct, series_cagr, sort_fy


@dataclass
class Projection:
    year_label: str
    revenue_cr: float
    opm_pct: float
    ebitda_cr: float
    pat_cr: float
    eps: float | None


@dataclass
class ProjectionSet:
    rows: list[Projection] = field(default_factory=list)
    assumptions: dict[str, float | str] = field(default_factory=dict)


def project(rows: list[StockFinancialYear], years: int = 3, mid_cycle: bool = False,
            fade: float = 0.10) -> ProjectionSet | None:
    """Revenue growth = clamp(3Y CAGR, 0, 25%) fading 10% a year; OPM = 3Y avg (all-years avg when
    mid_cycle); PAT margin = latest; EPS scales with PAT at constant share count.
    None if revenue or margins are missing."""
    r = sort_fy(rows)
    if not r or r[-1].revenue_cr is None or r[-1].pat_cr is None or r[-1].revenue_cr <= 0:
        return None
    g0 = series_cagr(r, "revenue_cr", 3)
    if g0 is None:
        g0 = series_cagr(r, "revenue_cr", len(r) - 1) if len(r) >= 3 else None
    if g0 is None:
        return None
    g = min(max(g0, 0.0), 25.0)
    opms = [m for m in (opm_pct(x) for x in r) if m is not None]
    if not opms:
        return None
    window = opms if mid_cycle else opms[-3:]
    opm = sum(window) / len(window)
    last = r[-1]
    pat_margin = last.pat_cr / last.revenue_cr
    ebitda_margin_last = opm_pct(last)
    # keep PAT margin consistent with the OPM change
    pat_margin_adj = pat_margin + ((opm - ebitda_margin_last) / 100 if ebitda_margin_last is not None else 0.0)
    pat_margin_adj = max(pat_margin_adj, 0.0)
    out = ProjectionSet(assumptions={
        "base_revenue_cagr_pct": round(g0, 2), "applied_growth_pct_year1": round(g, 2),
        "growth_fade_per_year": fade, "opm_pct": round(opm, 2),
        "opm_basis": "all-years average (mid-cycle)" if mid_cycle else "3Y average",
        "pat_margin_pct": round(pat_margin_adj * 100, 2),
    })
    rev = last.revenue_cr
    for i in range(1, years + 1):
        rev *= 1 + g / 100
        g *= 1 - fade
        pat = rev * pat_margin_adj
        eps = last.eps * pat / last.pat_cr if last.eps is not None and last.pat_cr > 0 else None
        out.rows.append(Projection(f"FY+{i}", round(rev, 2), round(opm, 2),
                                   round(rev * opm / 100, 2), round(pat, 2),
                                   None if eps is None else round(eps, 2)))
    return out
