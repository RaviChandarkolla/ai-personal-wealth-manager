"""M15 tests: Event-driven Monitoring Agent, EventBus, and FastAPI M15 endpoints + middleware."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from wealth_advisor.agents.monitoring import AlertStore, MonitoringAgent
from wealth_advisor.agents.profiling import ExtractedGoal
from wealth_advisor.api.main import app, get_db_conn, get_history_db_conn
from wealth_advisor.db import connect_sqlite, ensure_schema, resolve_db_adapter
from wealth_advisor.llm.fake import FakeLLM
from wealth_advisor.models.enums import RiskPreference
from wealth_advisor.orchestration.events import (
    InMemoryEventBus,
    MarketEvent,
    StreamBrokerAdapter,
)


# ── EventBus & MonitoringAgent Unit Tests ─────────────────────────────────


def test_event_bus_and_stream_broker_adapter_pub_sub():
    bus = InMemoryEventBus()
    received: list[MarketEvent] = []
    bus.subscribe("fund_events", lambda ev: received.append(ev))

    ev = MarketEvent(
        topic="fund_events",
        event_type="rating_downgrade",
        profile_id="u1",
        instrument_id="INF179K01UT0",
        payload={"old_rating": 5, "new_rating": 2},
    )
    bus.publish(ev)
    assert len(received) == 1
    assert len(bus.history("fund_events")) == 1

    adapter = StreamBrokerAdapter("redis://localhost:6379/0")
    adapter_received: list[MarketEvent] = []
    adapter.subscribe("market_data", lambda e: adapter_received.append(e))
    adapter.publish(
        MarketEvent(
            topic="market_data",
            event_type="nav_drop",
            instrument_id="INF179K01UT0",
            payload={"drop_pct": 4.2},
        )
    )
    assert len(adapter_received) == 1


def test_m15_exit_criterion_rating_downgrade_alert_recalibration_and_cooldown_dedup():
    """Exit criterion:
    Injecting a simulated `rating_downgrade` event produces 1 deduplicated alert and an updated
    recommendation; duplicate events within the cool-down window produce 0 additional alerts.
    """
    bus = InMemoryEventBus()
    store = AlertStore(":memory:")
    agent = MonitoringAgent(bus, alert_store=store, cooldown_seconds=3600.0)

    t0 = datetime(2026, 4, 15, 10, 0, 0, tzinfo=timezone.utc)

    # First rating_downgrade event (4 -> 2 stars)
    ev1 = MarketEvent(
        topic="fund_events",
        event_type="rating_downgrade",
        profile_id="investor_m15",
        instrument_id="INF179K01UT0",
        payload={"old_rating": 4, "new_rating": 2},
        timestamp=t0,
    )
    bus.publish(ev1)

    alerts = agent.get_alerts(profile_id="investor_m15")
    assert len(alerts) == 1
    first_alert = alerts[0]
    assert first_alert.alert_type == "rating_downgrade"
    assert first_alert.severity == "critical"
    assert first_alert.recalibration_triggered is True
    assert first_alert.recalibrated_run_id is not None
    assert first_alert.recalibration_summary["recommended_action"] == "SELL"
    assert first_alert.recalibration_summary["supervisor_invoked"] is True
    assert len(agent.recalibration_runs) == 1

    # Duplicate event 5 minutes later (within 3600s cool-down window) -> 0 additional alerts
    ev2 = MarketEvent(
        topic="fund_events",
        event_type="rating_downgrade",
        profile_id="investor_m15",
        instrument_id="INF179K01UT0",
        payload={"old_rating": 4, "new_rating": 2},
        timestamp=t0 + timedelta(minutes=5),
    )
    bus.publish(ev2)

    alerts_after_dup = agent.get_alerts(profile_id="investor_m15")
    assert len(alerts_after_dup) == 1
    assert agent.suppressed_duplicates == 1
    assert alerts_after_dup[0].batch_count == 2
    assert len(agent.recalibration_runs) == 1

    # Event after cool-down window expires (2 hours later) -> emits new alert
    ev3 = MarketEvent(
        topic="fund_events",
        event_type="rating_downgrade",
        profile_id="investor_m15",
        instrument_id="INF179K01UT0",
        payload={"old_rating": 2, "new_rating": 1},
        timestamp=t0 + timedelta(hours=2),
    )
    bus.publish(ev3)
    assert len(agent.get_alerts(profile_id="investor_m15")) == 2


def test_monitoring_agent_drift_anomaly_and_regulatory_rules():
    bus = InMemoryEventBus()
    agent = MonitoringAgent(bus, cooldown_seconds=1800.0, nav_drop_threshold_pct=3.0)

    # 1. Portfolio drift breaching 5/25 rule
    bus.publish(
        MarketEvent(
            topic="portfolio_ticks",
            event_type="portfolio_drift",
            profile_id="drift_user",
            payload={
                "target_weights": {"equity": 50.0, "debt": 40.0, "gold": 10.0},
                "holdings_value": {"equity": 620_000.0, "debt": 280_000.0, "gold": 100_000.0},
            },
        )
    )
    drift_alerts = agent.get_alerts(profile_id="drift_user")
    assert len(drift_alerts) == 1
    assert drift_alerts[0].alert_type == "portfolio_drift"
    assert drift_alerts[0].recalibration_summary["recommended_action"] == "REBALANCE"
    assert "equity" in drift_alerts[0].details["rebalance_deltas_inr"]

    # 2. Abnormal NAV drop (>= 3%)
    bus.publish(
        MarketEvent(
            topic="market_data",
            event_type="nav_drop",
            profile_id="drift_user",
            instrument_id="INF846K01EW2",
            payload={"drop_pct": 4.8},
        )
    )
    # 3. Manager exit & SEBI action
    bus.publish(
        MarketEvent(
            topic="fund_events",
            event_type="manager_exit",
            profile_id="drift_user",
            instrument_id="INF846K01EW2",
            payload={"manager_name": "Shreyash Devalkar"},
        )
    )
    bus.publish(
        MarketEvent(
            topic="fund_events",
            event_type="sebi_action",
            profile_id="drift_user",
            instrument_id="INF846K01EW2",
            payload={"summary": "Front-running inspection order"},
        )
    )

    all_alerts = agent.get_alerts(profile_id="drift_user")
    alert_types = {a.alert_type for a in all_alerts}
    assert {"portfolio_drift", "nav_drop", "manager_exit", "sebi_action"} <= alert_types


def test_database_adapter_sqlite_and_postgres_resolution():
    sqlite_adapter = resolve_db_adapter(":memory:")
    assert sqlite_adapter.backend == "sqlite"

    pg_adapter = resolve_db_adapter("postgresql://wealth:secret@localhost:5432/wealthdb")
    assert pg_adapter.backend == "postgres"
    assert pg_adapter.dsn.startswith("postgresql://")


# ── FastAPI M15 Endpoints & Middleware Tests ──────────────────────────────


def _make_test_client() -> TestClient:
    conn = connect_sqlite(":memory:")
    ensure_schema(conn)
    hist_conn = connect_sqlite(":memory:")
    ensure_schema(hist_conn)

    app.state.llm = FakeLLM(
        [
            ExtractedGoal(
                amount_text="500000",
                horizon_text="5 years",
                risk_preference=RiskPreference.MODERATE,
                purpose="wealth creation",
            )
        ]
    )
    app.state.event_bus = InMemoryEventBus()
    app.state.monitoring_agent = MonitoringAgent(
        app.state.event_bus,
        alert_store=AlertStore(conn),
        cooldown_seconds=3600.0,
    )
    app.dependency_overrides[get_db_conn] = lambda: conn
    app.dependency_overrides[get_history_db_conn] = lambda: hist_conn
    return TestClient(app)


def test_fastapi_m15_endpoints_chat_upload_goals_recommendations_alerts():
    client = _make_test_client()
    try:
        # 1. /api/chat with PII redaction + X-PII-Redacted header
        chat_res = client.post(
            "/api/chat",
            json={
                "message": "My PAN is ABCDE1234F and I want to invest 500000 for 5 years moderate",
                "amount": 500000.0,
                "horizon_years": 5.0,
                "risk_preference": "moderate",
            },
        )
        assert chat_res.status_code == 200
        assert chat_res.headers.get("X-PII-Redacted") == "true"
        chat_data = chat_res.json()
        assert "ABCDE1234F" not in chat_data["redacted_message"]
        assert "pan" in chat_data["pii_redacted"]
        assert chat_data["goal"]["amount"] == 500000.0

        # 2. /api/statements/upload + /api/statements/confirmations + /api/statements/confirm
        csv_body = """Date,Narration,Withdrawal Amt.,Deposit Amt.,Closing Balance
01/01/2026,NEFT SALARY CREDIT ACME PAN ABCDE1234F,0,150000,300000
05/01/2026,MYSTERY VENDOR PAYMENT XYZ,8000,0,292000
"""
        up_res = client.post(
            "/api/statements/upload",
            json={
                "profile_id": "api_user",
                "statement_type": "bank_csv",
                "content": csv_body,
                "goal_amount": 500000.0,
                "horizon_years": 5.0,
                "risk_preference": "moderate",
            },
        )
        assert up_res.status_code == 200
        up_data = up_res.json()
        assert up_data["encrypted_at_rest"] is True
        assert up_data["profile"]["profile_id"] == "api_user"
        assert up_data["transactions_parsed"] == 2
        assert len(up_data["pending_confirmations"]) == 1

        item_id = up_data["pending_confirmations"][0]["item_id"]
        conf_list = client.get("/api/statements/confirmations", params={"profile_id": "api_user"})
        assert conf_list.status_code == 200
        assert len(conf_list.json()["pending"]) == 1

        conf_res = client.post(
            "/api/statements/confirm",
            json={"item_id": item_id, "confirmed_category": "utilities"},
        )
        assert conf_res.status_code == 200
        assert conf_res.json()["confirmed_category"] == "utilities"

        # 3. /api/goals (POST + GET)
        goal_post = client.post(
            "/api/goals",
            json={
                "profile_id": "api_user",
                "goals": [
                    {"name": "Emergency Fund", "target_amount": 300000.0, "horizon_years": 1.0, "priority": "must_have"},
                    {"name": "Retirement Corpus", "target_amount": 5000000.0, "horizon_years": 15.0, "priority": "must_have"},
                ],
                "total_investable": 1000000.0,
            },
        )
        assert goal_post.status_code == 200
        assert len(goal_post.json()["allocations"]) == 2

        goal_get = client.get("/api/goals", params={"profile_id": "api_user"})
        assert goal_get.status_code == 200
        assert len(goal_get.json()["goals"]) == 2

        # 4. /api/alerts/events + /api/alerts (with deduplication)
        ev_res1 = client.post(
            "/api/alerts/events",
            json={
                "topic": "fund_events",
                "event_type": "rating_downgrade",
                "profile_id": "api_user",
                "instrument_id": "INF179K01UT0",
                "payload": {"old_rating": 5, "new_rating": 2},
            },
        )
        assert ev_res1.status_code == 200
        assert ev_res1.json()["alerts_count"] == 1

        # Duplicate event within cool-down window
        ev_res2 = client.post(
            "/api/alerts/events",
            json={
                "topic": "fund_events",
                "event_type": "rating_downgrade",
                "profile_id": "api_user",
                "instrument_id": "INF179K01UT0",
                "payload": {"old_rating": 5, "new_rating": 2},
            },
        )
        assert ev_res2.status_code == 200
        assert ev_res2.json()["alerts_count"] == 1
        assert ev_res2.json()["suppressed_duplicates"] == 1

        alerts_get = client.get("/api/alerts", params={"profile_id": "api_user"})
        assert alerts_get.status_code == 200
        assert len(alerts_get.json()["alerts"]) == 1
    finally:
        app.state.llm = None
        app.dependency_overrides.clear()


def test_fastapi_auth_and_rate_limit_middleware(monkeypatch):
    client = _make_test_client()
    try:
        # Enable API key auth
        monkeypatch.setenv("WEALTH_API_KEY", "super-secret-m15-key")
        unauth = client.get("/api/alerts")
        assert unauth.status_code == 401

        auth_ok = client.get("/api/alerts", headers={"X-API-Key": "super-secret-m15-key"})
        assert auth_ok.status_code == 200

        bearer_ok = client.get("/api/alerts", headers={"Authorization": "Bearer super-secret-m15-key"})
        assert bearer_ok.status_code == 200

        monkeypatch.delenv("WEALTH_API_KEY", raising=False)

        # Enable strict rate limit (2 requests per minute)
        monkeypatch.setenv("WEALTH_RATE_LIMIT_PER_MIN", "2")
        app.state.rate_limit_hits = {}
        assert client.get("/api/alerts").status_code == 200
        assert client.get("/api/alerts").status_code == 200
        limited = client.get("/api/alerts")
        assert limited.status_code == 429
    finally:
        monkeypatch.delenv("WEALTH_API_KEY", raising=False)
        monkeypatch.delenv("WEALTH_RATE_LIMIT_PER_MIN", raising=False)
        app.state.rate_limit_hits = None
        app.state.llm = None
        app.dependency_overrides.clear()
