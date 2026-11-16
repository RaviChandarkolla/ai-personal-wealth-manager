from __future__ import annotations

from datetime import date
from fastapi.testclient import TestClient

from tests.fakes import ScriptedLLM
from wealth_advisor.api import main as api
from wealth_advisor.audit.log import AuditLog
from wealth_advisor.bootstrap import bootstrap_demo
from wealth_advisor.config import Settings
from wealth_advisor.db import open_db
from wealth_advisor.history.store import HistoryStore, build_run_title
from wealth_advisor.models.core import Goal
from wealth_advisor.pipeline import advise

AS_OF = date(2026, 10, 9)


def test_history_store_save_list_get_and_cascade_delete(demo_conn, tmp_path):
    history_db = tmp_path / "wealth_history_test.db"
    hconn = open_db(history_db)
    store = HistoryStore(hconn)

    llm = ScriptedLLM()
    goal1 = Goal(
        purpose="retirement",
        horizon_years=5,
        risk_preference="moderate",
        amount=500000,
    )
    goal2 = Goal(
        purpose="wealth_creation",
        horizon_years=2,
        risk_preference="conservative",
        amount=200000,
    )
    res1 = advise(goal1, llm, demo_conn, as_of=AS_OF)
    res2 = advise(goal2, llm, demo_conn, as_of=AS_OF)

    store.save(
        run_id=res1.run_id,
        title=build_run_title(res1.goal.purpose, res1.goal.amount, res1.goal.horizon_years),
        request_dict={"query": "Save 5 lakh for 5 years moderate risk", "amount": 500000},
        response_dict={
            "run_id": res1.run_id,
            "goal": res1.goal.model_dump(mode="json"),
            "profile": {"category": res1.profile.category.value, "risk_score": res1.profile.risk_score},
            "rule_set": res1.rule_set,
            "recommendations": [{"instrument_id": r.instrument_id, "name": r.name} for r in res1.decision.rows],
        },
        context_dict=res1.build_context(),
        data_mode="synthetic",
        created_at="2026-10-09T10:00:00+00:00",
    )
    store.save(
        run_id=res2.run_id,
        title=build_run_title(res2.goal.purpose, res2.goal.amount, res2.goal.horizon_years),
        request_dict={"query": "Invest 2 lakh for 2 years conservative", "amount": 200000},
        response_dict={
            "run_id": res2.run_id,
            "goal": res2.goal.model_dump(mode="json"),
            "profile": {"category": res2.profile.category.value, "risk_score": res2.profile.risk_score},
            "rule_set": res2.rule_set,
            "recommendations": [{"instrument_id": r.instrument_id, "name": r.name} for r in res2.decision.rows],
        },
        context_dict=res2.build_context(),
        data_mode="synthetic",
        created_at="2026-10-09T11:00:00+00:00",
    )

    items = store.list()
    assert len(items) == 2
    assert items[0]["run_id"] == res2.run_id
    assert items[1]["run_id"] == res1.run_id
    assert "Retirement" in items[1]["title"]

    m1 = store.add_message(res1.run_id, role="user", content="Why was Fund A chosen?")
    m2 = store.add_message(
        res1.run_id,
        role="assistant",
        content="Fund A had the highest composite score.",
        referenced_funds=[{"code": "1001", "name": "Fund A", "status": "recommended", "reasons": []}],
        sources=[{"source_id": "rec:1001", "doc_type": "recommendation", "title": "Fund A"}],
    )
    assert m2["id"] > m1["id"]

    detail = store.get(res1.run_id)
    assert detail is not None
    assert detail["run_id"] == res1.run_id
    assert detail["request"]["amount"] == 500000
    assert detail["response"]["run_id"] == res1.run_id
    assert "research_log" in detail["context"]
    assert len(detail["messages"]) == 2
    assert detail["messages"][1]["referenced_funds"][0]["code"] == "1001"
    assert detail["messages"][1]["sources"][0]["source_id"] == "rec:1001"

    audit = AuditLog(demo_conn)
    entries_before = audit.entries(res1.run_id)
    assert len(entries_before) > 0
    assert audit.verify()[0] is True

    deleted = store.delete(res1.run_id)
    assert deleted is True
    assert store.delete(res1.run_id) is False
    assert store.get(res1.run_id) is None
    assert store.messages(res1.run_id) == []

    entries_after = audit.entries(res1.run_id)
    assert len(entries_after) == len(entries_before)
    assert audit.verify()[0] is True
    hconn.close()


def test_history_api_endpoints(tmp_path, monkeypatch):
    db_path = tmp_path / "api_synthetic.db"
    conn = open_db(db_path)
    bootstrap_demo(conn)
    conn.close()

    fake_settings = Settings(
        data_mode="synthetic",
        db_path_synthetic=db_path,
        db_path_real=db_path,
        history_db_path=tmp_path / "api_history.db",
    )
    monkeypatch.setattr(api, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(api, "get_llm", lambda: ScriptedLLM())
    api.set_active_mode("synthetic", persist_env=False)

    client = TestClient(api.app)

    res = client.post(
        "/api/advise",
        json={
            "amount": 500000,
            "horizon_years": 5,
            "risk_preference": "moderate",
            "purpose": "Child education",
            "assume_yes": True,
            "data_mode": "synthetic",
        },
    )
    assert res.status_code == 200
    data = res.json()
    run_id = data["run_id"]
    assert run_id

    hist_res = client.get("/api/history")
    assert hist_res.status_code == 200
    hist_list = hist_res.json()
    assert any(item["run_id"] == run_id for item in hist_list)

    detail_res = client.get(f"/api/history/{run_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["run_id"] == run_id
    assert detail["response"]["run_id"] == run_id
    assert "research_log" in detail["context"]
    assert detail["messages"] == []

    del_res = client.delete(f"/api/history/{run_id}")
    assert del_res.status_code == 204

    assert client.get(f"/api/history/{run_id}").status_code == 404
    assert client.delete(f"/api/history/{run_id}").status_code == 404
