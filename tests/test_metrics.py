from datetime import date, timedelta

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from wealth_advisor.domain.metrics import (
    calendar_returns,
    rolling_consistency,
    tracking_error,
)


def _to_series(pairs: list[tuple[date, float]]) -> pd.Series:
    idx = pd.DatetimeIndex([d.isoformat() for d, _ in pairs])
    return pd.Series([float(v) for _, v in pairs], index=idx, dtype=float)


def test_tracking_error_against_self_is_zero():
    start = date(2024, 1, 1)
    s = _to_series([(start + timedelta(days=i), 100.0 * (1.0005**i)) for i in range(60)])
    te = tracking_error(s, s)
    assert te == 0.0


def test_tracking_error_positive_on_divergent_series():
    start = date(2024, 1, 1)
    bench = _to_series([(start + timedelta(days=i), 100.0 + i * 0.2) for i in range(60)])
    nav = _to_series(
        [
            (start + timedelta(days=i), 100.0 + i * 0.2 + (0.5 if i % 2 == 0 else -0.5))
            for i in range(60)
        ]
    )
    te = tracking_error(nav, bench)
    assert te > 0.0


def test_calendar_returns_computes_full_year_returns():
    s = _to_series(
        [
            (date(2023, 1, 2), 100.0),
            (date(2023, 6, 15), 106.0),
            (date(2023, 12, 29), 112.0),  # +12.0% -> 0.12
            (date(2024, 1, 1), 112.0),
            (date(2024, 7, 10), 105.0),
            (date(2024, 12, 30), 100.8),  # -10.0% -> -0.10
            (date(2025, 1, 2), 100.8),    # single observation -> skipped
        ]
    )
    cal = calendar_returns(s)
    assert set(cal.keys()) == {2023, 2024}
    assert abs(cal[2023] - 0.12) < 1e-4
    assert abs(cal[2024] - (-0.10)) < 1e-4


def test_rolling_consistency_monotone_increasing():
    start = date(2020, 1, 1)
    s = _to_series([(start + timedelta(days=i * 7), 100.0 * (1.003**i)) for i in range(260)])
    rc = rolling_consistency(s, window_years=3, risk_free_rate=0.065)
    assert rc == 1.0


@settings(max_examples=25, deadline=None)
@given(
    daily_steps=st.lists(
        st.floats(min_value=-0.02, max_value=0.03, allow_nan=False, allow_infinity=False),
        min_size=90,
        max_size=140,
    )
)
def test_rolling_consistency_hypothesis_bounds(daily_steps: list[float]):
    start = date(2021, 1, 1)
    val = 100.0
    pairs: list[tuple[date, float]] = [(start, val)]
    for idx, step in enumerate(daily_steps, start=1):
        val = max(1.0, val * (1.0 + step))
        pairs.append((start + timedelta(days=idx * 7), val))

    rc = rolling_consistency(_to_series(pairs), window_years=1)
    assert 0.0 <= rc <= 1.0
