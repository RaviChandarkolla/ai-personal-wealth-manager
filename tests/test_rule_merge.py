"""Unit tests for the rule set merge engine (domain/rule_merge.py)."""

import pytest
from wealth_advisor.domain.rule_merge import merge_rule_sets
from wealth_advisor.domain.rulesets import SET1_RULES, SET2_RULES, COMBINED_RULES, COMBINED_PROVENANCE


def test_merge_stricter_minimum_floor():
    """Higher floor wins for minimums (AUM, track record, rating, etc.)."""
    a = {"min_aum_cr": 500.0, "min_rating": 4.0}
    b = {"min_aum_cr": 100.0, "min_rating": None}
    merged, prov = merge_rule_sets(a, b)
    assert merged["min_aum_cr"] == 500.0
    assert merged["min_rating"] == 4.0

    p_aum = next(p for p in prov if p["setting"] == "min_aum_cr")
    assert p_aum["strategy"] == "Stricter minimum"
    assert p_aum["winner"] == "Set 1"

    p_rtg = next(p for p in prov if p["setting"] == "min_rating")
    assert p_rtg["strategy"] == "Stricter minimum"
    assert p_rtg["winner"] == "Set 1"


def test_merge_stricter_maximum_ceiling():
    """Lower ceiling wins for caps (equity caps, beta ceiling, vol caps, expense ceilings)."""
    a = {"equity_cap_horizon_lt_3y": 20.0, "max_beta": None}
    b = {"equity_cap_horizon_lt_3y": 0.0, "max_beta": 1.2}
    merged, prov = merge_rule_sets(a, b)
    assert merged["equity_cap_horizon_lt_3y"] == 0.0
    assert merged["max_beta"] == 1.2

    p_cap = next(p for p in prov if p["setting"] == "equity_cap_horizon_lt_3y")
    assert p_cap["strategy"] == "Stricter maximum"
    assert p_cap["winner"] == "Set 2"

    p_beta = next(p for p in prov if p["setting"] == "max_beta")
    assert p_beta["strategy"] == "Stricter maximum"
    assert p_beta["winner"] == "Set 2"


def test_merge_longer_horizon():
    """Longer minimum holding horizon per category wins."""
    a = {"category_min_horizons": {"Large Cap": 3.0, "Mid Cap": 5.0}}
    b = {"category_min_horizons": {"Large Cap": 5.0, "Mid Cap": 5.0}}
    merged, prov = merge_rule_sets(a, b)
    assert merged["category_min_horizons"]["Large Cap"] == 5.0
    assert merged["category_min_horizons"]["Mid Cap"] == 5.0


def test_merge_overlapping_range_and_conflict_fallback():
    """Range overlap intersection; non-overlapping ranges fall back to lower-risk side with conflict flag."""
    a = {
        "allocation_bands": {
            "Moderate": {
                "equity": (40.0, 60.0),
                "debt": (30.0, 45.0),
            }
        }
    }
    b = {
        "allocation_bands": {
            "Moderate": {
                "equity": (40.0, 60.0),
                "debt": (30.0, 50.0),
            }
        }
    }
    merged, prov = merge_rule_sets(a, b)
    assert merged["allocation_bands"]["Moderate"]["equity"] == (40.0, 60.0)
    assert merged["allocation_bands"]["Moderate"]["debt"] == (30.0, 45.0)

    # Test conflict fallback when ranges do not overlap
    c1 = {"allocation_bands": {"Conservative": {"equity": (10.0, 20.0)}}}
    c2 = {"allocation_bands": {"Conservative": {"equity": (25.0, 35.0)}}}
    merged_conflict, prov_conflict = merge_rule_sets(c1, c2)
    # Lower equity is lower risk
    assert merged_conflict["allocation_bands"]["Conservative"]["equity"] == (10.0, 20.0)
    conflict_entry = next(p for p in prov_conflict if p.get("conflict"))
    assert "Conservative" in conflict_entry["reason"]
    assert conflict_entry.get("conflict") is True




def test_merge_union():
    """Union of allowed categories and trusted AMCs."""
    a = {"allowed_categories": ["Liquid", "Mid Cap"], "trusted_amcs": ["SBI"]}
    b = {"allowed_categories": ["Liquid", "Small Cap"], "trusted_amcs": ["HDFC"]}
    merged, _ = merge_rule_sets(a, b)
    assert set(merged["allowed_categories"]) == {"Liquid", "Mid Cap", "Small Cap"}
    assert set(merged["trusted_amcs"]) == {"SBI", "HDFC"}


def test_merge_either_set():
    """Boolean on/off flags apply if either set has them enabled."""
    a = {"require_positive_alpha": False, "match_maturity_to_horizon": False, "require_direct_plan": True}
    b = {"require_positive_alpha": True, "match_maturity_to_horizon": True, "require_direct_plan": True}
    merged, _ = merge_rule_sets(a, b)
    assert merged["require_positive_alpha"] is True
    assert merged["match_maturity_to_horizon"] is True
    assert merged["require_direct_plan"] is True


def test_merge_blend_weights():
    """Analyst and fundamental weights are averaged and normalized to 1.0."""
    a = {
        "analyst_weights": {
            "fundamental": 0.35,
            "fit": 0.25,
            "news": 0.20,
            "sentiment": 0.10,
            "liquidity_cost": 0.10,
        }
    }
    b = {
        "analyst_weights": {
            "fundamental": 0.45,
            "fit": 0.30,
            "news": 0.05,
            "sentiment": 0.05,
            "liquidity_cost": 0.15,
        }
    }
    merged, prov = merge_rule_sets(a, b)
    aw = merged["analyst_weights"]
    assert sum(aw.values()) == pytest.approx(1.0)
    assert aw["fundamental"] == pytest.approx(0.40)
    assert aw["fit"] == pytest.approx(0.275)
    assert aw["news"] == pytest.approx(0.125)
    assert aw["sentiment"] == pytest.approx(0.075)
    assert aw["liquidity_cost"] == pytest.approx(0.125)


def test_merge_one_sided():
    """Rules only defined by one set are adopted."""
    a = {"ytm_outlier_pp": None, "trusted_amc_bonus": 0.0}
    b = {"ytm_outlier_pp": 2.0, "trusted_amc_bonus": 5.0}
    merged, _ = merge_rule_sets(a, b)
    assert merged["ytm_outlier_pp"] == 2.0
    assert merged["trusted_amc_bonus"] == 5.0


def test_combined_rule_set_matches_step_15():
    """Validate COMBINED_RULES exactly match the values specified in step 15 of the plan."""
    # Allocation bands:
    # Conservative: Equity 15-25%, Debt 75-85%, Gold 0-10%, Alt 0-0%
    cons = COMBINED_RULES["allocation_bands"]["Conservative"]
    assert cons["equity"] == (15.0, 25.0)
    assert cons["debt"] == (75.0, 85.0)
    assert cons["gold"] == (0.0, 10.0)

    # Moderate: Equity 40-60%, Debt 30-45%, Gold 5-10%, Alt 0-0%
    mod = COMBINED_RULES["allocation_bands"]["Moderate"]
    assert mod["equity"] == (40.0, 60.0)
    assert mod["debt"] == (30.0, 45.0)
    assert mod["gold"] == (5.0, 10.0)

    # Aggressive: Equity 70-80%, Debt 15-20%, Gold 5-10%, Alt 0-0%
    agg = COMBINED_RULES["allocation_bands"]["Aggressive"]
    assert agg["equity"] == (70.0, 80.0)
    assert agg["debt"] == (15.0, 20.0)
    assert agg["gold"] == (5.0, 10.0)

    # Equity caps
    assert COMBINED_RULES["equity_cap_horizon_lt_1y"] == 0.0
    assert COMBINED_RULES["equity_cap_horizon_lt_3y"] == 0.0
    assert COMBINED_RULES["equity_cap_horizon_lt_5y"] == 25.0
    assert COMBINED_RULES["equity_cap_low_emergency"] == 30.0

    # Filters
    assert COMBINED_RULES["min_aum_cr"] == 500.0
    assert COMBINED_RULES["min_track_years_equity"] == 5.0
    assert COMBINED_RULES["min_rating"] == 4.0
    assert COMBINED_RULES["require_positive_alpha"] is True
    assert COMBINED_RULES["require_direct_plan"] is True
    assert COMBINED_RULES["match_maturity_to_horizon"] is True

    # Analyst weights
    aw = COMBINED_RULES["analyst_weights"]
    assert aw["fundamental"] == pytest.approx(0.40)
    assert aw["fit"] == pytest.approx(0.275)
    assert aw["news"] == pytest.approx(0.125)
    assert aw["sentiment"] == pytest.approx(0.075)
    assert aw["liquidity_cost"] == pytest.approx(0.125)
