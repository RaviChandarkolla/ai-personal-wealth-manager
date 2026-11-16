from wealth_advisor.data.stocks.provider import StockFinancialYear as FY
from wealth_advisor.domain.stock360 import checklists as c


def fy(year, **kw):
    return FY(symbol="X", fiscal_year=year, period_end=f"{year}-03-31", **kw)


def good_rows():
    return [fy(2022 + i, revenue_cr=1000, ebitda_cr=200, ebit_cr=180, pat_cr=150, net_worth_cr=800,
               total_debt_cr=100, total_assets_cr=1500, current_assets_cr=600,
               current_liabilities_cr=300, eps=10) for i in range(4)]


def by_trigger(ts, text):
    return next(t for t in ts if text in t.trigger)


def test_buffett_checklist_items():
    items = dict(c.buffett_checklist(good_rows(), 12, 20, None))
    assert items["ROE > 15% in every year available"] is True
    assert items["Net debt/equity < 0.5"] is True
    assert items["Operating margin above peer median"] is None
    assert dict(c.buffett_checklist(good_rows(), 5, -3))["EPS CAGR > 10%"] is False


def test_lynch_category():
    rows = good_rows()
    assert c.lynch_category(5, False, rows) == "Slow grower"
    assert c.lynch_category(12, False, rows) == "Stalwart"
    assert c.lynch_category(30, False, rows) == "Fast grower"
    assert c.lynch_category(30, True, rows) == "Cyclical"
    assert c.lynch_category(None, False, rows) == "Requires judgement"
    tr = [fy(2022, pat_cr=-50), fy(2023, pat_cr=10), fy(2024, pat_cr=80)]
    assert c.lynch_category(30, False, tr) == "Turnaround"


def test_graham_and_buffett_valuation_triggers():
    ctx = c.TriggerContext(cmp=300, rows=good_rows(), composite_iv=150, graham_number=190, peg=1.0)
    ts = c.evaluate_triggers(ctx)
    assert by_trigger(ts, "absurdly").fired is True
    assert by_trigger(ts, "at or above Graham").fired is True
    assert by_trigger(ts, "1.5 x Graham").fired is True
    assert by_trigger(ts, "Margin of safety gone").fired is True
    assert by_trigger(ts, "PEG > 2.0").fired is False


def test_missing_data_is_not_evaluable_not_hold():
    ts = c.evaluate_triggers(c.TriggerContext(cmp=100, rows=[]))
    v = c.legend_verdicts(ts)
    assert v["Fisher"] == "NOT EVALUABLE" and v["Buffett"] == "NOT EVALUABLE"
    assert c.sell_signal_count(v) == (0, 0)


def test_judgement_triggers_never_fire():
    ts = c.evaluate_triggers(c.TriggerContext(cmp=100, rows=good_rows()))
    assert all(t.fired is None for t in ts if t.kind in ("J", "W"))


def test_livermore_and_market_phase():
    closes = [100 + i * 0.1 for i in range(250)]
    closes += [closes[-1] - i * 2 for i in range(1, 20)]  # sharp selloff
    vols = [100.0] * (len(closes) - 20) + [500.0 if i % 2 else 50.0 for i in range(20)]
    vols[-19:] = [500.0] * 19
    ctx = c.TriggerContext(cmp=closes[-1], rows=[], closes=closes, volumes=vols,
                           nifty_below_200dma=True, rs_3m=-5, sector_rs_3m=3)
    ts = c.evaluate_triggers(ctx)
    assert by_trigger(ts, "Below 50-DMA").fired is True
    assert by_trigger(ts, "Pivot broken").fired is True
    assert by_trigger(ts, "RS divergence").fired is True
    assert by_trigger(ts, "bear phase").fired is True
    assert by_trigger(ts, "Extended").fired is False


def test_verdict_rules_and_consensus():
    ts = [c.LegendTrigger(legend="Graham", trigger="a", kind="D", fired=True),
          c.LegendTrigger(legend="Graham", trigger="b", kind="D", fired=True),
          c.LegendTrigger(legend="Lynch", trigger="a", kind="D", fired=True),
          c.LegendTrigger(legend="Munger", trigger="a", kind="D", fired=False)]
    v = c.legend_verdicts(ts)
    assert (v["Graham"], v["Lynch"], v["Munger"], v["Fisher"]) == ("SELL", "REDUCE", "HOLD", "NOT EVALUABLE")
    assert c.sell_signal_count(v) == (2, 3)
    assert [c.consensus_action(n) for n in (0, 1, 2, 3, 4, 5, 7)] == \
        ["ADD", "HOLD", "HOLD", "REDUCE", "REDUCE", "SELL", "SELL"]
