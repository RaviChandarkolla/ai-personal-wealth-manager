from dataclasses import replace

from tests.conftest import AS_OF
from tests.fakes import ScriptedLLM

from wealth_advisor.agents.analyst import AnalystAgent, AnalystProse
from wealth_advisor.agents.mf_researcher import MFResearchAgent, default_config
from wealth_advisor.agents.tools import ResearchTools
from wealth_advisor.data.store import MetadataStore
from wealth_advisor.domain.categories import categories_for
from wealth_advisor.domain.subscores import (
    confidence,
    detect_conflicts,
    fit_score,
    fundamental_score,
    liquidity_cost_score,
)
from wealth_advisor.guardrails.grounding import citations_in
from wealth_advisor.llm import FakeLLM
from wealth_advisor.models import Bucket, Goal, RiskCategory, RiskPreference
from wealth_advisor.rag.index import VectorIndex

GOAL = Goal(amount=50_000, horizon_years=5, risk_preference=RiskPreference.CONSERVATIVE)


def _entries(conn, bucket=Bucket.DEBT):
    tools = ResearchTools(MetadataStore(conn), VectorIndex(conn), as_of=AS_OF)
    agent = MFResearchAgent(ScriptedLLM(max_picks=20), tools,
                            replace(default_config(bucket, categories_for(bucket)), max_picks=20))
    return {e.instrument.id: e for e in agent.run(GOAL, AS_OF).section}  # type: ignore[union-attr]


def test_subscore_examples():
    assert fundamental_score({"pct_roll_3y": 80, "pct_cagr_5y": 60, "sharpe": 0.75,
                              "max_drawdown": -10}) == 67.5
    assert fundamental_score({}) == 50.0
    assert fit_score({"volatility": 10}, "Large Cap", "Moderate", 5) == 100
    assert fit_score({"volatility": 21}, "Large Cap", "Moderate", 5) == 0
    assert fit_score({"volatility": 10}, "Mid Cap", "Moderate", 2) == 50  # horizon too short
    assert liquidity_cost_score({"expense_ratio": 0.25, "aum_cr": 1000}, "Short Duration") == 62.5
    assert liquidity_cost_score({"expense_ratio": 0.25, "aum_cr": 1000, "plan": "regular"},
                                "Short Duration") == 42.5


def test_conflicts_are_detected_not_averaged():
    conflicts = detect_conflicts({"pct_roll_3y": 90, "expense_ratio": 1.5, "rating": 3,
                                  "max_drawdown": -30, "plan": "regular"}, "Large Cap")
    assert len(conflicts) == 4


def test_missing_field_lowers_confidence():
    full = {k: 1.0 for k in ("expense_ratio", "aum_cr", "roll_3y_median", "cagr_5y", "volatility",
                             "max_drawdown", "sharpe")}
    assert confidence(full, 0) == 1.0
    assert confidence({k: v for k, v in full.items() if k != "sharpe"}, 0) < 1.0
    assert confidence(full, 1) < confidence(full, 0)
    assert confidence(full, 0, n_stale_docs=1) < 1.0


def test_same_dossier_same_scores(demo_conn):
    entries = _entries(demo_conn)
    e = entries["DEMO000"]
    a = AnalystAgent(ScriptedLLM()).analyse(e, GOAL, RiskCategory.MODERATE, AS_OF)
    b = AnalystAgent(ScriptedLLM()).analyse(e, GOAL, RiskCategory.MODERATE, AS_OF)
    assert a.score == b.score and a.sub_scores == b.sub_scores and a.confidence == b.confidence
    assert 0 <= a.score <= 100 and set(a.sub_scores) == {
        "fundamental", "fit", "news", "sentiment", "liquidity_cost"}


def test_removing_a_data_field_lowers_confidence(demo_conn):
    e = _entries(demo_conn)["DEMO000"]
    base = AnalystAgent(ScriptedLLM()).analyse(e, GOAL, RiskCategory.MODERATE, AS_OF)
    trimmed = e.model_copy(update={"facts": [f for f in e.facts if f.name != "sharpe"]})
    lower = AnalystAgent(ScriptedLLM()).analyse(trimmed, GOAL, RiskCategory.MODERATE, AS_OF)
    assert lower.confidence < base.confidence


def test_uncited_prose_rejected_then_deterministic_fallback(demo_conn):
    e = _entries(demo_conn)["DEMO000"]
    bad = AnalystProse(pros=["Looks great"], cons=["Nothing"], change_conditions=[])
    llm = FakeLLM([bad, bad])
    agent = AnalystAgent(llm)
    v = agent.analyse(e, GOAL, RiskCategory.MODERATE, AS_OF)
    assert agent.fallbacks == ["DEMO000"]
    ids = {f.id for f in e.facts}
    for bullet in v.pros + v.cons + v.change_conditions:
        assert citations_in(bullet) and set(citations_in(bullet)) <= ids


def test_invented_citation_or_number_rejected(demo_conn):
    e = _entries(demo_conn)["DEMO000"]
    fid = "DEMO000:expense_ratio"
    bad = AnalystProse(pros=[f"Expense ratio is 0.01% [{fid}]"], cons=[f"Risk [{fid}]"])
    good = AnalystProse(pros=[f"Low cost [{fid}]"], cons=[f"Some risk [{fid}]"])
    agent = AnalystAgent(FakeLLM([bad, good]))
    v = agent.analyse(e, GOAL, RiskCategory.MODERATE, AS_OF)
    assert agent.fallbacks == [] and v.pros == [f"Low cost [{fid}]"]


def test_regular_plan_conflict_in_cons(demo_conn):
    e = _entries(demo_conn)["DEMO004"]  # Falcon = regular plan
    v = AnalystAgent(ScriptedLLM()).analyse(e, GOAL, RiskCategory.MODERATE, AS_OF)
    assert any(c.startswith("Conflict: Regular plan") for c in v.cons)
