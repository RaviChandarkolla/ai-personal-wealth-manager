"""Unit tests for Stock Rule Sets (Set 1 heuristics & Set 2 FA Course Notes)."""

from __future__ import annotations

from wealth_advisor.data.stocks.provider import StockSectorMedian
from wealth_advisor.data.stocks.synthetic import SyntheticStockProvider
from wealth_advisor.domain.stock_rulesets import (
    compare_stock_rule_sets,
    evaluate_stock_set1,
    evaluate_stock_set2,
    get_stock_rules_catalog,
)


def _it_sector_median() -> StockSectorMedian:
    return StockSectorMedian(
        sector="Information Technology",
        stock_count=4,
        median_pe=28.0,
        median_pb=9.0,
        median_ev_ebitda=19.0,
        median_ps=6.0,
        median_roe=0.28,
        median_roce=0.34,
        as_of_date="2026-03-31",
    )


def _bank_sector_median() -> StockSectorMedian:
    return StockSectorMedian(
        sector="Banking",
        stock_count=5,
        median_pe=18.5,
        median_pb=2.6,
        median_ev_ebitda=None,
        median_ps=3.2,
        median_roe=0.16,
        median_roce=None,
        as_of_date="2026-03-31",
    )


def test_set2_non_bank_9_pointer_default_no_mcap_limit():
    provider = SyntheticStockProvider()
    snap = provider.fetch_snapshot("TCS")
    fins = provider.fetch_financials("TCS")
    assert snap is not None

    eval_s2 = evaluate_stock_set2(
        snap,
        fins,
        _it_sector_median(),
        is_bank=False,
        min_market_cap_cr=None,
    )
    assert eval_s2.rule_set == "set2"
    assert eval_s2.is_bank is False
    assert eval_s2.passed is True
    assert eval_s2.evaluated_count == 9
    assert eval_s2.min_required_evaluated == 6
    assert eval_s2.pass_ratio >= 0.80
    assert eval_s2.red_flags == []

    mcap_rule = next(c for c in eval_s2.criteria if c.rule_id == "S2_NB_10_MCAP")
    assert mcap_rule.status == "not_applied"


def test_set2_non_bank_10_pointer_with_mcap_filter():
    provider = SyntheticStockProvider()
    snap = provider.fetch_snapshot("TCS")
    fins = provider.fetch_financials("TCS")
    assert snap is not None

    # Passes when min_market_cap_cr is below TCS market cap
    eval_pass = evaluate_stock_set2(
        snap,
        fins,
        _it_sector_median(),
        is_bank=False,
        min_market_cap_cr=50_000.0,
    )
    assert eval_pass.evaluated_count == 10
    assert eval_pass.min_required_evaluated == 7
    assert eval_pass.passed is True
    mcap_rule_pass = next(c for c in eval_pass.criteria if c.rule_id == "S2_NB_10_MCAP")
    assert mcap_rule_pass.status == "passed"

    # Fails when min_market_cap_cr is above TCS market cap
    eval_fail = evaluate_stock_set2(
        snap,
        fins,
        _it_sector_median(),
        is_bank=False,
        min_market_cap_cr=50_000_000.0,
    )
    assert eval_fail.evaluated_count == 10
    assert eval_fail.passed is False
    mcap_rule_fail = next(c for c in eval_fail.criteria if c.rule_id == "S2_NB_10_MCAP")
    assert mcap_rule_fail.status == "failed"


def test_set2_bank_5_pointer_default_and_6_pointer_with_mcap():
    provider = SyntheticStockProvider()
    snap = provider.fetch_snapshot("HDFCBANK")
    fins = provider.fetch_financials("HDFCBANK")
    assert snap is not None

    eval_default = evaluate_stock_set2(
        snap,
        fins,
        _bank_sector_median(),
        is_bank=True,
        min_market_cap_cr=None,
    )
    assert eval_default.is_bank is True
    assert eval_default.evaluated_count == 5
    assert eval_default.min_required_evaluated == 4
    assert eval_default.passed is True
    mcap_rule = next(c for c in eval_default.criteria if c.rule_id == "S2_BK_06_MCAP")
    assert mcap_rule.status == "not_applied"

    eval_with_mcap = evaluate_stock_set2(
        snap,
        fins,
        _bank_sector_median(),
        is_bank=True,
        min_market_cap_cr=100_000.0,
    )
    assert eval_with_mcap.evaluated_count == 6
    assert eval_with_mcap.min_required_evaluated == 4
    assert eval_with_mcap.passed is True


def test_set2_governance_and_solvency_red_flags_fail():
    provider = SyntheticStockProvider()
    snap = provider.fetch_snapshot("WEAKCORP")
    fins = provider.fetch_financials("WEAKCORP")
    assert snap is not None

    eval_weak = evaluate_stock_set2(
        snap,
        fins,
        _it_sector_median(),
        is_bank=False,
    )
    assert eval_weak.passed is False
    assert len(eval_weak.red_flags) >= 2
    assert any("pledge" in rf.lower() or "auditor" in rf.lower() for rf in eval_weak.red_flags)


def test_set1_evaluation():
    provider = SyntheticStockProvider()
    snap = provider.fetch_snapshot("INFY")
    fins = provider.fetch_financials("INFY")
    assert snap is not None

    eval_s1 = evaluate_stock_set1(
        snap,
        fins,
        _it_sector_median(),
        is_bank=False,
    )
    assert eval_s1.rule_set == "set1"
    assert eval_s1.passed is True
    assert eval_s1.composite_score > 0
    assert set(eval_s1.sub_scores.keys()) == {
        "fundamental",
        "fit",
        "news",
        "sentiment",
        "liquidity_cost",
    }


def test_rules_catalog_and_comparison():
    cat1 = get_stock_rules_catalog("set1")
    cat2 = get_stock_rules_catalog("set2")
    assert cat1["rule_set"] == "set1"
    assert len(cat1["rules"]) >= 5
    assert cat2["rule_set"] == "set2"
    assert len(cat2["rules"]) == 16  # 10 non-bank + 6 bank

    cmp_data = compare_stock_rule_sets()
    assert "set1" in cmp_data["rule_sets"]
    assert "set2" in cmp_data["rule_sets"]
    assert len(cmp_data["dimensions"]) >= 6
