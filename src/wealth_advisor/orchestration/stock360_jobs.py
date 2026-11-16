"""Storage, generation service and background jobs for 360° reports."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..audit.log import AuditLog
from ..data.stocks.report_data import ReportDataStore, ensure_report_data
from ..data.stocks.sync import StockSyncService
from ..data.web_research import WebEvidenceCache, get_researcher, research
from ..db import open_db
from ..domain.stock360.assemble import Params, ReportContext
from ..domain.stock360.models import Report360
from ..domain.stock360.template import TEMPLATE_VERSION

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_360_report (
  report_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, data_mode TEXT NOT NULL, template_version TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL, as_of TEXT, report_json TEXT NOT NULL DEFAULT '{}',
  mechanical_json TEXT NOT NULL DEFAULT '{}', llm_calls INTEGER DEFAULT 0, web_searches INTEGER DEFAULT 0,
  error TEXT DEFAULT '', run_id TEXT DEFAULT '', source_context TEXT DEFAULT '', inputs_hash TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_s360_report_sym ON stock_360_report(symbol, data_mode, created_at);
CREATE INDEX IF NOT EXISTS idx_s360_report_run ON stock_360_report(run_id);
CREATE TABLE IF NOT EXISTS stock_360_job (
  job_id TEXT PRIMARY KEY, report_id TEXT NOT NULL, symbol TEXT NOT NULL, inputs_hash TEXT DEFAULT '',
  status TEXT NOT NULL, progress INTEGER DEFAULT 0, message TEXT DEFAULT '', started_at TEXT, finished_at TEXT,
  error TEXT DEFAULT ''
);
"""


class JobConflictError(RuntimeError):
    """A job for this symbol with different inputs is already running."""


class ParallelLimitError(RuntimeError):
    """Too many reports are being generated at once."""


@dataclass
class ReportRequest:
    symbol: str
    force: bool = False
    run_id: str = ""
    source_context: str = ""  # advise | analyzer | compare | history | ""
    horizon_years: float | None = None
    risk_category: str | None = None
    holding: dict[str, Any] | None = None  # {avg_cost, buy_date, qty}

    def inputs_hash(self, data_mode: str, manual: dict[str, Any] | None = None) -> str:
        body = {"s": self.symbol.upper(), "m": data_mode, "t": TEMPLATE_VERSION, "h": self.holding,
                "hz": self.horizon_years, "rc": self.risk_category, "man": manual or {}}
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Stock360Store:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        conn.executescript(_SCHEMA)

    # ---- reports
    def save_report(self, r: Report360 | None, *, report_id: str, symbol: str, data_mode: str, status: str,
                    inputs_hash: str, run_id: str = "", source_context: str = "", error: str = "") -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO stock_360_report(report_id,symbol,data_mode,template_version,status,"
                "created_at,as_of,report_json,mechanical_json,llm_calls,web_searches,error,run_id,source_context,"
                "inputs_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (report_id, symbol.upper(), data_mode, TEMPLATE_VERSION, status,
                 (r.generated_at if r else datetime.now(UTC)).isoformat(), str(r.as_of) if r else None,
                 r.model_dump_json() if r else "{}", r.mechanical.model_dump_json() if r else "{}",
                 r.llm_calls if r else 0, r.web_searches if r else 0, error, run_id, source_context, inputs_hash))

    def get_report(self, report_id: str) -> Report360 | None:
        row = self.conn.execute("SELECT report_json,status FROM stock_360_report WHERE report_id=?", (report_id,)).fetchone()
        if not row or row[0] in (None, "", "{}"):
            return None
        return Report360.model_validate_json(row[0])

    def meta(self, report_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM stock_360_report WHERE report_id=?", (report_id,)).fetchone()
        return _meta(row) if row else None

    def latest(self, symbol: str, data_mode: str, *, inputs_hash: str | None = None,
               ttl_hours: float | None = None, now: datetime | None = None) -> dict[str, Any] | None:
        sql = ("SELECT * FROM stock_360_report WHERE symbol=? AND data_mode=? AND template_version=? "
               "AND status IN ('complete','partial')")
        args: list[Any] = [symbol.upper(), data_mode, TEMPLATE_VERSION]
        if inputs_hash:
            sql += " AND inputs_hash=?"
            args.append(inputs_hash)
        row = self.conn.execute(sql + " ORDER BY created_at DESC LIMIT 1", args).fetchone()
        if not row:
            return None
        m = _meta(row)
        if ttl_hours is not None:
            age = (now or datetime.now(UTC)) - datetime.fromisoformat(m["created_at"])
            if age > timedelta(hours=ttl_hours):
                return None
        return m

    def badge(self, symbol: str, data_mode: str) -> dict[str, Any] | None:
        """Cache-only summary for Advise rows; never generates anything."""
        m = self.latest(symbol, data_mode)
        if not m:
            return None
        rep = self.get_report(m["report_id"])
        if rep is None or rep.final is None:
            return None
        return {"report_id": m["report_id"], "status": m["status"], "rating": rep.final.stars, "stars": rep.final.stars,
                "action": rep.final.action, "generated_at": m["created_at"]}

    def for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM stock_360_report WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()
        return [_meta(r) for r in rows]

    # ---- jobs
    def create_job(self, job_id: str, report_id: str, symbol: str, inputs_hash: str) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO stock_360_job(job_id,report_id,symbol,inputs_hash,status,progress,started_at)"
                              " VALUES (?,?,?,?,?,?,?)",
                              (job_id, report_id, symbol.upper(), inputs_hash, "queued", 0, datetime.now(UTC).isoformat()))

    def update_job(self, job_id: str, **kw: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in kw)
        with self.conn:
            self.conn.execute(f"UPDATE stock_360_job SET {cols} WHERE job_id=?", (*kw.values(), job_id))

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM stock_360_job WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def active_jobs(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM stock_360_job WHERE status IN ('queued','running')").fetchall()
        return [dict(r) for r in rows]

    def mark_interrupted(self) -> int:
        now = datetime.now(UTC).isoformat()
        with self.conn:
            cur = self.conn.execute(
                "UPDATE stock_360_job SET status='failed', error='interrupted by restart', finished_at=? "
                "WHERE status IN ('queued','running')", (now,))
            self.conn.execute("UPDATE stock_360_report SET status='failed', error='interrupted by restart' "
                              "WHERE status='running'")
        return cur.rowcount


def _meta(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d.pop("report_json", None)
    d.pop("mechanical_json", None)
    return d


# ------------------------------------------------------------------ generation service
@dataclass
class Stock360Service:
    """Gathers data (on demand only), runs web research and the analyst. Used by the job runner."""

    db_path: str | Path
    data_mode: str
    llm_factory: Callable[[], Any]
    settings: Any
    provider: Any = None  # tests inject a provider
    researcher: Any = None  # tests inject a researcher
    extra: dict[str, Any] = field(default_factory=dict)

    def generate(self, req: ReportRequest, report_id: str,
                 progress: Callable[[str, int], None] | None = None) -> Report360:
        from ..agents.stock_360_analyst import Stock360Analyst
        from ..llm.base import BudgetedLLM

        s = self.settings
        sym = req.symbol.upper()
        conn = open_db(self.db_path)
        try:
            tick = progress or (lambda *_: None)
            tick("loading stock data", 5)
            svc = StockSyncService(conn, data_mode=self.data_mode, provider=self.provider,
                                   snapshot_ttl_hours=s.stock_snapshot_ttl_hours)
            bundle = svc.ensure_stock_cached(sym)
            if bundle is None:
                raise ValueError(f"Stock '{sym}' not found in cache or provider")
            tick("fetching price history and quarterly results", 15)
            store = ReportDataStore(conn)
            data_warn = ensure_report_data(store, svc.provider, sym)
            peers = [p for p in (svc.get_snapshot(r["symbol"]) for r in
                                 svc.get_sector_peers(bundle.master.sector, exclude_symbol=sym, limit=8)) if p]
            researcher = self.researcher or get_researcher(
                s.stock360_web_search_provider, s.llm_provider, s.llm_model, self.data_mode)
            tick("web research", 30)
            web = research(researcher, sym, bundle.master.name or sym, max_searches=s.stock360_max_web_searches,
                           cache=WebEvidenceCache(conn), ttl_hours=s.stock360_web_ttl_hours,
                           yahoo_cmp=bundle.snapshot.cmp or bundle.snapshot.price)
            params = Params(
                wacc=s.stock360_wacc, terminal_growth=s.stock360_terminal_growth, hurdle=s.stock360_buffett_hurdle,
                exit_pe_cap=s.stock360_buffett_exit_pe_cap, graham_multiplier=s.stock360_graham_multiplier,
                cash_conversion_flag=s.stock360_cash_conversion_flag, max_weight_pct=s.max_weight_per_stock,
                stcg_pct=s.tax_equity_stcg_pct, ltcg_pct=s.tax_equity_ltcg_pct,
                ltcg_exemption=s.tax_ltcg_exemption_inr, tax_rates_as_of=s.tax_rates_as_of)
            ctx = ReportContext(
                symbol=sym, snapshot=bundle.snapshot, annual=bundle.financials, quarterly=store.get_quarterly(sym),
                bars=store.get_daily(sym), index=store.get_index("^NSEI"), actions=store.get_actions(sym),
                manual=store.get_manual(sym), peers=peers, holding=req.holding, today=date.today(),
                params=params, data_warnings=data_warn)
            tick("writing report", 45)
            llm = BudgetedLLM(self.llm_factory(), s.stock360_max_llm_calls)
            analyst = Stock360Analyst(llm, max_llm_calls=s.stock360_max_llm_calls, run_id=req.run_id or None)
            return analyst.build(ctx, web=web, name=bundle.master.name or sym, data_mode=self.data_mode,
                                 report_id=report_id, progress=tick)
        finally:
            conn.close()


# ------------------------------------------------------------------ job runner
@dataclass
class Submit:
    kind: str  # cached | job
    report_id: str
    job_id: str = ""


class Stock360JobRunner:
    def __init__(self, db_path: str | Path, service: Stock360Service, *, max_parallel: int = 2,
                 ttl_hours: float = 24) -> None:
        self.db_path = db_path
        self.service = service
        self.max_parallel = max_parallel
        self.ttl_hours = ttl_hours
        self.pool = ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="stock360")
        self.futures: dict[str, Future[None]] = {}
        self._lock = threading.Lock()
        conn = open_db(db_path)
        try:
            Stock360Store(conn).mark_interrupted()
        finally:
            conn.close()

    def _manual(self, sym: str) -> dict[str, Any]:
        conn = open_db(self.db_path)
        try:
            return ReportDataStore(conn).get_manual(sym)
        finally:
            conn.close()

    def submit(self, req: ReportRequest) -> Submit:
        sym = req.symbol.upper()
        ihash = req.inputs_hash(self.service.data_mode, self._manual(sym))
        with self._lock:
            conn = open_db(self.db_path)
            try:
                st = Stock360Store(conn)
                active = st.active_jobs()
                for j in active:
                    if j["symbol"] == sym:
                        if j["inputs_hash"] == ihash:
                            return Submit("job", j["report_id"], j["job_id"])
                        raise JobConflictError(f"a report for {sym} with different inputs is already being generated")
                if not req.force:
                    hit = st.latest(sym, self.service.data_mode, inputs_hash=ihash, ttl_hours=self.ttl_hours)
                    if hit:
                        return Submit("cached", hit["report_id"])
                if len(active) >= self.max_parallel:
                    raise ParallelLimitError(f"{len(active)} reports already running (limit {self.max_parallel})")
                rid, jid = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
                st.create_job(jid, rid, sym, ihash)
                st.save_report(None, report_id=rid, symbol=sym, data_mode=self.service.data_mode, status="running",
                               inputs_hash=ihash, run_id=req.run_id, source_context=req.source_context)
            finally:
                conn.close()
            self.futures[jid] = self.pool.submit(self._run, req, rid, jid, ihash)
        return Submit("job", rid, jid)

    def _run(self, req: ReportRequest, rid: str, jid: str, ihash: str) -> None:
        conn = open_db(self.db_path)
        st = Stock360Store(conn)
        mode = self.service.data_mode

        def progress(msg: str, pct: int) -> None:
            st.update_job(jid, status="running", progress=min(pct, 99), message=msg)

        try:
            progress("starting", 1)
            rep = self.service.generate(req, rid, progress)
            degraded = any("mechanical only" in w or "unavailable" in w for w in rep.warnings) or rep.llm_calls == 0
            status = "partial" if degraded else "complete"
            st.save_report(rep, report_id=rid, symbol=req.symbol, data_mode=mode, status=status,
                           inputs_hash=ihash, run_id=req.run_id, source_context=req.source_context)
            st.update_job(jid, status="completed", progress=100, message=status,
                          finished_at=datetime.now(UTC).isoformat())
            AuditLog(conn).append(req.run_id or jid, "stock_360", "generate", {
                "symbol": req.symbol.upper(), "report_id": rid, "status": status, "llm_calls": rep.llm_calls,
                "web_searches": rep.web_searches, "inputs_hash": ihash, "action": rep.final.action if rep.final else None,
                "score": rep.mechanical.score})
        except Exception as exc:
            log.exception("stock360 job %s failed", jid)
            msg = f"{type(exc).__name__}: {exc}"
            st.save_report(None, report_id=rid, symbol=req.symbol, data_mode=mode, status="failed",
                           inputs_hash=ihash, run_id=req.run_id, source_context=req.source_context, error=msg)
            st.update_job(jid, status="failed", error=msg, finished_at=datetime.now(UTC).isoformat())
        finally:
            conn.close()

    def wait(self, job_id: str, timeout: float = 30.0) -> None:
        f = self.futures.get(job_id)
        if f:
            f.result(timeout=timeout)
