from datetime import date, timedelta

from wealth_advisor.domain.momentum import compute_momentum, regime_from_breadth, tilt_factor


def _series(n_days: int, f):
    end = date(2026, 1, 1)
    return [((end - timedelta(days=n_days - i)).isoformat(), f(i)) for i in range(n_days + 1)]


def test_uptrend_scores_above_downtrend():
    up = compute_momentum(_series(400, lambda i: 100 + i * 0.3))
    down = compute_momentum(_series(400, lambda i: 200 - i * 0.3))
    assert up and down
    assert up.score > 60 > 40 > down.score
    assert up.above_200dma is True and down.above_200dma is False


def test_weekly_bars_work():
    m = compute_momentum(_series(400, lambda i: 100 + i * 0.3)[::7])
    assert m and m.above_200dma is True


def test_short_history_returns_none():
    assert compute_momentum(_series(60, lambda i: 100.0)) is None


def test_tilt_and_regime():
    assert tilt_factor(0, 0.8, 1.2) == 0.8
    assert tilt_factor(100, 0.8, 1.2) == 1.2
    assert regime_from_breadth([True] * 7 + [False] * 3, 5.0) == ("bullish", 5.0)
    assert regime_from_breadth([True] * 2 + [False] * 8, 5.0) == ("bearish", -5.0)
    assert regime_from_breadth([], 5.0) == ("unknown", 0.0)
