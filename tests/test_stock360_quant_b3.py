from datetime import date

import pytest

from wealth_advisor.domain.stock360 import cycle_phase as cp
from wealth_advisor.domain.stock360 import mechanical as me
from wealth_advisor.domain.stock360 import targets as tg
from wealth_advisor.domain.stock360 import tax


def test_short_term_targets_scale_with_sqrt_time_and_snap():
    ts = tg.short_term_targets(100, 2.0, {})
    one = next(t for t in ts if t.horizon == "1M" and t.scenario == "bull")
    nine = next(t for t in ts if t.horizon == "9M" and t.scenario == "bull")
    assert one.price == pytest.approx(100 + 1.5 * 2 * 21 ** 0.5, abs=0.01)
    assert nine.price > one.price
    bear = next(t for t in ts if t.horizon == "1M" and t.scenario == "bear")
    assert bear.price < 100
    snapped = tg.short_term_targets(100, 2.0, {"R1": 113.0})
    assert any(t.level == "R1" and t.price == 113.0 for t in snapped)
    assert tg.short_term_targets(100, None, {}) == []


def test_long_term_targets_use_multiples():
    r = tg.long_term_targets({1: 10, 3: None}, own_median_pe=20, own_min_pe=15, sector_median_pe=25)
    assert r[1] == {"bear": 150.0, "base": 200.0, "bull": 275.0}
    assert r[3]["base"] is None


def test_tranche_plan_caps_position_and_stop_min_distance():
    p = tg.tranche_plan(100, 2.0, [95, 90], swing_low=99, dma200=97, buffett_entry=80,
                        target_base=120, score=100, max_weight_pct=6)
    assert p["stop"] <= 100 - 1.5 * 2.0
    assert p["position_pct"] == 6
    assert p["tranches"][1] == 95 and p["tranches"][2] == 80
    assert p["rr_at_cmp"] == pytest.approx(20 / (100 - p["stop"]), abs=0.01)
    assert tg.tranche_plan(100, None, [], None, None, None, None, 50, 5) is None


def test_cycle_phase_rules():
    assert cp.cycle_phase("Stage 4", 50, 0)["phase"].startswith("Phase 4")
    assert cp.cycle_phase("Stage 2", 50, 20)["sub_stage"] == "early"
    r = cp.cycle_phase("Stage 3", 90, 80, -2.0)
    assert r["phase"].startswith("Phase 3") and r["confidence"] == "high"
    assert cp.cycle_phase("Stage 1", 30, 5)["confidence"] == "low"  # shareholding unknown
    assert cp.cycle_phase(None, None, None)["phase"] == "Not available"
    assert cp.pct_rank(10, [5, 10, 20, 30]) == 50


def test_tax_rules():
    s = tax.tax_on_sale(100, 150, 1000, date(2025, 1, 1), date(2025, 6, 1))
    assert s["type"] == "STCG" and s["tax"] == 10000 and s["days_to_ltcg"] > 0
    lt = tax.tax_on_sale(100, 400, 1000, date(2024, 1, 1), date(2025, 6, 1))
    assert lt["type"] == "LTCG" and lt["tax"] == pytest.approx((300000 - 125000) * 0.125)
    assert tax.tax_on_sale(100, 80, 10, date(2025, 1, 1), date(2025, 2, 1))["tax"] == 0
    w = tax.wait_for_ltcg(100000, 120000, date(2025, 1, 1), date(2025, 6, 1))
    assert w["tax_now"] == 4000 and w["tax_after_ltcg"] == 0
    assert w["break_even_fall_pct"] == pytest.approx(4000 / 120000 * 100, abs=0.01)
    assert "assumption" in tax.hypothetical_position(100, date(2025, 6, 1))


def test_mechanical_score_bands_and_missing_pillars():
    great = me.MechInputs(roe_pct=25, roce_pct=25, roe_years_above_15=True, cash_conversion=1.0,
                          other_income_pct_pbt=5, premium_to_iv_pct=-30, peg=0.8, rev_cagr_pct=20,
                          eps_cagr_pct=25, debt_to_equity=0.1, net_debt_to_ebitda=0, interest_coverage=10,
                          weinstein_stage="Stage 2", above_200dma=True, rsi=55)
    v = me.build_mechanical(great)
    assert v.score == 100 and v.stars == 5.0 and v.action == "STRONG BUY"
    poor = me.build_mechanical(me.MechInputs(roe_pct=5, premium_to_iv_pct=60, weinstein_stage="Stage 4"))
    assert poor.action == "AVOID"
    empty = me.build_mechanical(me.MechInputs())
    assert empty.action == "HOLD" and empty.score == 0  # nothing to judge -> never AVOID
    assert me.action_for(95, 5) == "SELL"
    assert me.action_for(45, 0) == "HOLD" and me.action_for(35, 0) == "REDUCE"


def test_veto_and_clamp():
    from wealth_advisor.domain.stock360.models import MechanicalVerdict as MV

    assert me.should_veto(MV(action="AVOID"))[0] is True
    assert me.should_veto(MV(action="BUY", sell_signals=5, evaluable_legends=6)) == (True, "5/6 legend sell signals")
    assert me.should_veto(MV(action="BUY", sell_signals=2))[0] is False
    assert me.clamp_action("BUY", "ACCUMULATE") == ("BUY", "")
    a, why = me.clamp_action("STRONG BUY", "HOLD")
    assert a == "ACCUMULATE" and "clamped" in why
    assert me.clamp_action("nonsense", "HOLD")[0] == "HOLD"


def test_build_mechanical_counts_legend_signals():
    from wealth_advisor.data.stocks.provider import StockFinancialYear as FY

    rows = [FY(symbol="X", fiscal_year=2022 + i, period_end=f"{2022 + i}-03-31", revenue_cr=1000,
               ebitda_cr=200, ebit_cr=180, pat_cr=150, net_worth_cr=800, total_debt_cr=100,
               total_assets_cr=1500, current_assets_cr=600, current_liabilities_cr=300) for i in range(4)]
    ctx = me.TriggerContext(cmp=400, rows=rows, composite_iv=100, graham_number=120, peg=3.0)
    v = me.build_mechanical(me.MechInputs(roe_pct=18), ctx)
    assert v.legend_verdicts["Buffett"] == "REDUCE" and v.legend_verdicts["Graham"] == "SELL"
    assert v.sell_signals >= 3 and v.evaluable_legends >= 3
