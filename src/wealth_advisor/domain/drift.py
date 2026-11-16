"""Drift detection (5/25 rule) and rebalance deltas."""

from pydantic import BaseModel

from ..config import Settings, get_settings
from ..models.enums import Bucket


class DriftItem(BaseModel):
    bucket: Bucket
    current: float
    target: float
    abs_drift: float  # percentage points, signed (current - target)
    rel_drift: float | None  # percent of target; None when target is 0
    breached: bool


def compute_drift(
    current: dict[Bucket, float], target: dict[Bucket, float], s: Settings | None = None
) -> list[DriftItem]:
    """Breach if |abs| > 5pp or |abs|/target > 25%. e.g. 60 vs 50 -> 10pp, 20% -> breached."""
    s = s or get_settings()
    items = []
    for b in sorted(set(current) | set(target), key=lambda x: x.value):
        cur, tgt = current.get(b, 0.0), target.get(b, 0.0)
        diff = cur - tgt
        rel = abs(diff) / tgt * 100 if tgt > 0 else None
        breached = abs(diff) > s.drift_abs_pp or (rel is not None and rel > s.drift_rel_pct)
        items.append(
            DriftItem(bucket=b, current=cur, target=tgt, abs_drift=diff, rel_drift=rel, breached=breached)
        )
    return items


def rebalance_plan(
    holdings_value: dict[Bucket, float], target: dict[Bucket, float], s: Settings | None = None
) -> dict[Bucket, float]:
    """Rupee deltas (+buy / -sell) bringing all buckets to target; empty if no bucket breaches.

    Example: equity 6000, debt 4000, target 50/50 -> {equity: -1000, debt: +1000}.
    """
    total = sum(holdings_value.values())
    if total <= 0:
        return {}
    current_pct = {b: v / total * 100 for b, v in holdings_value.items()}
    if not any(i.breached for i in compute_drift(current_pct, target, s)):
        return {}
    buckets = set(holdings_value) | set(target)
    return {b: total * target.get(b, 0.0) / 100 - holdings_value.get(b, 0.0) for b in buckets}
