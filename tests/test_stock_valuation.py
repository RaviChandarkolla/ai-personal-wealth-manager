"""Unit tests for weighted relative valuation (FA Course Notes Section 10)."""

from __future__ import annotations

import pytest
from wealth_advisor.data.stocks.provider import StockSectorMedian, StockSnapshot
from wealth_advisor.domain.stock_valuation import (
    compute_relative_valuation,
    compute_weighted_valuation_from_implied_prices,
)


def test_fa_course_notes_worked_valuation_example():
    """Verify the exact worked example from FA Course Notes Section 10:
    Implied prices: EV/EBITDA=950 (30%), P/S=980 (30%), P/E=1050 (30%), P/B=920 (10%)
    Weighted Fair Value = 0.30*950 + 0.30*980 + 0.30*1050 + 0.10*920 = 285 + 294 + 315 + 92 = 986.0
    Fair Value Range = [986.0, 1183.2]
    """
    implied = {
        "ev_ebitda": 950.0,
        "ps": 980.0,
        "pe": 1050.0,
        "pb": 920.0,
    }
    undervalued = compute_weighted_valuation_from_implied_prices(
        symbol="DEMO",
        cmp=940.0,
        implied_prices=implied,
        is_bank=False,
    )
    assert undervalued.weighted_fair_value == pytest.approx(986.0, abs=0.01)
    assert undervalued.fair_value_low == pytest.approx(986.0, abs=0.01)
    assert undervalued.fair_value_high == pytest.approx(1183.2, abs=0.01)
    assert undervalued.verdict == "undervalued"
    assert undervalued.expected_return_low_pct == pytest.approx(
        (986.0 - 940.0) / 940.0 * 100.0, abs=0.1
    )

    fair = compute_weighted_valuation_from_implied_prices(
        symbol="DEMO",
        cmp=1050.0,
        implied_prices=implied,
        is_bank=False,
    )
    assert fair.verdict == "fairly_valued"

    overvalued = compute_weighted_valuation_from_implied_prices(
        symbol="DEMO",
        cmp=1250.0,
        implied_prices=implied,
        is_bank=False,
    )
    assert overvalued.verdict == "overvalued"


def test_bank_valuation_uses_pb_and_pe_only():
    """Verify banks use 60% P/B + 40% P/E and mark EV/EBITDA and P/S as not applicable."""
    val = compute_weighted_valuation_from_implied_prices(
        symbol="HDFCBANK",
        cmp=1600.0,
        implied_prices={
            "ev_ebitda": 9999.0,
            "ps": 9999.0,
            "pe": 1800.0,
            "pb": 1700.0,
        },
        is_bank=True,
    )
    # 0.60 * 1700 + 0.40 * 1800 = 1020 + 720 = 1740
    assert val.weighted_fair_value == pytest.approx(1740.0, abs=0.01)
    assert val.verdict == "undervalued"
    by_method = {c.method: c for c in val.components}
    assert by_method["ev_ebitda"].applicable is False
    assert by_method["ps"].applicable is False
    assert by_method["pb"].applicable is True
    assert by_method["pe"].applicable is True


def test_compute_relative_valuation_renormalizes_missing_multiples():
    """When some sector medians are missing, available multiples re-normalize to 100%."""
    snapshot = StockSnapshot(
        symbol="TEST",
        as_of="2026-04-06",
        price=100.0,
        market_cap_cr=1000.0,
        pe=20.0,
        pb=4.0,
        ps=None,
        ev_ebitda=None,
    )
    sector_median = StockSectorMedian(
        sector="Information Technology",
        as_of="2026-04-06",
        peer_count=5,
        median_pe=24.0,
        median_pb=4.8,
        median_ps=None,
        median_ev_ebitda=None,
    )
    val = compute_relative_valuation(
        snapshot=snapshot,
        sector_median=sector_median,
        is_bank=False,
    )
    # Implied P/E price = 100 * (24/20) = 120; Implied P/B price = 100 * (4.8/4.0) = 120
    assert val.weighted_fair_value == pytest.approx(120.0, abs=0.1)
    assert val.verdict == "undervalued"
