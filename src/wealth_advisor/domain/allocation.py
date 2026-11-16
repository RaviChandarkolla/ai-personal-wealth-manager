"""Allocation bands, capacity caps, and target weights (percent)."""

from ..config import Settings, get_settings
from ..models.core import AllocationBand
from ..models.enums import Bucket, RiskCategory

_BANDS: dict[RiskCategory, dict[Bucket, tuple[float, float]]] = {
    RiskCategory.CONSERVATIVE: {
        Bucket.EQUITY: (15, 30),
        Bucket.DEBT: (70, 85),
        Bucket.GOLD: (0, 10),
        Bucket.ALTERNATIVES: (0, 0),
    },
    RiskCategory.MODERATE: {
        Bucket.EQUITY: (40, 60),
        Bucket.DEBT: (30, 45),
        Bucket.GOLD: (5, 10),
        Bucket.ALTERNATIVES: (0, 5),
    },
    RiskCategory.AGGRESSIVE: {
        Bucket.EQUITY: (70, 85),
        Bucket.DEBT: (5, 20),
        Bucket.GOLD: (5, 10),
        Bucket.ALTERNATIVES: (0, 10),
    },
}


def equity_cap(horizon_years: float, emergency_months: float, s: Settings | None = None) -> float:
    """Tightest capacity cap on equity %. e.g. 2y horizon -> 20; 5y, 1 month fund -> 30."""
    s = s or get_settings()
    caps = [100.0]
    if horizon_years < 1:
        caps.append(s.equity_cap_horizon_lt_1y)
    elif horizon_years < 3:
        caps.append(s.equity_cap_horizon_lt_3y)
    elif horizon_years < 5 and s.equity_cap_horizon_lt_5y is not None:
        caps.append(s.equity_cap_horizon_lt_5y)
    if emergency_months < s.low_emergency_months:
        caps.append(s.equity_cap_low_emergency)
    return min(caps)


def allocation_band(
    category: RiskCategory,
    horizon_years: float,
    emergency_months: float,
    s: Settings | None = None,
) -> AllocationBand:
    """Category band with equity clamped by capacity (capacity overrides tolerance)."""
    s = s or get_settings()
    prof_key = category.value
    if s.allocation_bands and prof_key in s.allocation_bands:
        raw = s.allocation_bands[prof_key]
        ranges = {
            Bucket.EQUITY: raw.get("equity", (0.0, 0.0)),
            Bucket.DEBT: raw.get("debt", (0.0, 0.0)),
            Bucket.GOLD: raw.get("gold", (0.0, 0.0)),
            Bucket.ALTERNATIVES: raw.get("alternatives", (0.0, 0.0)),
        }
    else:
        ranges = dict(_BANDS[category])

    cap = equity_cap(horizon_years, emergency_months, s)
    lo, hi = ranges[Bucket.EQUITY]
    ranges[Bucket.EQUITY] = (min(lo, cap), min(hi, cap))
    return AllocationBand(ranges=ranges)


def target_allocation(band: AllocationBand) -> dict[Bucket, float]:
    """Midpoint for equity/gold/alternatives; debt takes the remainder so weights sum to 100.

    Example: Conservative -> equity 22.5, gold 5, alt 0, debt 72.5.
    """
    mid = {b: (lo + hi) / 2 for b, (lo, hi) in band.ranges.items()}
    mid[Bucket.DEBT] = 100.0 - mid[Bucket.EQUITY] - mid[Bucket.GOLD] - mid[Bucket.ALTERNATIVES]
    return mid
