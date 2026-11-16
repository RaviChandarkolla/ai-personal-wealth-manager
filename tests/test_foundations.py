import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from wealth_advisor.config import AnalystWeights, RiskWeights
from wealth_advisor.domain import (
    Candidate,
    RiskInputs,
    allocation_band,
    annualized_volatility,
    cagr,
    compute_drift,
    compute_risk_score,
    max_drawdown,
    rebalance_plan,
    rolling_returns,
    score_instrument,
    sharpe,
    size_positions,
    sortino,
    target_allocation,
)
from wealth_advisor.llm import CachedLLM, FakeLLM
from wealth_advisor.models import (
    Action,
    Bucket,
    Citation,
    Fact,
    Goal,
    IncomeStability,
    Recommendation,
    RiskCategory,
    RiskPreference,
)

horizons = st.floats(0.1, 40)
prefs = st.sampled_from(list(RiskPreference))


def _inputs(h=5, pref=RiskPreference.MODERATE, sr=0.25, em=6, hhi=None, stab=IncomeStability.STABLE):
    return RiskInputs(horizon_years=h, risk_preference=pref, savings_rate=sr,
                      emergency_months=em, hhi=hhi, income_stability=stab)


def test_weights_sum_to_one():
    assert sum(RiskWeights().model_dump().values()) == pytest.approx(1)
    assert sum(AnalystWeights().model_dump().values()) == pytest.approx(1)


def test_risk_worked_example():
    r = compute_risk_score(_inputs())
    assert r.score == pytest.approx(6.75)
    assert r.category == RiskCategory.MODERATE


@given(h1=horizons, h2=horizons, pref=prefs, sr=st.floats(0, 1), em=st.floats(0, 24))
def test_risk_monotonic_in_horizon(h1, h2, pref, sr, em):
    lo, hi = sorted([h1, h2])
    a = compute_risk_score(_inputs(lo, pref, sr, em)).score
    b = compute_risk_score(_inputs(hi, pref, sr, em)).score
    assert a <= b


@given(h=horizons, pref=prefs, sr=st.floats(0, 1), em=st.floats(0, 24))
def test_risk_deterministic_and_bounded(h, pref, sr, em):
    a, b = compute_risk_score(_inputs(h, pref, sr, em)), compute_risk_score(_inputs(h, pref, sr, em))
    assert a == b and 1 <= a.score <= 10


@given(cat=st.sampled_from(list(RiskCategory)), h=horizons, em=st.floats(0, 24))
def test_allocation_sums_and_caps(cat, h, em):
    band = allocation_band(cat, h, em)
    t = target_allocation(band)
    assert sum(t.values()) == pytest.approx(100)
    assert t[Bucket.EQUITY] <= band.ranges[Bucket.EQUITY][1]
    if h < 1:
        assert t[Bucket.EQUITY] <= 5
    elif h < 3:
        assert t[Bucket.EQUITY] <= 20
    if em < 3:
        assert t[Bucket.EQUITY] <= 30


def test_conservative_target():
    t = target_allocation(allocation_band(RiskCategory.CONSERVATIVE, 5, 6))
    assert t == {Bucket.EQUITY: 22.5, Bucket.DEBT: 72.5, Bucket.GOLD: 5, Bucket.ALTERNATIVES: 0}


@given(scores=st.lists(st.floats(1, 100), min_size=1, max_size=6), bw=st.floats(0, 100))
def test_sizing_respects_caps(scores, bw):
    cands = [Candidate(id=f"f{i}", score=s) for i, s in enumerate(scores)]
    pos = size_positions(cands, bw, 100_000)
    assert len(pos) <= 3
    assert all(p.weight <= 30 + 1e-6 for p in pos)
    assert sum(p.weight for p in pos) <= bw + 1e-4
    assert all(p.amount % 500 == 0 for p in pos)


def test_sizing_redistributes():
    pos = {p.id: p.weight for p in size_positions(
        [Candidate(id="A", score=80), Candidate(id="B", score=20)], 40, 100_000)}
    assert pos == {"A": pytest.approx(30), "B": pytest.approx(10)}


def test_drift_rule():
    items = {i.bucket: i for i in compute_drift({Bucket.EQUITY: 60, Bucket.DEBT: 40},
                                                {Bucket.EQUITY: 50, Bucket.DEBT: 50})}
    assert items[Bucket.EQUITY].breached and items[Bucket.DEBT].breached
    small = compute_drift({Bucket.EQUITY: 52, Bucket.DEBT: 48}, {Bucket.EQUITY: 50, Bucket.DEBT: 50})
    assert not any(i.breached for i in small)


def test_drift_relative_trigger():
    # 3pp absolute but 30% of a 10% target
    items = compute_drift({Bucket.GOLD: 13, Bucket.DEBT: 87}, {Bucket.GOLD: 10, Bucket.DEBT: 90})
    assert next(i for i in items if i.bucket == Bucket.GOLD).breached


@given(a=st.floats(0, 100), t=st.floats(0, 100))
def test_drift_symmetric(a, t):
    x = compute_drift({Bucket.EQUITY: a}, {Bucket.EQUITY: t})[0]
    y = compute_drift({Bucket.EQUITY: t}, {Bucket.EQUITY: a})[0]
    assert x.abs_drift == pytest.approx(-y.abs_drift)


def test_rebalance_plan():
    plan = rebalance_plan({Bucket.EQUITY: 6000, Bucket.DEBT: 4000}, {Bucket.EQUITY: 50, Bucket.DEBT: 50})
    assert plan[Bucket.EQUITY] == pytest.approx(-1000)
    assert plan[Bucket.DEBT] == pytest.approx(1000)
    assert rebalance_plan({Bucket.EQUITY: 5100, Bucket.DEBT: 4900},
                          {Bucket.EQUITY: 50, Bucket.DEBT: 50}) == {}


def _nav(values):
    return pd.Series(values, index=pd.date_range("2020-01-01", periods=len(values), freq="D"))


def test_metrics():
    assert max_drawdown(_nav([100, 120, 90, 100])) == pytest.approx(-0.25)
    idx = pd.to_datetime(["2020-01-01", "2022-01-01"])
    assert cagr(pd.Series([100, 121], index=idx)) == pytest.approx(0.10, abs=1e-3)
    nav = _nav([100, 101, 99, 102, 103, 101, 104])
    assert annualized_volatility(nav) > 0
    assert sortino(nav, 0.0) > sharpe(nav, 0.0) * 0  # computed without error
    with pytest.raises(ValueError):
        max_drawdown(_nav([100, -1]))


def test_rolling_returns():
    idx = pd.date_range("2018-01-01", "2021-01-01", freq="D")
    nav = pd.Series(100 * 1.1 ** ((idx - idx[0]).days / 365.25), index=idx)
    rr = rolling_returns(nav, 1)
    assert not rr.empty and rr.round(3).eq(0.1).all()


def test_score_instrument():
    assert score_instrument(dict.fromkeys(AnalystWeights().model_dump(), 80)) == pytest.approx(80)
    s = dict.fromkeys(AnalystWeights().model_dump(), 0)
    s["fundamental"] = 100
    assert score_instrument(s) == pytest.approx(35)
    with pytest.raises(ValueError):
        score_instrument({"fundamental": 1})


def test_fact_requires_citation():
    with pytest.raises(ValidationError):
        Fact(id="1", name="expense_ratio", value=0.5)
    c = Citation(source="AMFI", as_of="2026-01-01")
    f = Fact(id="1", name="expense_ratio", value=0.5, citation=c)
    assert Fact.model_validate_json(f.model_dump_json()) == f


def test_recommendation_requires_citation():
    with pytest.raises(ValidationError):
        Recommendation(instrument_id="x", action=Action.BUY, target_weight=10, rationale="r", citations=[])


class Out(BaseModel):
    goal: Goal


def test_fake_llm_and_cache(tmp_path):
    g = Out(goal=Goal(amount=50000, horizon_years=5, risk_preference=RiskPreference.CONSERVATIVE))
    fake = FakeLLM([g])
    llm = CachedLLM(fake, tmp_path)
    msgs = [{"role": "user", "content": "hi"}]
    first = llm.complete(msgs, Out)
    second = llm.complete(msgs, Out)
    assert first == second == g
    assert len(fake.calls) == 1 and llm.hits == 1


def test_risk_free_default_from_settings():
    from wealth_advisor.config import get_settings

    nav = _nav([100, 101, 99, 102, 103, 101, 104])
    rf = get_settings().risk_free_rate
    assert sharpe(nav) == sharpe(nav, rf)
    assert sortino(nav) == sortino(nav, rf)


def test_fake_llm_text_and_exhausted():
    fake = FakeLLM(["a"])
    assert fake.complete([{"role": "user", "content": "x"}]) == "a"
    with pytest.raises(RuntimeError):
        fake.complete([{"role": "user", "content": "x"}])


def test_langchain_client_langfuse_config():
    from wealth_advisor.llm import trace_context
    from wealth_advisor.llm.langchain_client import LangChainClient

    class DummyChat:
        def __init__(self):
            self.last_config = None

        def invoke(self, tuples, config=None):
            self.last_config = config

            class Resp:
                content = "ok"

            return Resp()

        def with_structured_output(self, schema):
            parent = self

            class Structured:
                def invoke(self, tuples, config=None):
                    parent.last_config = config
                    return Out(
                        goal=Goal(
                            amount=50000,
                            horizon_years=5,
                            risk_preference=RiskPreference.MODERATE,
                        )
                    )

            return Structured()

    dummy = DummyChat()
    client = LangChainClient(dummy, "gpt-6-luna", trace=True)
    with trace_context(run_id="run-123", step="profiling", metadata={"bucket": "Equity"}):
        client.complete([{"role": "user", "content": "hi"}], Out)

    cfg = dummy.last_config
    assert cfg is not None
    assert cfg["run_name"] == "wealth_advisor.profiling"
    assert "profiling" in cfg["tags"]
    assert cfg["metadata"]["langfuse_session_id"] == "run-123"
    assert cfg["metadata"]["langfuse_trace_name"] == "wealth_advisor.profiling"
    assert cfg["metadata"]["bucket"] == "Equity"
    assert cfg["metadata"]["schema"] == "Out"
    assert len(cfg["callbacks"]) == 1

