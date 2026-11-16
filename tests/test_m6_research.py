import pytest
from tests.conftest import AS_OF
from tests.fakes import ScriptedLLM

from wealth_advisor.agents.mf_researcher import MFResearchAgent, Pick, Selection, default_config
from wealth_advisor.agents.tools import ResearchTools
from wealth_advisor.data.store import MetadataStore
from wealth_advisor.domain.categories import categories_for
from wealth_advisor.guardrails.grounding import check_text
from wealth_advisor.llm import FakeLLM
from wealth_advisor.models import Bucket, Fact, Goal, NoData, RiskPreference
from wealth_advisor.models.core import Citation
from wealth_advisor.rag.index import VectorIndex

GOAL = Goal(amount=50_000, horizon_years=5, risk_preference=RiskPreference.CONSERVATIVE)
REAL = "DEMO000"  # Orchid Liquid Fund - Direct Plan


def _agent(conn, llm, tools_allowed=ResearchTools.ALL):
    tools = ResearchTools(MetadataStore(conn), VectorIndex(conn), tools_allowed, as_of=AS_OF)
    return MFResearchAgent(llm, tools, default_config(Bucket.DEBT, categories_for(Bucket.DEBT)))


def _facts():
    c = Citation(source="s", as_of=AS_OF)
    return [
        Fact(id="F1:expense_ratio", name="expense_ratio", value=0.15, citation=c),
        Fact(id="F1:cagr_5y", name="cagr_5y", value=7.234, citation=c),
    ]


def test_grounding_accepts_matching_numbers_names_and_ids():
    ok = check_text("Orchid Liquid Fund charges 0.15% and returned 7.2% over 5-year [F1:cagr_5y]",
                    _facts(), {"Orchid Liquid Fund - Direct Plan"})
    assert ok.ok


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("Expense ratio is only 0.07%", "ungrounded_numbers"),
        ("Zenith Mega Fund is a great pick", "unknown_names"),
        ("Strong returns [F9:made_up]", "unknown_citations"),
    ],
)
def test_grounding_flags_fabrications(text, field):
    rep = check_text(text, _facts(), {"Orchid Liquid Fund - Direct Plan"})
    assert not rep.ok and getattr(rep, field)


def test_agent_builds_cited_entries_from_tools(demo_conn):
    res = _agent(demo_conn, ScriptedLLM(max_picks=3)).run(GOAL, AS_OF)
    assert not isinstance(res.section, NoData) and len(res.section) == 3
    for entry in res.section:
        assert entry.facts and all(f.citation.source for f in entry.facts)
        assert any(f.name == "doc_0" for f in entry.facts)  # retrieved fact-sheet excerpt
    assert {c.tool for c in res.tool_calls} >= {"query_funds", "get_metrics", "retrieve_docs"}


def test_unknown_tool_is_refused(demo_conn):
    tools = ResearchTools(MetadataStore(demo_conn), VectorIndex(demo_conn), ["query_funds"])
    with pytest.raises(PermissionError):
        tools.call("retrieve_docs", query="x")


FABRICATIONS = [
    Pick(scheme_code="FAKE999", reason="Great fund"),  # invented scheme
    Pick(scheme_code=REAL, reason="Zenith Mega Fund is better and costs only 0.07%"),  # name+number
    Pick(scheme_code=REAL, reason="Expense ratio is 0.01%"),  # invented number
]


@pytest.mark.parametrize("bad", FABRICATIONS)
def test_adversarial_fabrication_regenerated_then_clean(demo_conn, bad):
    llm = ScriptedLLM(max_picks=2, bad_selection=Selection(picks=[bad, Pick(scheme_code=REAL)]))
    res = _agent(demo_conn, llm).run(GOAL, AS_OF)
    codes = [e.instrument.id for e in res.section]  # type: ignore[union-attr]
    assert "FAKE999" not in codes and codes
    assert llm.calls == 2 and all("Zenith" not in n for n in res.notes.values())


def test_persistent_fabrication_is_blocked(demo_conn):
    bad = Selection(picks=[
        Pick(scheme_code="FAKE999", reason="Great fund"),
        Pick(scheme_code=REAL, reason="Zenith Mega Fund costs only 0.07%"),
    ])
    res = _agent(demo_conn, FakeLLM([bad, bad])).run(GOAL, AS_OF)
    assert [e.instrument.id for e in res.section] == [REAL]  # type: ignore[union-attr]
    assert res.notes == {} and any("FAKE999" in d for d in res.dropped)
    assert any("stripped" in d for d in res.dropped)


def test_all_invented_means_no_data(demo_conn):
    bad = Selection(picks=[Pick(scheme_code="FAKE1"), Pick(scheme_code="FAKE2")])
    res = _agent(demo_conn, FakeLLM([bad, bad])).run(GOAL, AS_OF)
    assert isinstance(res.section, NoData)


def test_excluded_categories_never_reach_candidates(demo_conn):
    goal = GOAL.model_copy(update={"excluded_categories": ["liquid"]})
    res = _agent(demo_conn, ScriptedLLM(max_picks=20)).run(goal, AS_OF)
    assert all(e.instrument.category != "Liquid" for e in res.section)  # type: ignore[union-attr]
