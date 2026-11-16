"""Unit tests for head-to-head stock comparison engine (`compare_stocks`)."""

from __future__ import annotations

import sqlite3

from wealth_advisor.data.stocks.sync import StockSyncService
from wealth_advisor.data.store import MetadataStore
from wealth_advisor.domain.stock_compare import compare_stocks


def _synced_service() -> StockSyncService:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    MetadataStore(conn)
    svc = StockSyncService(conn, data_mode="synthetic")
    svc.sync(mode="full", force=True)
    return svc


def test_compare_three_non_bank_it_stocks():
    svc = _synced_service()
    bundles = [
        svc.get_stock_bundle("TCS"),
        svc.get_stock_bundle("INFY"),
        svc.get_stock_bundle("WIPRO"),
    ]
    assert all(b is not None for b in bundles)

    report = compare_stocks(
        [b for b in bundles if b is not None],
        rule_set="set2",
    )
    assert report.symbols == ["TCS", "INFY", "WIPRO"]
    assert report.matrix_type == "non_bank"
    assert report.cross_sector_warning is None
    assert len(report.criteria_matrix) == 18
    assert report.overall_winner in ("TCS", "INFY", "WIPRO")
    assert len(report.normalized_price_series) > 0
    assert len(report.financial_growth_series) == 5


def test_compare_two_banks_and_mixed_warning():
    svc = _synced_service()
    hdfc = svc.get_stock_bundle("HDFCBANK")
    icici = svc.get_stock_bundle("ICICIBANK")
    tcs = svc.get_stock_bundle("TCS")
    assert hdfc is not None and icici is not None and tcs is not None

    bank_report = compare_stocks([hdfc, icici], rule_set="set2")
    assert bank_report.matrix_type == "bank"
    assert bank_report.cross_sector_warning is None
    assert len(bank_report.criteria_matrix) == 14

    mixed_report = compare_stocks([tcs, hdfc], rule_set="set2")
    assert mixed_report.matrix_type == "mixed"
    assert mixed_report.cross_sector_warning is not None
    assert "Banking" in mixed_report.cross_sector_warning or "Cross-sector" in mixed_report.cross_sector_warning


def test_compare_what_if_overrides():
    svc = _synced_service()
    tcs = svc.get_stock_bundle("TCS")
    infy = svc.get_stock_bundle("INFY")
    assert tcs is not None and infy is not None

    # Inject a high promoter pledge (>20%) on TCS via overrides_by_symbol
    report = compare_stocks(
        [tcs, infy],
        rule_set="set2",
        overrides_by_symbol={"TCS": {"pledge_pct": 35.0}},
    )
    tcs_eval = report.evaluations_set2["TCS"]
    assert tcs_eval["passed"] is False
    assert any("pledge" in rf.lower() for rf in tcs_eval["red_flags"])
