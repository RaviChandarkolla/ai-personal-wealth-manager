from tests.test_m8_decision import *  # noqa: F401,F403  (reuse fixtures/imports)


def test_trend_based_smoke(demo_conn):
    goal = Goal(amount=500_000, horizon_years=7, risk_preference=RiskPreference.MODERATE)
    res = advise(goal, ScriptedLLM(), demo_conn, as_of=AS_OF, include_stocks=True, trend_based_stocks=True)
    assert res.trend_based_stocks
    assert res.trend_regime.get("regime") in {"bullish", "neutral", "bearish", "unknown"}
    stock_rows = [r for r in res.decision.rows if r.category == "Direct Equity"]
    assert stock_rows and all(r.weight <= 10.0 for r in stock_rows)
    assert any("momentum" in v.sub_scores for v in res.verdicts)
