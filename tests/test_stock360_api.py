"""Phase G: 360° report API."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from tests.conftest import AS_OF
from tests.fakes import ScriptedLLM, Stock360FakeLLM

from wealth_advisor.api.main import app, get_db_conn, get_history_db_conn, set_active_mode
from wealth_advisor.bootstrap import bootstrap_demo
from wealth_advisor.config import get_settings
from wealth_advisor.data.web_research import NullWebResearcher
from wealth_advisor.db import open_db
from wealth_advisor.orchestration.stock360_jobs import Stock360JobRunner, Stock360Service


@pytest.fixture()
def client(tmp_path):
    path = tmp_path / "api.db"
    conn = open_db(path)
    bootstrap_demo(conn, AS_OF)
    sym = "TCS"
    hist = open_db(":memory:")
    app.dependency_overrides[get_db_conn] = lambda: conn
    app.dependency_overrides[get_history_db_conn] = lambda: hist
    app.state.llm = ScriptedLLM()
    svc = Stock360Service(db_path=path, data_mode="synthetic", llm_factory=lambda: Stock360FakeLLM(),
                          settings=get_settings(), researcher=NullWebResearcher())
    app.state.stock360_runner = Stock360JobRunner(path, svc, max_parallel=2, ttl_hours=24)
    set_active_mode("synthetic", persist_env=False)
    with TestClient(app) as c:
        c.sym = sym
        yield c
    app.state.stock360_runner = None
    app.dependency_overrides.clear()


def _wait(c, job_id):
    for _ in range(300):
        j = c.get(f"/api/stocks/report360/jobs/{job_id}").json()
        if j["status"] in ("completed", "failed"):
            return j
        time.sleep(0.2)
    raise AssertionError("job timeout")


def test_generate_poll_fetch_and_cache(client):
    r = client.post(f"/api/stocks/{client.sym}/report360", json={"context": "analyzer", "run_id": "RUN-X"})
    assert r.status_code == 202, r.text
    body = r.json()
    job = _wait(client, body["job_id"])
    assert job["status"] == "completed", job
    got = client.get(f"/api/stocks/report360/{body['report_id']}")
    assert got.status_code == 200 and got.json()["report"]["symbol"] == client.sym
    assert len(got.json()["report"]["sections"]) >= 18
    latest = client.get(f"/api/stocks/{client.sym}/report360/latest")
    assert latest.json()["meta"]["report_id"] == body["report_id"]
    again = client.post(f"/api/stocks/{client.sym}/report360", json={})
    assert again.status_code == 200 and again.json()["cached"] is True
    forced = client.post(f"/api/stocks/{client.sym}/report360", json={"force": True})
    assert forced.status_code == 202
    _wait(client, forced.json()["job_id"])


def test_validation_and_404s(client):
    assert client.post("/api/stocks/bad%20sym!/report360", json={}).status_code == 400
    assert client.get("/api/stocks/report360/nope").status_code == 404
    assert client.get("/api/stocks/report360/jobs/nope").status_code == 404
    assert client.get(f"/api/stocks/{client.sym}/report360/latest").status_code == 404
    bad = client.post(f"/api/stocks/{client.sym}/report360", json={"report360_inputs": {"zzz": {"value": 1, "as_of": "2026-01-01"}}})
    assert bad.status_code == 422


def test_manual_inputs_roundtrip_and_validation(client):
    url = f"/api/stocks/{client.sym}/manual-inputs"
    assert client.get(url).json()["inputs"] == {}
    ok = client.put(url, json={"inputs": {"promoter_pct": {"value": 55.5, "as_of": "2026-03-31", "source_url": "https://x.in"}}})
    assert ok.status_code == 200
    assert client.get(url).json()["inputs"]["promoter_pct"]["value"] == 55.5
    assert client.put(url, json={"inputs": {"promoter_pct": {"value": 150, "as_of": "2026-03-31"}}}).status_code == 422
    assert client.put(url, json={"inputs": {"promoter_pct": {"value": 5}}}).status_code == 422


def test_advise_rows_carry_badge_and_disclosure(client):
    r = client.post("/api/advise", json={"amount": 500000, "horizon_years": 7, "risk_preference": "moderate",
                                         "include_stocks": True, "stock_rule_set": "set2"})
    assert r.status_code == 200, r.text
    stocks = [x for x in r.json()["recommendations"] if x["instrument_type"] == "stock"]
    assert stocks and all(x["report360"] is None for x in stocks)
    sym = stocks[0]["instrument_id"]
    sub = client.post(f"/api/stocks/{sym}/report360", json={"context": "advise", "run_id": r.json()["run_id"]})
    _wait(client, sub.json()["job_id"])
    r2 = client.post("/api/advise", json={"amount": 500000, "horizon_years": 7, "risk_preference": "moderate",
                                          "include_stocks": True, "stock_rule_set": "set2"})
    row = next(x for x in r2.json()["recommendations"] if x["instrument_id"] == sym)
    assert row["report360"] and row["report360"]["report_id"] == sub.json()["report_id"]
    assert any("360" in d for d in r2.json()["disclosures"])


def test_extended_manual_inputs_validation_and_roundtrip(client):
    url = f"/api/stocks/{client.sym}/manual-inputs"
    ok = {"inputs": {
        "consensus_eps_fy1": {"value": 150, "as_of": "2025-03-31"},
        "buy_count": {"value": 20, "as_of": "2025-03-31"},
        "guidance": {"value": "Double-digit growth guided", "as_of": "2025-03-31"},
        "shareholding_history": {"value": [{"quarter": "Mar-25", "promoter_pct": 70, "fii_pct": 12}], "as_of": "2025-03-31"},
    }}
    assert client.put(url, json=ok).status_code == 200
    got = client.get(url).json()["inputs"]
    assert got["buy_count"]["value"] == 20 and got["shareholding_history"]["value"][0]["quarter"] == "Mar-25"
    for bad in (
        {"guidance": {"value": "x" * 1001, "as_of": "2025-03-31"}},
        {"shareholding_history": {"value": [{"promoter_pct": 5}], "as_of": "2025-03-31"}},
        {"shareholding_history": {"value": [{"quarter": "Q", "fii_pct": 150}], "as_of": "2025-03-31"}},
        {"buy_count": {"value": -1, "as_of": "2025-03-31"}},
    ):
        assert client.put(url, json={"inputs": bad}).status_code == 422


def test_report360_inputs_nested_in_stock_overrides_are_persisted(client):
    body = {"symbol": client.sym, "overrides": {"report360_inputs": {"target_price_mean": {"value": 4500, "as_of": "2025-03-31"}}}}
    r = client.post("/api/stocks/analyze", json=body)
    assert r.status_code == 200, r.text
    got = client.get(f"/api/stocks/{client.sym}/manual-inputs").json()["inputs"]
    assert got["target_price_mean"]["value"] == 4500
