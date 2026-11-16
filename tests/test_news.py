"""Tests for Real-Time News & Market Sentiment Intelligence in Portfolio Creation and Fund Comparison."""
from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from tests.conftest import AS_OF
from tests.fakes import ScriptedLLM

import wealth_advisor.api.main as api_main
from wealth_advisor.api.main import app
from wealth_advisor.data.news import (
    NewsItem,
    classify_headline,
    compute_news_and_sentiment_scores,
    get_fund_news,
    get_macro_and_regulatory_news,
)
from wealth_advisor.db import open_db
from wealth_advisor.domain.subscores import news_score, sentiment_score
from wealth_advisor.models import Goal, RiskPreference
from wealth_advisor.pipeline import advise


def test_classify_headline_and_scores() -> None:
    sent_pos, mat_pos, scope_pos = classify_headline(
        "SEBI compliance audit cleared; strong record SIP inflows and outperform benchmark"
    )
    assert sent_pos == "positive"
    assert mat_pos == "high"
    assert scope_pos == "regulatory"

    sent_neg, mat_neg, _ = classify_headline(
        "SEBI penalty and outflow warning after scheme downgrade"
    )
    assert sent_neg == "negative"
    assert mat_neg == "high"

    items = [
        NewsItem(
            id="n1",
            title="Strong record SIP inflows and top quartile alpha",
            summary="Positive momentum",
            source="AMFI",
            published_at=date(2026, 4, 17),
            sentiment="positive",
            materiality="high",
            scope="category",
        ),
        NewsItem(
            id="n2",
            title="Steady portfolio rebalancing",
            summary="Routine update",
            source="Fund Watch",
            published_at=date(2026, 4, 17),
            sentiment="neutral",
            materiality="low",
            scope="fund",
        ),
    ]
    n_score, s_score, label = compute_news_and_sentiment_scores(items)
    assert n_score > 60.0
    assert s_score > 60.0
    assert label == "positive"


def test_subscore_functions_backward_compatible() -> None:
    # Default 50.0 when news_score / sentiment_score are absent
    assert news_score({}) == 50.0
    assert sentiment_score({}) == 50.0
    # Uses grounded fact values when present
    assert news_score({"news_score": 68.5}) == 68.5
    assert sentiment_score({"sentiment_score": 72.0}) == 72.0


def test_fund_news_and_macro_news_generation() -> None:
    summary = get_fund_news(
        code="INF109K01Z48",
        name="Axis Bluechip Fund - Direct Plan - Growth",
        category="Large Cap",
        amc="Axis Mutual Fund",
        as_of=date(2026, 4, 17),
        fetch_live=False,
    )
    assert summary.code == "INF109K01Z48"
    assert len(summary.items) >= 3
    assert 0.0 <= summary.news_score <= 100.0
    assert 0.0 <= summary.sentiment_score <= 100.0
    assert summary.overall_sentiment in ("positive", "neutral", "negative")

    macro = get_macro_and_regulatory_news(as_of=date(2026, 4, 17), fetch_live=False)
    assert len(macro) >= 3


def test_advise_pipeline_populates_news_and_dossier(demo_conn) -> None:
    conn = open_db(":memory:")
    demo_conn.backup(conn)
    goal = Goal(amount=300_000, horizon_years=7, risk_preference=RiskPreference.MODERATE)
    res = advise(goal, ScriptedLLM(), conn, as_of=AS_OF)
    assert len(res.dossier.news_context) > 0
    assert len(res.dossier.sentiment) > 0
    assert len(res.news_summaries) > 0
    for verdict in res.verdicts:
        assert verdict.instrument_id in res.news_summaries
        summary = res.news_summaries[verdict.instrument_id]
        assert verdict.sub_scores["news"] == summary.news_score
        assert verdict.sub_scores["sentiment"] == summary.sentiment_score


def test_api_advise_and_compare_include_news_sections(monkeypatch) -> None:
    monkeypatch.setattr(api_main, "get_llm", lambda: ScriptedLLM())
    with TestClient(app) as client:
        # 1. Test /api/advise includes news_context and per-recommendation news_summary
        adv_resp = client.post(
            "/api/advise",
            json={
                "amount": 250000,
                "horizon_years": 7,
                "risk_preference": "moderate",
                "purpose": "wealth",
                "savings_rate": 0.25,
                "emergency_months": 6,
                "income_stability": "stable",
                "data_mode": "synthetic",
            },
        )
        assert adv_resp.status_code == 200
        adv_data = adv_resp.json()
        assert "news_context" in adv_data
        assert len(adv_data["news_context"]["macro_news"]) >= 3
        assert len(adv_data["news_context"]["fund_news"]) >= 1
        assert len(adv_data["recommendations"]) >= 1
        first_rec = adv_data["recommendations"][0]
        assert first_rec["news_summary"] is not None
        assert "news_score" in first_rec["news_summary"]
        assert "sentiment_score" in first_rec["news_summary"]

        # 2. Test /api/compare includes news_comparison section
        funds_resp = client.get("/api/funds", params={"data_mode": "synthetic"})
        assert funds_resp.status_code == 200
        funds = funds_resp.json()
        assert len(funds) >= 2
        code_a, code_b = funds[0]["code"], funds[1]["code"]

        cmp_resp = client.post(
            "/api/compare",
            json={"codes": [code_a, code_b], "data_mode": "synthetic"},
        )
        assert cmp_resp.status_code == 200
        cmp_data = cmp_resp.json()
        assert "news_comparison" in cmp_data
        nc = cmp_data["news_comparison"]
        assert "fund_a_news" in nc
        assert "fund_b_news" in nc
        assert "macro_news" in nc
        assert nc["news_winner"] in ("a", "b", "tie")
        assert nc["summary"]

        # 3. Test GET /api/news endpoint
        news_resp = client.get("/api/news", params={"code": code_a, "data_mode": "synthetic"})
        assert news_resp.status_code == 200
        news_data = news_resp.json()
        assert len(news_data["macro_news"]) >= 3
        assert news_data["fund_news"]["code"] == code_a
