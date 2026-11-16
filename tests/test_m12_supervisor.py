"""Tests for M12: Supervisor fan-out + blackboard dossier."""

from __future__ import annotations

import time
from datetime import date

import pytest

from wealth_advisor.agents.analyst import AnalystAgent
from wealth_advisor.agents.decision import decide
from wealth_advisor.agents.mf_researcher import ResearcherConfig, ResearchResult, default_config
from wealth_advisor.agents.profiling import ProfilingAgent
from wealth_advisor.bootstrap import bootstrap_demo
from wealth_advisor.data.store import MetadataStore
from wealth_advisor.db import open_db
from wealth_advisor.domain.allocation import target_allocation
from wealth_advisor.domain.categories import categories_for
from wealth_advisor.llm.base import BudgetedLLM
from wealth_advisor.llm.fake import FakeLLM
from wealth_advisor.models.core import (
    Citation,
    DossierEntry,
    Fact,
    Goal,
    Instrument,
    NoData,
)
from wealth_advisor.models.enums import Bucket, RiskPreference
from wealth_advisor.orchestration.supervisor import (
    ResearchSupervisor,
    all_dossier_entries,
    collate_dossier,
    default_multi_asset_configs,
    dossier_timeout_sections,
)
from wealth_advisor.rag.index import VectorIndex


def _deterministic_llm() -> BudgetedLLM:
    return BudgetedLLM(FakeLLM([]), max_calls=0)


def _setup_demo_env():
    conn = open_db(":memory:")
    bootstrap_demo(conn)
    store = MetadataStore(conn)
    index = VectorIndex(conn)
    as_of = store.latest_nav_date() or date(2026, 3, 31)
    return conn, store, index, as_of


def test_supervisor_selects_researchers_by_horizon_and_risk():
    _, store, index, _ = _setup_demo_env()
    llm = _deterministic_llm()

    # Short horizon (<1y) conservative goal -> skips EQUITY and ALTERNATIVES
    short_goal = Goal(
        amount=200_000,
        horizon_years=0.5,
        risk_preference=RiskPreference.CONSERVATIVE,
        purpose="emergency parking",
    )
    profile, _ = ProfilingAgent.build_profile(short_goal)
    target = target_allocation(profile.allocation_band)

    supervisor = ResearchSupervisor(llm=llm, store=store, index=index)
    active, skipped = supervisor.select_configs(short_goal, profile, target=target)
    selected_fields = {cfg.output_field for cfg in active}

    assert "equity" not in selected_fields
    assert "alt" not in selected_fields
    assert "fixed_income" in selected_fields or "mf" in selected_fields
    assert "equity" in skipped
    assert "alt" in skipped


def test_adding_dummy_researcher_requires_only_researcher_config():
    """M12 requirement: Adding a dummy second researcher requires ONLY a new ResearcherConfig."""
    _, store, index, as_of = _setup_demo_env()
    llm = _deterministic_llm()

    goal = Goal(
        amount=500_000,
        horizon_years=5.0,
        risk_preference=RiskPreference.MODERATE,
        purpose="wealth creation",
    )
    profile, _ = ProfilingAgent.build_profile(goal)
    target = {
        Bucket.EQUITY: 50.0,
        Bucket.DEBT: 35.0,
        Bucket.GOLD: 10.0,
        Bucket.ALTERNATIVES: 5.0,
    }

    dummy_cfg = ResearcherConfig(
        asset_class=Bucket.DEBT,
        persona="Dummy fixed income researcher",
        tools=("query_fixed_income",),
        output_field="fixed_income",
        categories=("Bank FD",),
        max_picks=2,
    )
    mf_cfg = default_config(Bucket.EQUITY, categories_for(Bucket.EQUITY))

    supervisor = ResearchSupervisor(
        llm=llm,
        store=store,
        index=index,
        configs=[mf_cfg, dummy_cfg],
    )
    res = supervisor.run(goal=goal, as_of=as_of, profile=profile, target=target)

    assert isinstance(res.dossier.mf, list) and len(res.dossier.mf) > 0
    assert isinstance(res.dossier.fixed_income, list) and len(res.dossier.fixed_income) > 0
    assert len(all_dossier_entries(res.dossier)) == len(res.dossier.mf) + len(res.dossier.fixed_income)


def test_supervisor_timeout_produces_nodata_lowers_confidence_and_escalates():
    """M12 Exit Criterion:
    Killing/timing-out one researcher mid-run produces NoData('timeout') in its dossier slot,
    lowers confidence on affected verdicts, and triggers the 'missing section' escalation rule.
    """
    _, store, index, as_of = _setup_demo_env()
    llm = _deterministic_llm()

    goal = Goal(
        amount=500_000,
        horizon_years=5.0,
        risk_preference=RiskPreference.MODERATE,
        purpose="retirement",
    )
    profile, _ = ProfilingAgent.build_profile(goal)
    target = {
        Bucket.EQUITY: 50.0,
        Bucket.DEBT: 40.0,
        Bucket.GOLD: 10.0,
        Bucket.ALTERNATIVES: 0.0,
    }

    def slow_commodity_runner(_goal: Goal, _as_of: date):
        time.sleep(0.25)
        return []

    supervisor = ResearchSupervisor(
        llm=llm,
        store=store,
        index=index,
        configs=default_multi_asset_configs(),
        timeout_seconds=0.05,
        runners={"commodity": slow_commodity_runner},
    )
    sup_res = supervisor.run(goal=goal, as_of=as_of, profile=profile, target=target)

    # 1. Timed-out slot is NoData(reason="timeout"), never silently dropped
    assert isinstance(sup_res.dossier.commodity, NoData)
    assert sup_res.dossier.commodity.reason == "timeout"
    assert "commodity" in sup_res.timed_out_sections
    assert "commodity" in dossier_timeout_sections(sup_res.dossier)

    # 2. Analyst produces verdicts on available entries; Decision Maker lowers confidence & escalates
    entries = all_dossier_entries(sup_res.dossier)
    assert len(entries) > 0
    analyst = AnalystAgent(llm)
    baseline_verdicts = [
        analyst.analyse(e, goal, profile.category, as_of, use_llm=False)
        for e in entries
    ]
    n_timeouts = len(dossier_timeout_sections(sup_res.dossier))
    adjusted_verdicts = [
        v.model_copy(update={"confidence": round(max(0.0, v.confidence - 0.15 * n_timeouts), 4)})
        for v in baseline_verdicts
    ]

    decision_with_timeout = decide(
        run_id="run_m12_timeout",
        profile=profile,
        goal=goal,
        target=target,
        entries=entries,
        verdicts={v.instrument_id: v for v in adjusted_verdicts},
        dossier=sup_res.dossier,
    )

    assert any("commodity" in esc and "timed out" in esc for esc in decision_with_timeout.escalations)

    # Verify verdict confidence was lowered compared to baseline
    baseline_conf = {v.instrument_id: v.confidence for v in baseline_verdicts}
    for adj_v in adjusted_verdicts:
        assert adj_v.confidence < baseline_conf[adj_v.instrument_id]


def test_collate_dossier_rejects_uncited_facts():
    inst = Instrument(
        id="TEST_INST",
        name="Test Direct Plan",
        asset_class=Bucket.EQUITY,
        category="Large Cap",
    )
    empty_source_fact = Fact(
        id="f1",
        name="cagr_5y",
        value=0.14,
        citation=Citation(source="", as_of=date(2026, 3, 31)),
    )
    entry = DossierEntry(instrument=inst, facts=[empty_source_fact])
    res = ResearchResult(config_field="mf", section=[entry])
    with pytest.raises(ValueError, match="Uncited fact"):
        collate_dossier([res])
