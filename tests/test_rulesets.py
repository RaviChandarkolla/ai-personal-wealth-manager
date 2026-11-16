"""Tests for switchable fund-selection rule sets (Set 1, Set 2, Combined) and domain behavior."""

import concurrent.futures
from datetime import date
import pandas as pd
import pytest

from wealth_advisor.config import _base_settings, get_settings
from wealth_advisor.data.store import FundRecord, MetadataStore
from wealth_advisor.db import open_db
from wealth_advisor.domain.allocation import allocation_band, equity_cap
from wealth_advisor.domain.categories import categories_for
from wealth_advisor.domain.metrics import alpha, beta
from wealth_advisor.domain.rulesets import (
    active_rule_set,
    get_active_rule_set,
    set_active_rule_set,
    use_rule_set,
)
from wealth_advisor.domain.subscores import liquidity_cost_score
from wealth_advisor.guardrails.suitability import check_fund
from wealth_advisor.models import Bucket, Goal, RiskCategory, RiskPreference


def test_set1_settings_match_base_defaults():
    """Set 1 reproduces the baseline settings exactly."""
    base = _base_settings()
    with use_rule_set("set1"):
        s1 = get_settings("set1")
        assert s1.fund_rule_set == "set1"
        assert s1.allowed_categories == base.allowed_categories
        assert s1.min_aum_cr == base.min_aum_cr
        assert s1.min_rating == base.min_rating
        assert s1.require_positive_alpha is False
        assert s1.match_maturity_to_horizon is False


def test_set2_settings_and_thread_isolation():
    """Set 2 overrides apply and per-request choice does not leak across requests or threads."""
    with use_rule_set("set2"):
        s2 = get_settings()
        assert s2.fund_rule_set == "set2"
        assert s2.require_positive_alpha is True
        assert s2.match_maturity_to_horizon is True
        assert s2.min_rating is None
        assert s2.min_aum_cr == 100.0
        assert s2.equity_cap_horizon_lt_3y == 0.0
        assert s2.equity_cap_horizon_lt_5y == 25.0
        assert len(s2.allowed_categories) == 16

    # Outside the context manager, should return default (set1)
    assert active_rule_set() == "set1"
    assert get_settings().require_positive_alpha is False

    # Check thread isolation
    def worker(r_set: str) -> str:
        with use_rule_set(r_set):
            return get_settings().fund_rule_set

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        f1 = ex.submit(worker, "set1")
        f2 = ex.submit(worker, "set2")
        f3 = ex.submit(worker, "combined")
        assert f1.result() == "set1"
        assert f2.result() == "set2"
        assert f3.result() == "combined"


def test_set2_bands_and_equity_caps():
    """Set 2 allocation bands and equity caps."""
    with use_rule_set("set2"):
        s = get_settings()
        # Horizon < 3y -> 0% equity cap
        cap_2y = equity_cap(2.0, 6.0, s)
        assert cap_2y == 0.0

        # Horizon 3-5y -> 25% equity cap
        cap_4y = equity_cap(4.0, 6.0, s)
        assert cap_4y == 25.0

        # Allocation band for Conservative at 4y horizon
        band = allocation_band(RiskCategory.CONSERVATIVE, 4.0, 6.0, s)
        eq_range = band.ranges[Bucket.EQUITY]
        # Set 2 Conservative is (10, 25), clamped by 25% cap -> (10, 25)
        assert eq_range == (10.0, 25.0)

        # 2y horizon -> equity range is (0, 0)
        band_2y = allocation_band(RiskCategory.MODERATE, 2.0, 6.0, s)
        assert band_2y.ranges[Bucket.EQUITY] == (0.0, 0.0)


def test_categories_for_excludes_new_categories_under_set1():
    """categories_for only returns allowed categories for the active rule set."""
    with use_rule_set("set1"):
        cats_eq = categories_for(Bucket.EQUITY)
        assert "Small Cap" not in cats_eq
        assert "Large & Mid Cap" not in cats_eq
        assert "ELSS" not in cats_eq
        assert "Aggressive Hybrid" not in cats_eq
        assert "Large Cap" in cats_eq

    with use_rule_set("set2"):
        cats_eq_s2 = categories_for(Bucket.EQUITY)
        assert "Small Cap" in cats_eq_s2
        assert "Large & Mid Cap" in cats_eq_s2
        assert "ELSS" in cats_eq_s2
        assert "Aggressive Hybrid" in cats_eq_s2


def test_check_fund_flags_negative_alpha_and_maturity():
    """check_fund flags negative alpha on active equity and debt maturity > horizon under Set 2."""
    goal = Goal(amount=1000000, horizon_years=2.0, risk_preference=RiskPreference.MODERATE)

    # 1. Negative alpha on active equity fund
    equity_facts = {
        "expense_ratio": 0.75,
        "aum_cr": 1500.0,
        "track_years": 5.0,
        "plan": "direct",
        "alpha": -0.5,
    }

    # Under Set 1: negative alpha does not trigger hard suitability violation
    with use_rule_set("set1"):
        v_s1 = check_fund("EQ001", "Large Cap", equity_facts, goal)
        assert not any(v.rule == "alpha" for v in v_s1)

    # Under Set 2: negative alpha triggers violation for active equity
    with use_rule_set("set2"):
        v_s2 = check_fund("EQ001", "Large Cap", equity_facts, goal)
        assert any(v.rule == "alpha" for v in v_s2)

        # Index Fund is exempt
        v_index = check_fund("IDX001", "Index Fund", equity_facts, goal)
        assert not any(v.rule == "alpha" for v in v_index)

    # 2. Debt maturity matching
    debt_facts = {
        "expense_ratio": 0.35,
        "aum_cr": 2000.0,
        "track_years": 4.0,
        "plan": "direct",
        "avg_maturity_years": 3.5,  # Exceeds 2.0y goal horizon
    }

    with use_rule_set("set1"):
        v_debt_s1 = check_fund("DEBT001", "Corporate Bond", debt_facts, goal)
        assert not any(v.rule == "maturity" for v in v_debt_s1)

    with use_rule_set("set2"):
        v_debt_s2 = check_fund("DEBT001", "Corporate Bond", debt_facts, goal)
        assert any(v.rule == "maturity" for v in v_debt_s2)


def test_amc_bonus_applied_in_liquidity_cost_score():
    """Trusted AMC receives bonus points in liquidity_cost_score under Set 2."""
    facts_sbi = {"expense_ratio": 0.40, "aum_cr": 5000.0, "plan": "direct", "amc": "SBI Mutual Fund"}
    facts_other = {"expense_ratio": 0.40, "aum_cr": 5000.0, "plan": "direct", "amc": "Random AMC"}

    with use_rule_set("set1"):
        s1 = get_settings()
        score_sbi_s1 = liquidity_cost_score(facts_sbi, "Large Cap", s1)
        score_other_s1 = liquidity_cost_score(facts_other, "Large Cap", s1)
        assert score_sbi_s1 == score_other_s1

    with use_rule_set("set2"):
        s2 = get_settings()
        score_sbi_s2 = liquidity_cost_score(facts_sbi, "Large Cap", s2)
        score_other_s2 = liquidity_cost_score(facts_other, "Large Cap", s2)
        assert score_sbi_s2 == score_other_s2 + 5.0


def test_proxy_benchmark_beta_and_alpha_against_itself():
    """Benchmark proxy NAV has beta of 1.0 and alpha of 0.0 when evaluated against itself."""
    dates = pd.date_range("2021-01-01", periods=100, freq="B")
    # Monotonic ascending NAV series
    prices = [100.0 * (1.0005 ** i) for i in range(100)]
    nav_series = pd.Series(prices, index=dates)

    b = beta(nav_series, nav_series)
    a = alpha(nav_series, nav_series, risk_free_rate=0.065)

    assert b == pytest.approx(1.0, abs=1e-3)
    assert a == pytest.approx(0.0, abs=1e-2)


def test_store_column_migration_is_idempotent():
    """MetadataStore column migration runs safely more than once without errors."""
    conn = open_db(":memory:")
    store = MetadataStore(conn)
    # Call migration again explicitly
    store._migrate()
    store._migrate()

    # Query schema to ensure all columns exist
    cols = {row[1] for row in conn.execute("PRAGMA table_info(scheme)").fetchall()}
    assert "manager_since" in cols
    assert "ytm" in cols
    assert "modified_duration" in cols
    assert "avg_maturity_years" in cols

    stats_cols = {row[1] for row in conn.execute("PRAGMA table_info(fund_stats)").fetchall()}
    assert "alpha" in stats_cols
    assert "beta" in stats_cols
    assert "pct_low_vol" in stats_cols
    conn.close()


def test_pipeline_zero_equity_for_2y_goal_under_set2_and_combined(demo_conn):
    """Pipeline tests: 2-year goal gets 0% equity under Set 2 and Combined."""
    from wealth_advisor.pipeline import advise
    from tests.fakes import ScriptedLLM

    goal_2y = Goal(
        amount=1000000,
        horizon_years=2.0,
        risk_preference=RiskPreference.MODERATE,
    )
    llm = ScriptedLLM()

    # Set 2
    res_s2 = advise(goal_2y, llm, demo_conn, rule_set="set2")
    assert res_s2.target[Bucket.EQUITY] == 0.0

    # Combined
    res_comb = advise(goal_2y, llm, demo_conn, rule_set="combined")
    assert res_comb.target[Bucket.EQUITY] == 0.0


def test_combined_recommends_no_fund_rejected_by_set1_or_set2(demo_conn):
    """Pipeline test: on synthetic data, combined recommends no fund rejected by Set 1 or Set 2."""
    from wealth_advisor.pipeline import advise
    from tests.fakes import ScriptedLLM

    goal = Goal(
        amount=1000000,
        horizon_years=7.0,
        risk_preference=RiskPreference.AGGRESSIVE,
    )
    llm = ScriptedLLM()

    res_comb = advise(goal, llm, demo_conn, rule_set="combined")
    assert len(res_comb.decision.record.recommendations) > 0

    # Ensure none of the recommendations violate Set 1 or Set 2 suitability rules
    facts_by_id = {}
    cat_by_id = {}
    for entry in res_comb.dossier.mf:
        facts_by_id[entry.instrument.id] = {f.name: f.value for f in entry.facts}
        cat_by_id[entry.instrument.id] = entry.instrument.category

    for rec in res_comb.decision.record.recommendations:
        f_facts = facts_by_id.get(rec.instrument_id, {})
        category = cat_by_id.get(rec.instrument_id, "Large Cap")

        # Check suitability under Set 1
        with use_rule_set("set1"):
            v_s1 = check_fund(rec.instrument_id, category, f_facts, goal)
            assert len(v_s1) == 0, f"Fund {rec.instrument_id} recommended by combined was rejected by Set 1: {v_s1}"

        # Check suitability under Set 2
        with use_rule_set("set2"):
            v_s2 = check_fund(rec.instrument_id, category, f_facts, goal)
            assert len(v_s2) == 0, f"Fund {rec.instrument_id} recommended by combined was rejected by Set 2: {v_s2}"


def test_audit_replay_set2_and_combined_runs(demo_conn):
    """Verification step 6: Replay Set 2 and Combined runs from audit log and confirm outputs match."""
    from wealth_advisor.pipeline import advise
    from wealth_advisor.audit.log import AuditLog
    from wealth_advisor.audit.replay import replay
    from tests.fakes import ScriptedLLM

    goal = Goal(amount=1000000, horizon_years=5.0, risk_preference=RiskPreference.MODERATE)
    llm = ScriptedLLM()

    # Replay Set 2 run
    res_s2 = advise(goal, llm, demo_conn, rule_set="set2")
    rep_s2 = replay(AuditLog(demo_conn), res_s2.run_id)
    assert rep_s2.ok, f"Set 2 replay failed: {[c for c in rep_s2.checks if not c.ok]}"

    # Replay Combined run
    res_comb = advise(goal, llm, demo_conn, rule_set="combined")
    rep_comb = replay(AuditLog(demo_conn), res_comb.run_id)
    assert rep_comb.ok, f"Combined replay failed: {[c for c in rep_comb.checks if not c.ok]}"



