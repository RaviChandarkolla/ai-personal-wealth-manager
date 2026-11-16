"""Phase F: Advise badges and the optional 360° quant veto."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tests.conftest import AS_OF
from tests.fakes import ScriptedLLM

from wealth_advisor.models.core import Goal
from wealth_advisor.models.enums import RiskPreference
from wealth_advisor.orchestration import stock360_veto
from wealth_advisor.pipeline import advise

GOAL = Goal(amount=500_000, horizon_years=7, risk_preference=RiskPreference.MODERATE)


def _stock_ids(res):
    return [r.instrument_id for r in res.decision.rows if r.category == "Direct Equity"]


def _run(conn, **kw):
    return advise(GOAL, ScriptedLLM(), conn, as_of=AS_OF, include_stocks=True, stock_rule_set="set2", **kw)


def test_veto_off_by_default_and_identical(demo_conn, monkeypatch):
    called = []
    monkeypatch.setattr(stock360_veto, "apply_veto", lambda *a, **k: called.append(1))
    a = _run(demo_conn)
    assert not called
    assert _stock_ids(a)


def test_veto_drops_and_warns(demo_conn, monkeypatch):
    base = _run(demo_conn)
    victim = _stock_ids(base)[0]
    sym_state = {"cur": None}

    def ctx_for(conn, sym, s, mode):
        sym_state["cur"] = sym
        return object()

    monkeypatch.setattr(stock360_veto, "_cached_context", ctx_for)

    def asm(ctx):
        bad = sym_state["cur"] == victim
        return SimpleNamespace(mech=SimpleNamespace(action="SELL" if bad else "BUY", sell_signals=6 if bad else 0,
                                                    evaluable_legends=7, score=20 if bad else 70))

    monkeypatch.setattr(stock360_veto, "assemble", asm)
    res = _run(demo_conn, stock360_veto=True)
    assert victim not in _stock_ids(res)
    assert any(victim in w and "360° check" in w for w in res.stock_warnings)
    assert any(r.get("output_field") == "stock360_veto" for r in res.research_log)
    # the vetoed stock's weight is not lost: it is re-sized among the remaining equity candidates
    # (a next-ranked stock fills the slot) and the non-equity sleeve and unallocated cash are untouched
    def sleeve(r, equity):
        return sum(x.weight for x in r.decision.rows if (x.category == "Direct Equity") == equity)

    assert sum(x.weight for x in base.decision.rows if x.instrument_id == victim) > 0
    assert sleeve(res, True) == pytest.approx(sleeve(base, True), abs=0.5)
    assert sleeve(res, False) == pytest.approx(sleeve(base, False), abs=0.5)
    assert res.decision.unallocated_weight == pytest.approx(base.decision.unallocated_weight, abs=0.5)
    assert max(x.weight for x in res.decision.rows if x.category == "Direct Equity") <= 5.0 + 1e-6


def test_veto_skips_when_data_missing(demo_conn, monkeypatch):
    monkeypatch.setattr(stock360_veto, "_cached_context", lambda *a: None)
    res = _run(demo_conn, stock360_veto=True)
    assert _stock_ids(res)
    assert any("skipped" in w for w in res.stock_warnings)


def test_badges_cache_only(demo_conn):
    out = stock360_veto.report_badges(demo_conn, ["TCS", "INFY"], "synthetic")
    assert out == {"TCS": None, "INFY": None}
