from datetime import date

from fastapi.testclient import TestClient

from wealth_advisor.api import main as api
from wealth_advisor.data.store import FundRecord, MetadataStore
from wealth_advisor.db import open_db
from wealth_advisor.models import Bucket


def test_funds_api_serializes_store_date_as_iso_string():
    def override_db():
        conn = open_db(":memory:")
        MetadataStore(conn).add_funds([
            FundRecord(
                code="TEST001",
                name="Test Fund",
                category="Liquid",
                bucket=Bucket.DEBT,
                source="test",
                as_of=date(2026, 10, 14),
            )
        ])
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db_conn] = override_db
    try:
        response = TestClient(api.app).get(
            "/api/funds", headers={"Origin": "http://localhost:5173"}
        )
    finally:
        api.app.dependency_overrides.pop(api.get_db_conn, None)

    assert response.status_code == 200
    assert response.json()[0]["as_of"] == "2026-10-14"


def test_advice_provider_error_returns_cors_compatible_502(monkeypatch):
    def fail_get_llm():
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(api, "get_llm", fail_get_llm)
    response = TestClient(api.app).post(
        "/api/advise",
        json={"query": "hi", "assume_yes": True},
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 502
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "Check backend logs" in response.json()["detail"]


def test_data_mode_endpoints_report_and_switch_mode(monkeypatch, tmp_path):
    from wealth_advisor.config import Settings

    syn_db = tmp_path / "syn.db"
    real_db = tmp_path / "real.db"
    fake_settings = Settings(
        data_mode="synthetic",
        db_path_synthetic=syn_db,
        db_path_real=real_db,
    )
    monkeypatch.setattr(api, "get_settings", lambda: fake_settings)
    api.set_active_mode("synthetic", persist_env=False)

    client = TestClient(api.app)
    r1 = client.get("/api/config/data-mode")
    assert r1.status_code == 200
    assert r1.json()["data_mode"] == "synthetic"

    r2 = client.post(
        "/api/config/data-mode",
        json={"data_mode": "synthetic", "auto_seed": True, "persist_env": False},
    )
    assert r2.status_code == 200
    assert r2.json()["fund_count"] == 45
    assert r2.json()["latest_nav_date"] is not None


def test_rule_set_endpoints_get_and_post():
    client = TestClient(api.app)
    # Reset to default set1
    api.set_active_rule_set("set1")

    # 1. GET /api/config/rule-set
    r1 = client.get("/api/config/rule-set")
    assert r1.status_code == 200
    data = r1.json()
    assert data["active"] == "set1"
    assert data["default"] == "set1"
    assert len(data["available"]) == 3
    ids = [item["id"] for item in data["available"]]
    assert ids == ["set1", "set2", "combined"]

    # 2. POST /api/config/rule-set
    r2 = client.post("/api/config/rule-set", json={"rule_set": "set2", "persist_env": False})
    assert r2.status_code == 200
    assert r2.json()["active"] == "set2"
    assert api.get_default_rule_set() == "set2"

    # Reset back to set1
    api.set_active_rule_set("set1")


def test_rules_catalog_and_compare_endpoints():
    client = TestClient(api.app)

    # 1. GET /api/rules for each set
    for r_set in ("set1", "set2", "combined"):
        res = client.get(f"/api/rules?rule_set={r_set}")
        assert res.status_code == 200
        body = res.json()
        assert body["rule_set"] == r_set
        assert "groups" in body
        assert len(body["groups"]) >= 5
        if r_set == "combined":
            assert "provenance" in body and len(body["provenance"]) > 0

    # 2. GET /api/rules/compare
    res_cmp = client.get("/api/rules/compare")
    assert res_cmp.status_code == 200
    rows = res_cmp.json()
    assert len(rows) >= 20
    assert "strategy" in rows[0]
    assert "winner" in rows[0]


def test_advise_with_rule_set_override(monkeypatch, tmp_path):
    from tests.fakes import ScriptedLLM
    from wealth_advisor.bootstrap import bootstrap_demo
    from wealth_advisor.config import Settings

    db_path = tmp_path / "test_syn.db"
    conn = open_db(db_path)
    bootstrap_demo(conn)
    conn.close()

    fake_settings = Settings(
        data_mode="synthetic",
        db_path_synthetic=db_path,
        db_path_real=db_path,
        fund_rule_set="set1",
    )
    monkeypatch.setattr(api, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(api, "get_llm", lambda: ScriptedLLM())
    api.set_active_rule_set("set1")

    client = TestClient(api.app)

    # Advise with rule_set="combined"
    req_payload = {
        "query": "I want to invest 10 lakhs for 10 years, aggressive growth",
        "assume_yes": True,
        "data_mode": "synthetic",
        "rule_set": "combined",
    }
    r = client.post("/api/advise", json=req_payload)
    assert r.status_code == 200
    resp = r.json()
    assert resp["rule_set"] == "combined"
    assert resp["provenance"] is not None
    assert len(resp["provenance"]) > 0

    # Verify that the global default setting was NOT mutated by the request override
    assert api.get_default_rule_set() == "set1"
    assert api.active_rule_set() == "set1"


def test_advise_structured_input_skips_profiling_llm_call(monkeypatch, tmp_path):
    from tests.fakes import ScriptedLLM
    from wealth_advisor.bootstrap import bootstrap_demo
    from wealth_advisor.config import Settings

    db_path = tmp_path / "test_syn_struct.db"
    conn = open_db(db_path)
    bootstrap_demo(conn)
    conn.close()

    fake_settings = Settings(
        data_mode="synthetic",
        db_path_synthetic=db_path,
        db_path_real=db_path,
    )
    fake_llm = ScriptedLLM()
    monkeypatch.setattr(api, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(api, "get_llm", lambda: fake_llm)

    client = TestClient(api.app)
    r = client.post(
        "/api/advise",
        json={
            "amount": 1500000,
            "horizon_years": 7,
            "risk_preference": "moderate",
            "purpose": "retirement corpus",
            "excluded_categories": ["Small Cap"],
            "emergency_months": 9.0,
            "savings_rate": 0.30,
            "income_stability": "stable",
            "assume_yes": True,
            "data_mode": "synthetic",
        },
    )
    assert r.status_code == 200
    resp = r.json()
    assert resp["input_mode"] == "structured"
    assert resp["goal"]["amount"] == 1500000.0
    assert resp["goal"]["horizon_years"] == 7.0
    assert resp["goal"]["risk_preference"] == "moderate"
    assert resp["goal"]["purpose"] == "retirement corpus"
    assert resp["goal"]["excluded_categories"] == ["Small Cap"]
    assert resp["assumptions"]["emergency_months"] == 9.0
    assert resp["assumptions"]["savings_rate"] == 0.30
    # Structured mode skips ExtractedGoal LLM call (1 Selection + 5 AnalystProse = 6 calls)
    assert fake_llm.calls == 6


def test_advise_hybrid_input_merges_structured_and_query(monkeypatch, tmp_path):
    from tests.fakes import ScriptedLLM
    from wealth_advisor.bootstrap import bootstrap_demo
    from wealth_advisor.config import Settings

    db_path = tmp_path / "test_syn_hybrid.db"
    conn = open_db(db_path)
    bootstrap_demo(conn)
    conn.close()

    fake_settings = Settings(
        data_mode="synthetic",
        db_path_synthetic=db_path,
        db_path_real=db_path,
    )
    fake_llm = ScriptedLLM()
    monkeypatch.setattr(api, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(api, "get_llm", lambda: fake_llm)

    client = TestClient(api.app)
    r = client.post(
        "/api/advise",
        json={
            "query": "Saving for wealth creation, avoid small caps",
            "amount": 500000,
            "horizon_years": 5,
            "risk_preference": "conservative",
            "excluded_categories": ["Mid Cap"],
            "assume_yes": True,
            "data_mode": "synthetic",
        },
    )
    assert r.status_code == 200
    resp = r.json()
    assert resp["input_mode"] == "hybrid"
    # Structured values take precedence over ScriptedLLM's 10L / 10y / aggressive defaults
    assert resp["goal"]["amount"] == 500000.0
    assert resp["goal"]["horizon_years"] == 5.0
    assert resp["goal"]["risk_preference"] == "conservative"
    # Purpose comes from LLM extraction on query
    assert resp["goal"]["purpose"] == "wealth creation"
    assert "Mid Cap" in resp["goal"]["excluded_categories"]
    # Hybrid mode runs ExtractedGoal (1) + Selection (1) + AnalystProse (5) = 7 calls
    assert fake_llm.calls == 7


def test_advise_hybrid_guardrail_rejects_out_of_scope(monkeypatch):
    from tests.fakes import ScriptedLLM

    monkeypatch.setattr(api, "get_llm", lambda: ScriptedLLM())
    client = TestClient(api.app)
    r = client.post(
        "/api/advise",
        json={
            "query": "Use 5x margin and options trading for quick gains",
            "amount": 500000,
            "horizon_years": 5,
            "risk_preference": "aggressive",
            "assume_yes": True,
        },
    )
    assert r.status_code == 400


def test_compare_schemes_and_compare_endpoints_synthetic(monkeypatch, tmp_path):
    from wealth_advisor.bootstrap import bootstrap_demo
    from wealth_advisor.config import Settings

    db_path = tmp_path / "test_compare_syn.db"
    conn = open_db(db_path)
    bootstrap_demo(conn)
    conn.close()

    fake_settings = Settings(
        data_mode="synthetic",
        db_path_synthetic=db_path,
        db_path_real=db_path,
    )
    monkeypatch.setattr(api, "get_settings", lambda: fake_settings)
    api.set_active_mode("synthetic", persist_env=False)

    client = TestClient(api.app)

    # 1. GET /api/compare/schemes
    r_sch = client.get("/api/compare/schemes?q=DEMO&data_mode=synthetic")
    assert r_sch.status_code == 200
    schemes = r_sch.json()
    assert len(schemes) >= 2
    code_a = schemes[0]["code"]
    code_b = schemes[1]["code"]

    # 2. GET /api/compare/categories
    r_cat = client.get("/api/compare/categories?data_mode=synthetic")
    assert r_cat.status_code == 200
    assert len(r_cat.json()) > 0

    # 3. Validation: 1 code or identical codes -> 422
    r_one = client.post("/api/compare", json={"codes": [code_a], "narrative": False})
    assert r_one.status_code == 422

    r_same = client.post("/api/compare", json={"codes": [code_a, code_a], "narrative": False})
    assert r_same.status_code == 422

    # 4. narrative=False makes 0 LLM calls
    class CountingFakeLLM:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, schema=None):
            self.calls += 1
            return "Should not be called"

    counter_llm = CountingFakeLLM()
    monkeypatch.setattr(api, "get_llm", lambda: counter_llm)

    r_cmp = client.post(
        "/api/compare",
        json={"codes": [code_a, code_b], "narrative": False, "data_mode": "synthetic"},
    )
    assert r_cmp.status_code == 200
    assert counter_llm.calls == 0
    body = r_cmp.json()
    assert body["narrative"] == ""
    assert body["narrative_mode"] == "disabled"
    assert set(body["ranking"]) == {code_a, code_b}
    assert len(body["criteria"]) == 18

    # 5. Invented number in LLM narrative falls back to deterministic template verdict
    class HallucinatingLLM:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, schema=None):
            self.calls += 1
            return f"Fund {code_a} returned 99999.99% with guaranteed profits [{code_a}:cagr_5y]."

    bad_llm = HallucinatingLLM()
    monkeypatch.setattr(api, "get_llm", lambda: bad_llm)

    r_nar = client.post(
        "/api/compare",
        json={"codes": [code_a, code_b], "narrative": True, "data_mode": "synthetic"},
    )
    assert r_nar.status_code == 200
    # Initial call + 1 retry = 2 calls, then falls back to template verdict
    assert bad_llm.calls == 2
    nar_body = r_nar.json()
    nar_text = nar_body["narrative"]
    assert nar_body["narrative_mode"] == "template"
    assert "99999.99" not in nar_text
    assert f"[{code_a}:compare_score]" in nar_text


def test_fund_detail_includes_profile_checklist_and_refresh_endpoints(monkeypatch, tmp_path):
    import shutil
    from wealth_advisor.bootstrap import bootstrap_demo
    from wealth_advisor.config import Settings
    from wealth_advisor.data.fund_refresh import FundRefreshReport
    from wealth_advisor.data.store import FundRefreshRecord, SchemeMasterRecord

    syn_db = tmp_path / "test_detail_syn.db"
    real_db = tmp_path / "test_detail_real.db"
    conn = open_db(syn_db)
    bootstrap_demo(conn)
    store = MetadataStore(conn)
    demo_code = store.query_funds()[0].code
    store.log_refresh(
        FundRefreshRecord(
            code=demo_code,
            refreshed_at="2026-10-21T09:00:00Z",
            data_mode="synthetic",
            status="ok",
            sources=["mfapi"],
            nav_points_before=500,
            nav_points_after=501,
            nav_date_before=date(2026, 10, 20),
            nav_date_after=date(2026, 10, 21),
        )
    )
    # Also seed a numeric scheme 120503 so real mode lookup finds it
    store.upsert_master(
        [
            SchemeMasterRecord(
                code="120503",
                name="Axis Bluechip Fund - Direct Plan - Growth",
                amc="Axis Mutual Fund",
                category="Large Cap",
                plan="direct",
                option="growth",
                as_of=date(2026, 10, 20),
            )
        ]
    )
    conn.close()

    shutil.copy(syn_db, real_db)

    fake_settings = Settings(
        data_mode="synthetic",
        db_path_synthetic=syn_db,
        db_path_real=real_db,
    )
    monkeypatch.setattr(api, "get_settings", lambda: fake_settings)
    api.set_active_mode("synthetic", persist_env=False)

    client = TestClient(api.app)

    # 1. GET /api/funds/{code} returns profile, checklist, and last_refresh
    r_det = client.get(f"/api/funds/{demo_code}?data_mode=synthetic")
    assert r_det.status_code == 200
    det = r_det.json()
    assert "profile" in det and det["profile"] is not None
    assert "calendar_returns" in det["profile"]
    assert "checklist" in det and isinstance(det["checklist"], list) and len(det["checklist"]) >= 5
    assert "last_refresh" in det and det["last_refresh"] is not None
    assert det["last_refresh"]["status"] == "ok"

    # 2. GET /api/funds/{code}/refresh-log returns persisted refresh history list
    r_log = client.get(f"/api/funds/{demo_code}/refresh-log?limit=5")
    assert r_log.status_code == 200
    log_body = r_log.json()
    assert isinstance(log_body, list)
    assert len(log_body) == 1
    assert log_body[0]["code"] == demo_code
    assert log_body[0]["sources"] == ["mfapi"]

    # 3. POST /api/funds/{code}/refresh in synthetic mode -> 409
    r_syn_ref = client.post(f"/api/funds/{demo_code}/refresh", json={"include_ter_aum": False})
    assert r_syn_ref.status_code == 409

    # 4. Switch to real mode: DEMO_ code -> 409, non-numeric -> 422, unknown -> 404, mocked numeric -> 200
    api.set_active_mode("real", persist_env=False)

    r_demo_ref = client.post(f"/api/funds/{demo_code}/refresh", json={"include_ter_aum": False})
    assert r_demo_ref.status_code == 409

    r_bad_code = client.post("/api/funds/NOT_NUMERIC/refresh", json={"include_ter_aum": False})
    assert r_bad_code.status_code == 422

    r_not_found = client.post("/api/funds/999999/refresh", json={"include_ter_aum": False})
    assert r_not_found.status_code == 404

    def fake_refresh_fund(st, code, *, include_ter_aum=False, include_news=True, data_mode="real"):
        return FundRefreshReport(
            id=1,
            code=code,
            refreshed_at="2026-10-21T10:00:00Z",
            data_mode=data_mode,
            status="ok",
            sources=["mfapi", "amfi_ter", "google_news_rss"] if include_ter_aum else ["mfapi", "google_news_rss"],
            nav_points_before=100,
            nav_points_after=102,
            nav_date_before=date(2026, 10, 19),
            nav_date_after=date(2026, 10, 21),
            latest_nav=55.1234,
            ter_before=0.50,
            ter_after=0.48,
            aum_before=10000.0,
            aum_after=10500.0,
            news_score=60.0,
            sentiment_score=55.0,
            changed_fields=["nav_points", "nav_date", "expense_ratio", "aum_cr"],
            errors=[],
        )

    monkeypatch.setattr("wealth_advisor.data.fund_refresh.refresh_fund", fake_refresh_fund)
    r_ok = client.post("/api/funds/120503/refresh", json={"include_ter_aum": True, "include_news": True})
    assert r_ok.status_code == 200
    ok_body = r_ok.json()
    assert ok_body["code"] == "120503"
    assert ok_body["status"] == "ok"
    assert "amfi_ter" in ok_body["sources"]
    assert ok_body["nav_points_after"] == 102
    api.set_active_mode("synthetic", persist_env=False)
