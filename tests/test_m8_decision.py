from dataclasses import replace

import pytest
from tests.conftest import AS_OF
from tests.fakes import ScriptedLLM

from wealth_advisor.agents.mf_researcher import MFResearchAgent, default_config
from wealth_advisor.agents.tools import ResearchTools
from wealth_advisor.config import get_settings
from wealth_advisor.data.store import MetadataStore
from wealth_advisor.domain.categories import categories_for
from wealth_advisor.guardrails.compliance import review
from wealth_advisor.guardrails.suitability import (
    PlanLine,
    SuitabilityError,
    check_fund,
    verify_plan,
)
from wealth_advisor.models import Bucket, Goal, RiskPreference
from wealth_advisor.models.core import AllocationBand
from wealth_advisor.pipeline import advise
from wealth_advisor.rag.index import VectorIndex

GOAL = Goal(amount=50_000, horizon_years=5, risk_preference=RiskPreference.CONSERVATIVE)


def test_end_to_end_cited_shortlist(demo_conn):
    res = advise(GOAL, ScriptedLLM(), demo_conn, as_of=AS_OF)
    d = res.decision
    assert d.rows, "expected a non-empty shortlist"
    s = get_settings()
    assert sum(r.amount for r in d.rows) <= GOAL.amount
    assert all(r.weight <= s.max_weight_per_instrument for r in d.rows)
    by_bucket: dict = {}
    for r in d.rows:
        by_bucket[r.bucket] = by_bucket.get(r.bucket, 0) + 1
        assert r.citation_ids and r.rationale
    assert all(n <= s.max_instruments_per_bucket for n in by_bucket.values())
    # only direct, long-enough, cheap, big funds: Orchid/Juniper brands in the demo universe
    assert all(("Orchid" in r.name or "Juniper" in r.name) and "Direct" in r.name for r in d.rows)
    # every recommendation cites sources; rejected funds carry reasons
    assert all(rec.citations for rec in d.record.recommendations)
    assert d.rejected is not None
    assert any("Disclosure" in x or "AI-generated" in x for x in res.compliance.disclosures)
    assert any("synthetic" in x for x in res.compliance.disclosures)
    assert d.record.profile_version == res.profile.version


def test_short_horizon_leaves_equity_unallocated(demo_conn):
    goal = GOAL.model_copy(update={"horizon_years": 2})
    res = advise(goal, ScriptedLLM(), demo_conn, as_of=AS_OF)
    assert all(r.bucket != Bucket.EQUITY for r in res.decision.rows)
    assert any(e.startswith("equity") for e in res.decision.escalations)
    assert res.decision.unallocated_weight > 0


def test_suitability_blocks_deliberately_bad_shortlist(demo_conn):
    res = advise(GOAL, ScriptedLLM(), demo_conn, as_of=AS_OF)
    tools = ResearchTools(MetadataStore(demo_conn), VectorIndex(demo_conn), as_of=AS_OF)
    cfg = replace(default_config(Bucket.DEBT, categories_for(Bucket.DEBT)), max_picks=20)
    entries = list(MFResearchAgent(ScriptedLLM(max_picks=20), tools, cfg).run(GOAL, AS_OF).section)
    by_id = {e.instrument.id: e for e in entries}
    bad = by_id["DEMO003"]  # Maple Liquid: expense over ceiling, rating 3
    regular = by_id["DEMO004"]  # Falcon Liquid: regular plan
    result = res.decision.model_copy(deep=True)
    result.rows[0].instrument_id = bad.instrument.id
    result.rows[1].instrument_id = regular.instrument.id
    result.rows[2].weight = 55.0  # concentration + band breach
    with pytest.raises(SuitabilityError) as exc:
        review(result, entries, GOAL)
    rules = {v.rule for v in exc.value.violations}
    assert {"expense_ratio", "plan", "concentration"} <= rules


def test_verify_plan_total_and_band():
    band = AllocationBand(ranges={Bucket.DEBT: (70, 85)})
    facts = {"A": {"plan": "direct", "expense_ratio": 0.1, "aum_cr": 9000, "track_years": 7, "rating": 5}}
    lines = [PlanLine(instrument_id="A", bucket=Bucket.DEBT, category="Liquid", weight=30)]
    assert verify_plan(lines, facts, GOAL, band) == []
    lines2 = lines * 4
    rules = {v.rule for v in verify_plan(lines2, facts, GOAL, band)}
    assert {"total_weight", "bucket_count"} <= rules


def test_check_fund_flags_missing_data_and_exclusions():
    goal = GOAL.model_copy(update={"excluded_categories": ["mid cap"]})
    v = check_fund("X", "Mid Cap", {"plan": "direct"}, goal)
    assert {x.rule for x in v} >= {"excluded_category", "missing_data", "horizon"} or {
        x.rule for x in v} >= {"excluded_category", "missing_data"}


def test_advise_stock_sleeve_carve_out_and_caps(demo_conn):
    # Moderate risk, 7Y horizon with include_stocks=True carves out Direct Equity sleeve
    mod_goal = Goal(amount=500_000, horizon_years=7, risk_preference=RiskPreference.MODERATE)
    res = advise(
        mod_goal,
        ScriptedLLM(),
        demo_conn,
        as_of=AS_OF,
        include_stocks=True,
        stock_rule_set="set2",
    )
    stock_rows = [
        r for r in res.decision.rows if r.instrument_type in ("stock", "equity") or r.category == "Direct Equity"
    ]
    assert len(stock_rows) >= 1
    assert all(r.weight <= 10.0 for r in stock_rows)
    assert any("Direct equity" in d for d in res.compliance.disclosures)


def test_advise_stocks_skipped_for_conservative_or_short_horizon(demo_conn):
    from wealth_advisor.agents.profiling import ProfileAssumptions

    # Conservative risk gets 0% stock sleeve even when include_stocks=True
    cons_goal = Goal(
        amount=500_000,
        horizon_years=5,
        risk_preference=RiskPreference.CAPITAL_PRESERVATION,
    )
    low_risk_assumptions = ProfileAssumptions(savings_rate=0.03, emergency_months=1.0, hhi=0.9)
    res_cons = advise(
        cons_goal,
        ScriptedLLM(),
        demo_conn,
        assumptions=low_risk_assumptions,
        as_of=AS_OF,
        include_stocks=True,
    )
    assert all(r.instrument_type not in ("stock", "equity") for r in res_cons.decision.rows)
    assert any("Conservative" in w or "0%" in w for w in res_cons.stock_warnings)

    # Horizon < 5Y gets 0% stock sleeve even for Aggressive risk
    short_goal = Goal(amount=500_000, horizon_years=3, risk_preference=RiskPreference.AGGRESSIVE)
    res_short = advise(short_goal, ScriptedLLM(), demo_conn, as_of=AS_OF, include_stocks=True)
    assert all(r.instrument_type not in ("stock", "equity") for r in res_short.decision.rows)
    assert any("horizon" in w.lower() for w in res_short.stock_warnings)
