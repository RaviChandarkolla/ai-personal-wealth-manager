import pytest

from wealth_advisor.agents.profiling import (
    ExtractedGoal,
    ProfilingAgent,
    parse_amount,
    parse_horizon,
    risk_score_tool,
)
from wealth_advisor.guardrails.input import check_input
from wealth_advisor.llm import FakeLLM
from wealth_advisor.models import RiskCategory, RiskPreference

CP, CONS, MOD, GROW, AGG = (
    RiskPreference.CAPITAL_PRESERVATION, RiskPreference.CONSERVATIVE, RiskPreference.MODERATE,
    RiskPreference.GROWTH, RiskPreference.AGGRESSIVE,
)

# (amount_text, horizon_text, risk, expected amount, expected horizon years)
PHRASINGS = [
    ("₹50,000", "5 years", CONS, 50_000, 5),
    ("Rs. 50000", "5 yrs", CONS, 50_000, 5),
    ("2 lakh", "3 saal", MOD, 200_000, 3),
    ("2.5 lakhs", "7 years", GROW, 250_000, 7),
    ("1 crore", "10 years", AGG, 10_000_000, 10),
    ("1.5 cr", "15 yr", AGG, 15_000_000, 15),
    ("50k", "18 months", CONS, 50_000, 1.5),
    ("5 lac", "24 months", MOD, 500_000, 2),
    ("10,000", "six months", CP, 10_000, 0.5),
    ("one lakh", "five years", MOD, 100_000, 5),
    ("INR 75,000", "2 years", CONS, 75_000, 2),
    ("25 thousand", "3 mahine", CP, 25_000, 0.25),
    ("1,00,000", "4 years", MOD, 100_000, 4),
    ("20L", "8 yrs", GROW, 2_000_000, 8),
    ("rupees 30000", "1 year", CONS, 30_000, 1),
    ("500000", "5y", GROW, 500_000, 5),
    ("40,000", "3-year", MOD, 40_000, 3),
    ("12 lakh", "12 years", AGG, 1_200_000, 12),
    ("3 million", "20 years", AGG, 3_000_000, 20),
    ("8 thousand", "10 months", CONS, 8_000, 10 / 12),
]


@pytest.mark.parametrize(("amount", "horizon", "risk", "exp_amount", "exp_years"), PHRASINGS)
def test_phrasings_parse(amount, horizon, risk, exp_amount, exp_years):
    llm = FakeLLM([ExtractedGoal(amount_text=amount, horizon_text=horizon, risk_preference=risk)])
    res = ProfilingAgent(llm).extract("whatever the user typed")
    assert res.goal is not None and not res.missing
    assert res.goal.amount == pytest.approx(exp_amount)
    assert res.goal.horizon_years == pytest.approx(exp_years)
    assert res.goal.risk_preference == risk


def test_parsers_reject_garbage():
    assert parse_amount("a lot") is None
    assert parse_horizon("soon") is None
    assert parse_horizon("5 parsecs") is None


@pytest.mark.parametrize(
    ("extracted", "missing"),
    [
        (ExtractedGoal(amount_text=None, horizon_text="5 years", risk_preference=CONS), ["amount"]),
        (ExtractedGoal(amount_text="50k", horizon_text=None, risk_preference=CONS), ["horizon"]),
        (ExtractedGoal(amount_text="50k", horizon_text="5 years"), ["risk"]),
        (ExtractedGoal(amount_text="some", horizon_text="soon"), ["amount", "horizon", "risk"]),
    ],
)
def test_only_missing_fields_are_asked(extracted, missing):
    res = ProfilingAgent(FakeLLM([extracted])).extract("text")
    assert res.goal is None and res.missing == missing


@pytest.mark.parametrize(
    "text",
    [
        "Invest 1 lakh with 5x leverage",
        "Buy Nifty call options for me",
        "I want to do intraday trading with 50k",
        "Execute this trade now",
        "trade F&O with my savings",
        "put 10k in bitcoin",
    ],
)
def test_out_of_scope_rejected_without_llm_call(text):
    llm = FakeLLM([])  # would raise if called
    res = ProfilingAgent(llm).extract(text)
    assert res.rejected is not None and not res.rejected.allowed
    assert "Out of scope" in res.rejected.message


def test_in_scope_not_rejected():
    assert check_input("I have 50000 to invest, low risk, 5 years, many options").allowed


def test_profile_uses_deterministic_risk_tool():
    llm = FakeLLM([ExtractedGoal(amount_text="50k", horizon_text="5 years", risk_preference=CONS)])
    goal = ProfilingAgent(llm).extract("x").goal
    profile, assumptions = ProfilingAgent.build_profile(goal)
    # horizon 5y->7, conservative->4, savings 20% stable->8, hhi unknown->5, 6m->8
    assert profile.risk_score == pytest.approx(7 * 0.30 + 4 * 0.25 + 8 * 0.20 + 5 * 0.15 + 8 * 0.10)
    assert profile.category == RiskCategory.MODERATE
    assert profile.version == 1 and "savings_rate" in profile.inputs["assumed_defaults"]
    assert risk_score_tool(profile.inputs["risk_inputs"])["score"] == profile.risk_score
    assert "50,000" in ProfilingAgent.restate(goal)
