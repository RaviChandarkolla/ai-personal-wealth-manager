"""Phase E: 360° report store, service and job runner."""

from __future__ import annotations

import threading

import pytest
from tests.fakes import Stock360FakeLLM

from wealth_advisor.config import get_settings
from wealth_advisor.data.web_research import NullWebResearcher
from wealth_advisor.db import open_db
from wealth_advisor.orchestration.stock360_jobs import (
    JobConflictError,
    ParallelLimitError,
    ReportRequest,
    Stock360JobRunner,
    Stock360Service,
    Stock360Store,
)


@pytest.fixture()
def env(tmp_path):
    from datetime import date

    from tests.conftest import AS_OF

    from wealth_advisor.bootstrap import bootstrap_demo

    path = tmp_path / "t.db"
    conn = open_db(path)
    bootstrap_demo(conn, AS_OF if isinstance(AS_OF, date) else date.fromisoformat(str(AS_OF)))
    row = conn.execute("SELECT symbol FROM stock_master LIMIT 1").fetchone()
    conn.close()
    return path, (row["symbol"] if row else "TCS")


def _runner(path, llm_factory=None, max_parallel=2):
    svc = Stock360Service(db_path=path, data_mode="synthetic", llm_factory=llm_factory or (lambda: Stock360FakeLLM()),
                          settings=get_settings(), researcher=NullWebResearcher())
    return Stock360JobRunner(path, svc, max_parallel=max_parallel, ttl_hours=24)


def _store(path):
    return Stock360Store(open_db(path))


def test_job_completes_and_report_retrievable(env):
    path, sym = env
    r = _runner(path)
    sub = r.submit(ReportRequest(sym, run_id="run1", source_context="analyzer"))
    assert sub.kind == "job"
    r.wait(sub.job_id, 120)
    st = _store(path)
    job = st.get_job(sub.job_id)
    assert job["status"] == "completed", job
    rep = st.get_report(sub.report_id)
    assert rep is not None and rep.symbol == sym
    assert st.badge(sym, "synthetic")["report_id"] == sub.report_id
    assert [m["report_id"] for m in st.for_run("run1")] == [sub.report_id]
    audit = open_db(path).execute("SELECT COUNT(*) c FROM audit_log WHERE agent='stock_360'").fetchone()["c"]
    assert audit == 1


def test_cached_then_force(env):
    path, sym = env
    r = _runner(path)
    s1 = r.submit(ReportRequest(sym))
    r.wait(s1.job_id, 120)
    s2 = r.submit(ReportRequest(sym))
    assert s2.kind == "cached" and s2.report_id == s1.report_id
    s3 = r.submit(ReportRequest(sym, force=True))
    assert s3.kind == "job" and s3.report_id != s1.report_id
    r.wait(s3.job_id, 120)


def test_same_symbol_dedupes_and_conflicts(env):
    path, sym = env
    gate = threading.Event()

    def slow():
        gate.wait(30)
        return Stock360FakeLLM()

    r = _runner(path, slow)
    a = r.submit(ReportRequest(sym))
    b = r.submit(ReportRequest(sym))
    assert a.job_id == b.job_id
    with pytest.raises(JobConflictError):
        r.submit(ReportRequest(sym, horizon_years=7))
    gate.set()
    r.wait(a.job_id, 120)


def test_parallel_limit(env):
    path, sym = env
    gate = threading.Event()

    def slow():
        gate.wait(30)
        return Stock360FakeLLM()

    r = _runner(path, slow, max_parallel=1)
    a = r.submit(ReportRequest(sym))
    with pytest.raises(ParallelLimitError):
        r.submit(ReportRequest("OTHERSYM"))
    gate.set()
    r.wait(a.job_id, 120)


def test_failure_recorded(env):
    path, _ = env
    r = _runner(path)
    sub = r.submit(ReportRequest("NOSUCHSTOCKXYZ"))
    r.wait(sub.job_id, 60)
    st = _store(path)
    job = st.get_job(sub.job_id)
    assert job["status"] == "failed" and job["error"]
    assert st.meta(sub.report_id)["status"] == "failed"


def test_restart_marks_interrupted(env):
    path, sym = env
    st = _store(path)
    st.create_job("j1", "r1", sym, "h")
    st.update_job("j1", status="running")
    _runner(path)
    job = _store(path).get_job("j1")
    assert job["status"] == "failed" and "restart" in job["error"]
