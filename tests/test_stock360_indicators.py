import pytest

from wealth_advisor.domain.stock360 import technicals as t


def test_sma_ema():
    assert t.sma([1, 2, 3, 4], 2) == 3.5
    assert t.sma([1], 2) is None
    assert t.ema([1, 2, 3, 4, 5], 3) == pytest.approx(4.0)  # seed 2, k=.5 -> 3 -> 4


def test_rsi_extremes():
    assert t.rsi(list(range(1, 40))) == 100.0
    assert t.rsi(list(range(40, 1, -1))) == pytest.approx(0.0, abs=1e-9)
    assert t.rsi([1, 2]) is None


def test_rsi_wilder_reference():
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
              45.89, 46.03, 45.61, 46.28, 46.28]
    assert t.rsi(closes) == pytest.approx(70.46, abs=0.1)


def test_macd_uptrend_positive():
    closes = [100 + i for i in range(80)]
    line, sig, _hist = t.macd(closes)
    assert line > 0 and sig > 0
    assert t.macd(closes[:20]) is None


def test_atr_constant_range():
    h = [11.0] * 30
    low = [9.0] * 30
    c = [10.0] * 30
    assert t.atr(h, low, c) == pytest.approx(2.0)


def test_adx_trending_high_and_di():
    n = 80
    c = [100 + 2 * i for i in range(n)]
    h = [x + 1 for x in c]
    low = [x - 1 for x in c]
    a, pdi, mdi = t.adx(h, low, c)
    assert a > 50 and pdi > mdi


def test_pivots():
    p = t.floor_pivots(110, 90, 100)
    assert p["P"] == 100 and p["R1"] == 110 and p["S1"] == 90
    assert p["R2"] == 120 and p["S2"] == 80


def test_cross_state():
    down_then_up = [100 - i * 0.5 for i in range(250)] + [-25 + i * 2 for i in range(200)]
    state, bars = t.cross_state([max(x, 1) for x in down_then_up])
    assert state == "golden" and bars is not None
    assert t.cross_state([1.0] * 10) == ("none", None)


def test_up_down_volume():
    up, dn = t.up_down_volume([1, 2, 1, 2], [10, 20, 30, 40], n=3)
    assert (up, dn) == (60, 30)


def test_weinstein_stages():
    rising = [100 + i * 0.5 for i in range(300)]
    falling = [300 - i * 0.5 for i in range(300)]
    flat = [100.0] * 300
    assert t.weinstein_stage(rising) == "Stage 2"
    assert t.weinstein_stage(falling) == "Stage 4"
    assert t.weinstein_stage(flat) == "Stage 1"
    topping = [100 + i * 0.5 for i in range(250)] + [225 - i * 0.4 for i in range(50)]
    assert t.weinstein_stage(topping) == "Stage 3"
    assert t.weinstein_stage([1.0] * 10) is None
