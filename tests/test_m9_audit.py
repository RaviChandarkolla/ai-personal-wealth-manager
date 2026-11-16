import sqlite3

import pytest
from tests.conftest import AS_OF
from tests.fakes import ScriptedLLM

from wealth_advisor.audit.log import AuditLog, ProfileStore
from wealth_advisor.audit.replay import replay
from wealth_advisor.db import open_db
from wealth_advisor.models import Goal, RiskPreference
from wealth_advisor.pipeline import advise

GOAL = Goal(amount=50_000, horizon_years=5, risk_preference=RiskPreference.CONSERVATIVE)


@pytest.fixture
def conn(demo_conn):
    dst = open_db(":memory:")
    demo_conn.backup(dst)
    return dst


def test_audit_chain_and_append_only(conn):
    res = advise(GOAL, ScriptedLLM(), conn, as_of=AS_OF)
    audit = AuditLog(conn)
    steps = [e.step for e in audit.entries(res.run_id)]
    assert steps == ["profile", "research", "analysis", "decision", "compliance"]
    assert audit.verify() == (True, None)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE audit_log SET step = 'x'")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM audit_log")
    entry = next(e for e in audit.entries(res.run_id) if e.step == "research")
    assert entry.model == "scripted" and entry.prompt_version and entry.payload["runs"]
    assert any(c["result_hash"] for r in entry.payload["runs"] for c in r.get("tool_calls", []))


def test_tampering_is_detected(conn):
    res = advise(GOAL, ScriptedLLM(), conn, as_of=AS_OF)
    conn.execute("DROP TRIGGER audit_log_no_update")
    conn.execute("UPDATE audit_log SET payload = '{}' WHERE run_id = ? AND step = 'decision'",
                 (res.run_id,))
    ok, bad = AuditLog(conn).verify()
    assert not ok and bad is not None


def test_replay_reproduces_scores_and_allocations(conn):
    res = advise(GOAL, ScriptedLLM(), conn, as_of=AS_OF)
    rep = replay(AuditLog(conn), res.run_id)
    names = [c.name for c in rep.checks]
    assert rep.ok
    assert {"risk_score", "allocation_band", "target_allocation"} <= set(names)
    assert any(n.startswith("score:") for n in names) and any(n.startswith("sizing:") for n in names)


def test_replay_detects_changed_output(conn):
    res = advise(GOAL, ScriptedLLM(), conn, as_of=AS_OF)
    conn.execute("DROP TRIGGER audit_log_no_update")
    row = conn.execute("SELECT seq, payload FROM audit_log WHERE run_id = ? AND step = 'analysis'",
                       (res.run_id,)).fetchone()
    conn.execute("UPDATE audit_log SET payload = ? WHERE seq = ?",
                 (row["payload"].replace('"score":', '"score": 1.0, "x":', 1), row["seq"]))
    assert not replay(AuditLog(conn), res.run_id).ok


def test_unknown_run_does_not_replay_ok(conn):
    assert not replay(AuditLog(conn), "nope").ok


def test_recommendation_links_to_immutable_profile_version(conn):
    res = advise(GOAL, ScriptedLLM(), conn, as_of=AS_OF)
    stored = ProfileStore(conn).get(res.decision.record.profile_id, res.decision.record.profile_version)
    assert stored == res.profile
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE profile SET body = '{}'")
