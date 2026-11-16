"""API endpoint tests for Stock Fundamental Analysis, Compare Stocks, Sync, and Advise."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.conftest import AS_OF
from tests.fakes import ScriptedLLM
from wealth_advisor.api.main import app, get_db_conn, get_history_db_conn, set_active_mode
from wealth_advisor.bootstrap import bootstrap_demo
from wealth_advisor.db import open_db


@pytest.fixture()
def api_client():
    conn = open_db(":memory:")
    bootstrap_demo(conn, AS_OF)
    hist_conn = open_db(":memory:")

    def _override_db():
        yield conn

    def _override_hist():
        yield hist_conn

    app.dependency_overrides[get_db_conn] = _override_db
    app.dependency_overrides[get_history_db_conn] = _override_hist
    app.state.llm = ScriptedLLM()
    set_active_mode("synthetic", persist_env=False)

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def test_stocks_sync_and_status_endpoints(api_client: TestClient):
    # Initial status before sync
    st0 = api_client.get("/api/stocks/sync/status")
    assert st0.status_code == 200
    assert st0.json()["has_synced_data"] is False

    # First sync succeeds with 202
    r1 = api_client.post("/api/stocks/sync", json={"mode": "full", "force": False})
    assert r1.status_code == 202
    data1 = r1.json()
    assert data1["synced_today"] is True
    assert data1["total_synced"] == 22
    job_id = data1["job_id"]
    assert job_id

    # Poll specific job id
    rj = api_client.get(f"/api/stocks/sync/{job_id}")
    assert rj.status_code == 200
    assert rj.json()["status"] == "completed"

    # Second sync same day without force=True returns 409 conflict
    r2 = api_client.post("/api/stocks/sync", json={"mode": "full", "force": False})
    assert r2.status_code == 409
    assert r2.json()["detail"]["status"] == "skipped_already_synced_today"

    # Force=True re-runs and returns 202
    r3 = api_client.post("/api/stocks/sync", json={"mode": "full", "force": True})
    assert r3.status_code == 202


def test_stocks_list_rules_analyze_and_compare_endpoints(api_client: TestClient):
    api_client.post("/api/stocks/sync", json={"mode": "full", "force": True})

    # List stocks
    ls = api_client.get("/api/stocks?rule_set=set2&passed_only=true")
    assert ls.status_code == 200
    ls_data = ls.json()
    assert ls_data["total"] >= 8
    assert all(item["passed"] is True for item in ls_data["items"])

    # Stock rules & compare rules
    rules_res = api_client.get("/api/stocks/rules")
    assert rules_res.status_code == 200
    assert "set1" in rules_res.json()
    assert "set2" in rules_res.json()

    cmp_rules = api_client.get("/api/stocks/rules/compare")
    assert cmp_rules.status_code == 200
    assert "dimensions" in cmp_rules.json()

    # Single stock analysis (Non-Bank: TCS)
    an_tcs = api_client.post(
        "/api/stocks/analyze",
        json={"symbol": "TCS", "stock_rule_set": "set2", "narrative": True},
    )
    assert an_tcs.status_code == 200
    tcs_body = an_tcs.json()
    assert tcs_body["stock"]["symbol"] == "TCS"
    assert tcs_body["active_screen"]["passed"] is True
    assert tcs_body["active_screen"]["evaluated_count"] == 9  # 9-pointer by default
    assert tcs_body["narrative"]

    # Single stock analysis with optional min_market_cap_cr activates 10-pointer
    an_tcs_10 = api_client.post(
        "/api/stocks/analyze",
        json={
            "symbol": "TCS",
            "stock_rule_set": "set2",
            "min_market_cap_cr": 50000.0,
            "narrative": False,
        },
    )
    assert an_tcs_10.status_code == 200
    assert an_tcs_10.json()["active_screen"]["evaluated_count"] == 10

    # Compare 3 stocks head-to-head
    cmp_res = api_client.post(
        "/api/stocks/compare",
        json={
            "symbols": ["TCS", "INFY", "WIPRO"],
            "stock_rule_set": "set2",
            "narrative": True,
        },
    )
    assert cmp_res.status_code == 200
    cmp_body = cmp_res.json()
    assert cmp_body["symbols"] == ["TCS", "INFY", "WIPRO"]
    assert cmp_body["matrix_type"] == "non_bank"
    assert len(cmp_body["criteria_matrix"]) == 18
    assert cmp_body["overall_winner"] in ("TCS", "INFY", "WIPRO")


def test_advise_with_and_without_stocks(api_client: TestClient):
    # 1. Default include_stocks=False => only mutual funds
    res_mf = api_client.post(
        "/api/advise",
        json={
            "amount": 500000,
            "horizon_years": 7,
            "risk_preference": "moderate",
            "include_stocks": False,
        },
    )
    assert res_mf.status_code == 200
    body_mf = res_mf.json()
    assert body_mf["include_stocks"] is False
    assert all(r.get("instrument_type") not in ("stock", "equity") for r in body_mf["recommendations"])

    # 2. include_stocks=True with Moderate risk & 7Y horizon => includes Direct Equity stocks
    res_stk = api_client.post(
        "/api/advise",
        json={
            "amount": 500000,
            "horizon_years": 7,
            "risk_preference": "moderate",
            "include_stocks": True,
            "stock_rule_set": "set2",
        },
    )
    assert res_stk.status_code == 200
    body_stk = res_stk.json()
    assert body_stk["include_stocks"] is True
    assert body_stk["stock_rule_set"] == "set2"
    eq_rows = [
        r
        for r in body_stk["recommendations"]
        if r.get("instrument_type") in ("stock", "equity") or r.get("category") == "Direct Equity"
    ]
    assert len(eq_rows) >= 1
    assert all(r["weight"] <= 10.0 for r in eq_rows)
