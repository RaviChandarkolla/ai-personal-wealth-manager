import pytest

from wealth_advisor.domain.stock360 import intrinsic as iv


def test_graham_number():
    assert iv.graham_number(10, 100) == pytest.approx(150.0)
    assert iv.graham_number(-1, 100) is None
    assert iv.graham_number(10, 0) is None


def test_lynch_fair_values():
    r = iv.lynch_fair_values(20, 15)
    assert r == {1.0: 300.0, 1.5: 200.0, 2.0: 150.0}
    assert iv.lynch_fair_values(-2, 15) is None


def test_reverse_dcf_round_trips_forward_dcf():
    fcf, wacc, tg = 100.0, 0.12, 0.045
    mcap = iv._flat_dcf_value(fcf, 0.14, wacc, tg, 10)
    g = iv.reverse_dcf_growth(mcap, fcf, net_debt=0, wacc=wacc, terminal_growth=tg)
    assert g == pytest.approx(0.14, abs=1e-6)


def test_reverse_dcf_none_for_negative_fcf_or_out_of_range():
    assert iv.reverse_dcf_growth(1000, -5) is None
    assert iv.reverse_dcf_growth(1e15, 1.0) is None


def test_dcf_terminal_growth_monotonic():
    a = iv.dcf_equity_value(100, 0.10, terminal_growth=0.045)
    b = iv.dcf_equity_value(100, 0.10, terminal_growth=0.05)
    assert a and b and b > a
    assert iv.dcf_equity_value(100, 0.1, net_debt=50) == pytest.approx(a - 50)
    assert iv.dcf_equity_value(0, 0.1) is None


def test_reverse_dcf_label_and_composite():
    assert iv.reverse_dcf_label(0.10, 0.12) == "realistic"
    assert iv.reverse_dcf_label(0.15, 0.12) == "stretched"
    assert iv.reverse_dcf_label(0.25, 0.12) == "very stretched"
    assert iv.composite_iv({"a": 100, "b": 200, "c": None, "d": 300}) == 200
    assert iv.composite_iv({"a": None}) is None
    assert iv.premium_pct(150, 100) == pytest.approx(50)


def test_buffett_entry():
    assert iv.buffett_entry(100, 20) == pytest.approx(2000 / 1.12**5)
    assert iv.exit_pe(20, 15) == 15
    assert iv.exit_pe(20, None) == 20
