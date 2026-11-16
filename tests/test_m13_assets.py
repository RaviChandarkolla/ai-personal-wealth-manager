"""Tests for M13: Asset-class & Context Researchers (13a-13f), Analyst rubric, Suitability, and Grounding."""

from __future__ import annotations

from datetime import date

from wealth_advisor.agents.analyst import AnalystAgent
from wealth_advisor.agents.asset_researchers import (
    AlternativesResearchAgent,
    CommodityResearchAgent,
    EquityResearchAgent,
    FDResearchAgent,
    NewsResearchAgent,
    SentimentResearchAgent,
)
from wealth_advisor.agents.profiling import ProfilingAgent
from wealth_advisor.data.multi_asset import (
    ALTERNATIVES_CATALOG,
    MultiAssetRecord,
    entry_for_multi_asset,
)
from wealth_advisor.data.news import NewsItem, compute_sentiment_trend
from wealth_advisor.domain.subscores import (
    detect_conflicts,
    fundamental_score,
    liquidity_cost_score,
)
from wealth_advisor.guardrails.grounding import check_text
from wealth_advisor.guardrails.suitability import check_fund
from wealth_advisor.llm.base import BudgetedLLM
from wealth_advisor.llm.fake import FakeLLM
from wealth_advisor.models.core import Goal
from wealth_advisor.models.enums import Bucket, RiskPreference

AS_OF = date(2026, 3, 31)


def _sample_goal(amount: float = 1_000_000.0, horizon: float = 5.0) -> Goal:
    return Goal(
        amount=amount,
        horizon_years=horizon,
        risk_preference=RiskPreference.MODERATE,
        purpose="diversified wealth creation",
    )


def test_13a_fd_researcher_dicgc_coverage_and_credit_floor():
    goal = _sample_goal(amount=1_000_000.0)  # ₹10L -> ₹5L DICGC covers 50%
    agent = FDResearchAgent(llm=None)
    res = agent.run(goal, AS_OF)

    assert res.config_field == "fixed_income"
    assert isinstance(res.section, list) and len(res.section) > 0
    first_entry = res.section[0]
    fact_map = {f.name: f.value for f in first_entry.facts}

    assert "fd_rate_pct" in fact_map
    assert "dicgc_coverage_pct" in fact_map
    assert fact_map["dicgc_coverage_pct"] == 50.0
    assert fact_map["credit_rating"] in ("AAA", "SOV", "AA+", "AA")


def test_13b_commodity_researcher_sgb_and_gold_etf():
    goal = _sample_goal()
    agent = CommodityResearchAgent(llm=None)
    res = agent.run(goal, AS_OF)

    assert res.config_field == "commodity"
    assert isinstance(res.section, list) and len(res.section) == 2
    categories = {e.instrument.category for e in res.section}
    assert "SGB" in categories
    assert "Gold ETF" in categories

    sgb_entry = next(e for e in res.section if e.instrument.category == "SGB")
    sgb_facts = {f.name: f.value for f in sgb_entry.facts}
    assert sgb_facts.get("coupon_pct") == 2.5


def test_13c_equity_researcher_nifty50_screening():
    goal = _sample_goal()
    agent = EquityResearchAgent(llm=None)
    res = agent.run(goal, AS_OF)

    assert res.config_field == "equity"
    assert isinstance(res.section, list) and len(res.section) > 0
    for entry in res.section:
        fm = {f.name: f.value for f in entry.facts}
        assert "pe_ratio" in fm
        assert "sector_pe" in fm
        assert "earnings_growth_yoy" in fm
        assert "debt_to_equity" in fm
        assert float(fm.get("promoter_pledge_pct", 0.0)) <= 25.0


def test_13d_alternatives_researcher_reits_invits_skips_pms_aif():
    pms_record = MultiAssetRecord(
        code="PMS_HIGH_TICKET",
        name="Alpha Concentrated PMS",
        bucket=Bucket.ALTERNATIVES,
        category="REIT",
        source="sebi_pms",
        source_url="https://www.sebi.gov.in",
        metrics={"min_investment_inr": 5_000_000, "distribution_yield": 9.0},
    )
    agent = AlternativesResearchAgent(
        llm=None,
        catalog=[*ALTERNATIVES_CATALOG, pms_record],
    )
    res = agent.run(_sample_goal(), AS_OF)

    assert res.config_field == "alt"
    assert isinstance(res.section, list) and len(res.section) > 0
    ids = {e.instrument.id for e in res.section}
    assert "PMS_HIGH_TICKET" not in ids
    for entry in res.section:
        fm = {f.name: f.value for f in entry.facts}
        assert "distribution_yield" in fm
        assert "occupancy_pct" in fm


def test_13e_news_researcher_deduplication_and_materiality_tags():
    dup_headlines = [
        {
            "id": "N1",
            "title": "SEBI tightens mutual fund expense ratio disclosure norms",
            "source": "sebi_circular",
            "materiality": "high",
            "sentiment": "neutral",
        },
        {
            "id": "N2",
            "title": "  sebi   tightens mutual fund expense ratio disclosure norms ",
            "source": "sebi_circular",
            "materiality": "high",
            "sentiment": "neutral",
        },
        {
            "id": "N3",
            "title": "Minor routine holiday schedule notice",
            "source": "nse_circular",
            "materiality": "none",
            "sentiment": "neutral",
        },
    ]
    agent = NewsResearchAgent(custom_headlines=dup_headlines)
    res = agent.run(_sample_goal(), AS_OF)

    assert res.config_field == "news_context"
    assert isinstance(res.section, list) and len(res.section) == 1
    facts = res.section[0].facts
    headline_facts = [f for f in facts if f.name.startswith("news_headline_")]
    materiality_facts = [f for f in facts if f.name.startswith("news_materiality_")]

    # Duplicate headline was removed -> 2 unique headlines
    assert len(headline_facts) == 2
    assert {f.value for f in materiality_facts} == {"high", "none"}


def test_13f_sentiment_researcher_score_trend_and_sample_size():
    items = [
        NewsItem(
            id="S1",
            title="Strong inflows and record earnings boost fund outlook",
            summary="Positive momentum",
            source="amfi",
            url="https://www.amfiindia.com",
            published_at=AS_OF,
            sentiment="positive",
            materiality="medium",
            scope="fund",
        ),
        NewsItem(
            id="S2",
            title="Consistent outperformance continues in Q4",
            summary="Positive trend",
            source="amfi",
            url="https://www.amfiindia.com",
            published_at=AS_OF,
            sentiment="positive",
            materiality="low",
            scope="fund",
        ),
    ]
    agent = SentimentResearchAgent(headlines=items)
    res = agent.run(_sample_goal(), AS_OF)

    assert res.config_field == "sentiment"
    assert isinstance(res.section, list) and len(res.section) == 1
    fm = {f.name: f.value for f in res.section[0].facts}

    assert fm["sentiment_score"] > 50.0
    assert fm["sentiment_trend"] == "improving"
    assert fm["sentiment_sample_size"] == 2
    assert compute_sentiment_trend(items, float(fm["sentiment_score"])) == "improving"


def test_analyst_rubric_suitability_and_grounding_across_asset_classes():
    goal = _sample_goal()
    profile, _ = ProfilingAgent.build_profile(goal)
    llm = BudgetedLLM(FakeLLM([]), max_calls=0)
    analyst = AnalystAgent(llm)

    fd_entry = FDResearchAgent(llm=None).run(goal, AS_OF).section[0]
    eq_entry = EquityResearchAgent(llm=None).run(goal, AS_OF).section[0]
    reit_entry = AlternativesResearchAgent(llm=None).run(goal, AS_OF).section[0]

    verdicts = [
        analyst.analyse(e, goal, profile.category, AS_OF, use_llm=False)
        for e in (fd_entry, eq_entry, reit_entry)
    ]
    assert len(verdicts) == 3
    for v in verdicts:
        assert 0.0 <= v.score <= 100.0
        assert set(v.sub_scores) == {"fundamental", "fit", "news", "sentiment", "liquidity_cost"}

    # Conflict detection for high PE & promoter pledge on equity
    risky_eq_facts = {
        "pe_ratio": 45.0,
        "sector_pe": 20.0,
        "promoter_pledge_pct": 18.0,
        "debt_to_equity": 1.8,
        "cagr_5y": 12.0,
        "roll_3y_median": 11.0,
        "pct_roll_3y": 60.0,
        "pct_cagr_5y": 60.0,
        "sharpe": 0.9,
        "max_drawdown": -15.0,
    }
    conflicts = detect_conflicts(risky_eq_facts, "Large Cap Equity")
    assert len(conflicts) >= 2
    assert fundamental_score(risky_eq_facts) > 0
    assert liquidity_cost_score({"early_withdrawal_penalty": 1.0, "dicgc_coverage_pct": 100.0, "expense_ratio": 0.0}, "Bank FD") > 50

    # Suitability hard constraints on new asset classes
    bad_fd = entry_for_multi_asset(
        MultiAssetRecord(
            code="FD_JUNK",
            name="Unrated Co-op Bank FD",
            bucket=Bucket.DEBT,
            category="Bank FD",
            source="test",
            source_url="https://example.com",
            metrics={
                "credit_rating": "BBB",
                "plan": "direct",
                "expense_ratio": 0.0,
                "aum_cr": 1000.0,
                "track_years": 5.0,
                "rating": 3.0,
            },
        ),
        AS_OF,
    )
    bad_fd_violations = check_fund(
        bad_fd.instrument.id,
        bad_fd.instrument.category,
        {f.name: f.value for f in bad_fd.facts},
        goal,
    )
    assert any(v.rule == "credit_rating" for v in bad_fd_violations)

    # SGB lock-in suitability check for short horizon (< 5y)
    short_goal = _sample_goal(horizon=2.0)
    sgb_entry = CommodityResearchAgent(llm=None).run(goal, AS_OF).section[0]
    sgb_violations = check_fund(
        sgb_entry.instrument.id,
        sgb_entry.instrument.category,
        {f.name: f.value for f in sgb_entry.facts},
        short_goal,
    )
    assert any(v.rule == "horizon" for v in sgb_violations)

    # Grounding check catches fabricated REIT / SGB / FD names
    rep = check_text(
        "Invest in Phantom Galaxy REIT and SuperGold SGB Series 99 for 18.5% return.",
        reit_entry.facts,
        {reit_entry.instrument.name, reit_entry.instrument.id, "REIT"},
    )
    assert not rep.ok
    assert any("Phantom Galaxy REIT" in f for f in rep.fabricated_funds)
