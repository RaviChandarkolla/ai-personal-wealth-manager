"""Helpers to build Metric facts consistently."""

from __future__ import annotations

from datetime import date

from .models import Metric, MetricStatus


def computed(
    sym: str, key: str, label: str, value: float | str | None, unit: str = "",
    *, as_of: date | str | None = None, formula: str = "", inputs: list[str] | None = None,
    source: str = "derived", status: MetricStatus | None = None,
) -> Metric:
    """A derived metric; value None becomes status 'unavailable' (never a guess)."""
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of[:10]) if as_of else None
    if isinstance(value, float):
        value = round(value, 4)
    st: MetricStatus = status or ("computed" if value is not None else "unavailable")
    if value is None and st == "computed":
        st = "unavailable"
    return Metric(id=f"{sym}:{key}", label=label, value=value, unit=unit, status=st,
                  as_of=as_of, source=source, formula=formula, inputs=inputs or [])


def safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b
