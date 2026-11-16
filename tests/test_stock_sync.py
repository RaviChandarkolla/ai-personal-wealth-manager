"""Unit tests for Daily Stock Sync & Screening Service (`StockSyncService`)."""

from __future__ import annotations

import sqlite3

from wealth_advisor.data.stocks.sync import StockSyncService
from wealth_advisor.data.store import MetadataStore


def _new_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    MetadataStore(conn)
    return conn


def test_stock_sync_full_and_idempotency():
    conn = _new_conn()
    svc = StockSyncService(conn, data_mode="synthetic")

    # Initial status is idle with no synced data
    st0 = svc.get_status()
    assert st0["has_synced_data"] is False
    assert st0["synced_today"] is False

    # First full sync runs and persists snapshots, financials, medians, and screen results
    res1 = svc.sync(mode="full", force=False)
    assert res1["already_synced_today"] is False
    assert res1["synced_today"] is True
    assert res1["has_synced_data"] is True
    assert res1["total_synced"] == 22
    assert res1["passed_set1"] > 0
    assert res1["passed_set2"] > 0

    # Sector medians should be populated
    it_med = svc.get_sector_median("Information Technology")
    assert it_med is not None
    assert it_med.peer_count >= 3
    assert it_med.median_pe is not None

    bank_med = svc.get_sector_median("Financial Services")
    assert bank_med is not None
    assert bank_med.is_bank is True
    assert bank_med.median_nim_pct is not None

    # Second full sync on the same calendar day without force=True is a no-op
    res2 = svc.sync(mode="full", force=False)
    assert res2["already_synced_today"] is True
    assert res2["job_id"] == res1["job_id"]

    # Calling with force=True runs a fresh sync
    res3 = svc.sync(mode="full", force=True)
    assert res3["already_synced_today"] is False
    assert res3["job_id"] != res1["job_id"]


def test_stock_sync_rescreen_and_list_stocks():
    conn = _new_conn()
    svc = StockSyncService(conn, data_mode="synthetic")
    svc.sync(mode="full", force=True)

    # Rescreen with high market-cap floor reduces pass counts
    res_strict = svc.sync(mode="rescreen", min_market_cap_cr=1_000_000.0)
    assert res_strict["already_synced_today"] is False
    assert res_strict["passed_set2"] <= 3

    # Rescreen back to default (no market cap limit)
    res_default = svc.sync(mode="rescreen", min_market_cap_cr=None)
    assert res_default["passed_set2"] >= 8

    # List stocks with search, sector, and passed_only filters
    all_set2 = svc.list_stocks(rule_set="set2", passed_only=True, limit=50)
    assert all_set2["total"] == res_default["passed_set2"]
    assert all(item["passed"] is True for item in all_set2["items"])

    banks_only = svc.list_stocks(rule_set="set2", is_bank=True, passed_only=True)
    assert banks_only["total"] >= 2
    assert all(item["is_bank"] is True for item in banks_only["items"])

    search_tcs = svc.list_stocks(query="TCS", rule_set="set2")
    assert any(item["symbol"] == "TCS" for item in search_tcs["items"])
