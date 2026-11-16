"""Price-trend (momentum) metrics for direct stocks."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

# Calendar-day lookbacks and blend weights for 3m / 6m / 12m returns (works for daily or weekly bars).
_LOOKBACKS = ((91, 0.40), (182, 0.35), (365, 0.25))
_DMA_DAYS = 280  # ~200 trading days
_MIN_DMA_POINTS = 30


@dataclass(frozen=True)
class Momentum:
    score: float  # 0-100
    ret_3m_pct: float | None
    ret_6m_pct: float | None
    ret_12m_pct: float | None
    above_200dma: bool | None


def compute_momentum(series: Sequence[tuple[str, float]]) -> Momentum | None:
    """Score trend from (iso_date, close) pairs; None when history spans under 6 months."""
    pts = sorted((date.fromisoformat(d[:10]), c) for d, c in series if d and c and c > 0)
    if not pts:
        return None
    dates = [d for d, _ in pts]
    last_d, last_c = pts[-1]
    if (last_d - dates[0]).days < _LOOKBACKS[1][0]:
        return None

    rets: list[float | None] = []
    for days, _ in _LOOKBACKS:
        target = last_d - timedelta(days=days)
        i = bisect_right(dates, target) - 1
        rets.append((last_c / pts[i][1] - 1.0) * 100.0 if i >= 0 else None)

    num = sum(w * max(0.0, min(100.0, 50.0 + 1.5 * r)) for (_, w), r in zip(_LOOKBACKS, rets) if r is not None)
    den = sum(w for (_, w), r in zip(_LOOKBACKS, rets) if r is not None)
    score = num / den

    window = [c for d, c in pts if (last_d - d).days <= _DMA_DAYS]
    above: bool | None = None
    if len(window) >= _MIN_DMA_POINTS and (last_d - dates[0]).days >= _DMA_DAYS:
        above = last_c >= sum(window) / len(window)
        score += 10.0 if above else -10.0

    r3, r6, r12 = (None if r is None else round(r, 1) for r in rets)
    return Momentum(round(max(0.0, min(100.0, score)), 1), r3, r6, r12, above)


def tilt_factor(momentum_score: float, lo: float, hi: float) -> float:
    """Map a 0-100 momentum score to a sizing multiplier in [lo, hi]."""
    return lo + (hi - lo) * max(0.0, min(100.0, momentum_score)) / 100.0


def regime_from_breadth(above_flags: Sequence[bool], shift_pct: float) -> tuple[str, float]:
    """Return (label, sleeve shift in pct points) from share of stocks above their 200DMA."""
    if not above_flags:
        return "unknown", 0.0
    breadth = sum(above_flags) / len(above_flags)
    if breadth >= 0.65:
        return "bullish", shift_pct
    if breadth <= 0.35:
        return "bearish", -shift_pct
    return "neutral", 0.0
