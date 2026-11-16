"""Optional 360° quant veto for Advise stock picks, and report badges. Cache-only: never fetches or calls an LLM."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from typing import Any

from ..data.stocks.report_data import ReportDataStore
from ..data.stocks.sync import StockSyncService
from ..data.stocks.synthetic import SyntheticStockProvider
from ..domain.stock360.assemble import Params, ReportContext, assemble
from ..domain.stock360.mechanical import should_veto
from .stock360_jobs import Stock360Store

log = logging.getLogger(__name__)
_BAD = ("SELL", "AVOID")


def params_from(s: Any) -> Params:
    return Params(
        wacc=s.stock360_wacc, terminal_growth=s.stock360_terminal_growth, hurdle=s.stock360_buffett_hurdle,
        exit_pe_cap=s.stock360_buffett_exit_pe_cap, graham_multiplier=s.stock360_graham_multiplier,
        cash_conversion_flag=s.stock360_cash_conversion_flag, max_weight_pct=s.max_weight_per_stock,
        stcg_pct=s.tax_equity_stcg_pct, ltcg_pct=s.tax_equity_ltcg_pct,
        ltcg_exemption=s.tax_ltcg_exemption_inr, tax_rates_as_of=s.tax_rates_as_of)


def report_badges(conn: sqlite3.Connection, symbols: list[str], data_mode: str) -> dict[str, dict[str, Any] | None]:
    """Latest stored report summary per symbol (cache only)."""
    try:
        st = Stock360Store(conn)
        return {sym: st.badge(sym, data_mode) for sym in symbols}
    except sqlite3.Error:
        log.exception("stock360 badge lookup failed")
        return dict.fromkeys(symbols)


def _cached_context(conn: sqlite3.Connection, sym: str, s: Any, data_mode: str) -> ReportContext | None:
    svc = StockSyncService(conn, data_mode=data_mode, provider=SyntheticStockProvider())
    snap = svc.get_snapshot(sym)
    annual = svc.get_financials(sym)
    if snap is None or not annual:
        return None
    store = ReportDataStore(conn)
    master = svc.get_master(sym)
    peers = [p for p in (svc.get_snapshot(r["symbol"]) for r in
                         svc.get_sector_peers(master.sector if master else "", exclude_symbol=sym, limit=8)) if p]
    return ReportContext(
        symbol=sym, snapshot=snap, annual=annual, quarterly=store.get_quarterly(sym), bars=store.get_daily(sym),
        index=store.get_index("^NSEI"), actions=store.get_actions(sym), manual=store.get_manual(sym), peers=peers,
        today=date.today(), params=params_from(s))


def apply_veto(conn: sqlite3.Connection, entries: list, verdicts: list, *, data_mode: str, s: Any,
               min_signals: int) -> tuple[list, list, list[str], list[dict[str, Any]]]:
    """Drop stock candidates whose 360° verdict is SELL/AVOID. Returns (entries, verdicts, warnings, audit_rows)."""
    keep_e, keep_v, warns, rows = [], [], [], []
    vmap = {v.instrument_id: v for v in verdicts}
    store = Stock360Store(conn)
    for e in entries:
        sym = e.instrument.id
        row: dict[str, Any] = {"symbol": sym, "vetoed": False}
        drop, why = False, ""
        try:
            fresh = store.latest(sym, data_mode, ttl_hours=s.stock360_report_ttl_hours)
            rep = store.get_report(fresh["report_id"]) if fresh else None
            if fresh and rep is not None and rep.final is not None:
                act = rep.final.action
                drop, why = act in _BAD, f"360° report verdict {act}"
                row.update(source="report", report_id=fresh["report_id"], action=act)
            else:
                ctx = _cached_context(conn, sym, s, data_mode)
                if ctx is None:
                    warns.append(f"{sym}: 360° check skipped (data not cached)")
                    row.update(source="skipped", reason="data not cached")
                else:
                    mech = assemble(ctx).mech
                    drop, why = should_veto(mech, min_signals)
                    row.update(source="mechanical", action=mech.action, score=mech.score,
                               sell_signals=mech.sell_signals, evaluable=mech.evaluable_legends)
        except Exception as exc:
            log.exception("stock360 veto failed for %s", sym)
            warns.append(f"{sym}: 360° check skipped ({type(exc).__name__})")
            row.update(source="error", reason=str(exc))
        if drop:
            row.update(vetoed=True, reason=why)
            warns.append(f"{sym} dropped by 360° check: {why}")
        else:
            keep_e.append(e)
            if sym in vmap:
                keep_v.append(vmap[sym])
        rows.append(row)
    return keep_e, keep_v, warns, rows
