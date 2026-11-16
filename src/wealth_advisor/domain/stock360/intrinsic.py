"""Intrinsic value methods (Section 4) and Buffett entry price (Section 12). Pure functions."""

from __future__ import annotations

from math import sqrt
from statistics import median


def graham_number(eps: float | None, bvps: float | None, multiplier: float = 22.5) -> float | None:
    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return None
    return sqrt(multiplier * eps * bvps)


def lynch_fair_values(
    eps: float | None, growth_pct: float | None, pegs: tuple[float, ...] = (1.0, 1.5, 2.0)
) -> dict[float, float] | None:
    """Fair price at PEG p = EPS x growth% / p (P/E = growth x 1/p)."""
    if eps is None or growth_pct is None or eps <= 0 or growth_pct <= 0:
        return None
    return {p: eps * growth_pct / p for p in pegs}


def project_fcf(fcf0: float, growth: float, years: int, terminal_growth: float) -> list[float]:
    """Growth fades linearly from `growth` (year 1) to `terminal_growth` (year `years`)."""
    out: list[float] = []
    cur = fcf0
    for t in range(1, years + 1):
        g = growth + (terminal_growth - growth) * (t - 1) / max(years - 1, 1)
        cur *= 1 + g
        out.append(cur)
    return out


def dcf_equity_value(
    fcf0: float,
    growth: float,
    wacc: float = 0.12,
    terminal_growth: float = 0.045,
    years: int = 5,
    net_debt: float = 0.0,
) -> float | None:
    """Equity value (same unit as fcf0) of a fading-growth DCF with Gordon terminal value."""
    if fcf0 <= 0 or wacc <= terminal_growth:
        return None
    flows = project_fcf(fcf0, growth, years, terminal_growth)
    pv = sum(f / (1 + wacc) ** t for t, f in enumerate(flows, 1))
    tv = flows[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
    pv += tv / (1 + wacc) ** years
    return pv - net_debt


def _flat_dcf_value(fcf0: float, g: float, wacc: float, tg: float, years: int) -> float:
    """Value of `years` flows growing at constant g, then terminal growth tg."""
    cur, pv = fcf0, 0.0
    for t in range(1, years + 1):
        cur *= 1 + g
        pv += cur / (1 + wacc) ** t
    return pv + cur * (1 + tg) / (wacc - tg) / (1 + wacc) ** years


def reverse_dcf_growth(
    market_cap: float,
    fcf0: float,
    net_debt: float = 0.0,
    wacc: float = 0.12,
    terminal_growth: float = 0.045,
    years: int = 10,
    lo: float = -0.5,
    hi: float = 1.0,
) -> float | None:
    """Bisection for the constant 10Y growth g that justifies today's EV. None if FCF <= 0
    or the target is outside [lo, hi]."""
    if fcf0 <= 0 or wacc <= terminal_growth:
        return None
    target = market_cap + net_debt
    f_lo, f_hi = _flat_dcf_value(fcf0, lo, wacc, terminal_growth, years), _flat_dcf_value(
        fcf0, hi, wacc, terminal_growth, years
    )
    if not (f_lo <= target <= f_hi):
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if _flat_dcf_value(fcf0, mid, wacc, terminal_growth, years) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def reverse_dcf_label(implied: float, historical_cagr: float | None) -> str:
    if historical_cagr is None or historical_cagr <= 0:
        return "very stretched" if implied > 0.15 else "realistic"
    if implied <= historical_cagr:
        return "realistic"
    return "stretched" if implied <= 1.5 * historical_cagr else "very stretched"


def composite_iv(values: dict[str, float | None]) -> float | None:
    vals = [v for v in values.values() if v is not None and v > 0]
    return median(vals) if vals else None


def premium_pct(cmp_: float, value: float | None) -> float | None:
    """CMP premium (+) / discount (-) to a value, in %."""
    if value is None or value <= 0:
        return None
    return (cmp_ / value - 1) * 100


def buffett_entry(
    eps_fy5: float | None,
    exit_pe: float | None,
    hurdle: float = 0.12,
    years: int = 5,
) -> float | None:
    """Entry price = EPS(FY+5) x exit P/E / (1+hurdle)^years."""
    if eps_fy5 is None or exit_pe is None or eps_fy5 <= 0 or exit_pe <= 0:
        return None
    return eps_fy5 * exit_pe / (1 + hurdle) ** years


def exit_pe(cap: float, sector_median_pe: float | None) -> float:
    return min(cap, sector_median_pe) if sector_median_pe and sector_median_pe > 0 else cap
