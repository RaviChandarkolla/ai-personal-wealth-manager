"""Section 14: multi-timeframe targets, long-term targets, tranche plan. Pure functions."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

HORIZONS = {"1M": 21, "3M": 63, "6M": 126, "9M": 189}  # trading days
K = {"bear": -1.0, "base": 0.5, "bull": 1.5}  # multiples of ATR x sqrt(days)


@dataclass
class Target:
    horizon: str
    scenario: str
    price: float
    level: str = ""  # S/R level it was snapped to, if any


def snap(price: float, levels: dict[str, float], tol: float) -> tuple[float, str]:
    """Snap to the nearest named S/R level when within `tol`."""
    best = min(levels.items(), key=lambda kv: abs(kv[1] - price), default=None)
    if best and abs(best[1] - price) <= tol:
        return best[1], best[0]
    return price, ""


def short_term_targets(cmp_: float, atr: float | None, levels: dict[str, float]) -> list[Target]:
    """CMP + k x ATR x sqrt(days), snapped to a nearby S/R level (tolerance 25% of the move)."""
    if atr is None or atr <= 0:
        return []
    out = []
    for h, d in HORIZONS.items():
        span = atr * sqrt(d)
        for sc, k in K.items():
            raw = max(cmp_ + k * span, 0.01)
            p, name = snap(raw, levels, 0.25 * span)
            out.append(Target(h, sc, round(p, 2), name))
    return out


def long_term_targets(
    eps_by_year: dict[int, float | None], own_median_pe: float | None,
    own_min_pe: float | None, sector_median_pe: float | None,
) -> dict[int, dict[str, float | None]]:
    """Years (1,2,3,5) -> bear/base/bull = EPS x multiple.

    bear = min(own 5Y P/E, sector median); base = own 5Y median; bull = max(own median, sector x 1.1)."""
    out: dict[int, dict[str, float | None]] = {}
    for y, eps in eps_by_year.items():
        if eps is None or eps <= 0 or own_median_pe is None:
            out[y] = {"bear": None, "base": None, "bull": None}
            continue
        bear_pe = min([x for x in (own_min_pe if own_min_pe is not None else own_median_pe,
                                   sector_median_pe) if x is not None])
        bull_pe = max(own_median_pe, (sector_median_pe or 0) * 1.1)
        out[y] = {"bear": round(eps * bear_pe, 2), "base": round(eps * own_median_pe, 2),
                  "bull": round(eps * bull_pe, 2)}
    return out


def tranche_plan(
    cmp_: float, atr: float | None, supports: list[float], swing_low: float | None,
    dma200: float | None, buffett_entry: float | None, target_base: float | None,
    score: float, max_weight_pct: float,
) -> dict[str, object] | None:
    """Three tranche prices, stop, R:R and position size (never above the plan cap)."""
    if atr is None or atr <= 0:
        return None
    below = sorted([s for s in supports if s < cmp_], reverse=True)
    t1 = cmp_ if not below else below[0] if (cmp_ - below[0]) < 0.5 * atr else cmp_
    t2 = below[0] if below else cmp_ - atr
    t3 = buffett_entry if buffett_entry and buffett_entry < t2 else (below[1] if len(below) > 1 else t2 - atr)
    cands = [x for x in (swing_low, dma200) if x is not None and x < cmp_]
    stop = max(cands) if cands else cmp_ - 2 * atr  # the closer one
    stop = min(stop, cmp_ - 1.5 * atr)  # never tighter than 1.5 ATR
    out: dict[str, object] = {
        "tranches": [round(t1, 2), round(t2, 2), round(t3, 2)],
        "stop": round(stop, 2),
        "stop_pct": round((stop / cmp_ - 1) * 100, 2),
    }
    if target_base:
        for key, entry in (("rr_at_cmp", cmp_), ("rr_at_tranche1", t1)):
            risk = entry - stop
            out[key] = round((target_base - entry) / risk, 2) if risk > 0 else None
    suggested = max_weight_pct * min(max(score, 0), 100) / 100
    out["position_pct"] = round(min(suggested, max_weight_pct), 2)
    return out
