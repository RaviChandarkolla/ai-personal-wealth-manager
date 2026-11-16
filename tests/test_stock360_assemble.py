import sqlite3
from datetime import date

from wealth_advisor.data.stocks.report_data import ReportDataStore, ensure_report_data
from wealth_advisor.data.stocks.synthetic import SyntheticStockProvider
from wealth_advisor.domain.stock360.assemble import ReportContext, assemble
from wealth_advisor.domain.stock360.template import SECTIONS


def _ctx(sym="TCS", **kw):
    p = SyntheticStockProvider()
    st = ReportDataStore(sqlite3.connect(":memory:"))
    ensure_report_data(st, p, sym)
    return ReportContext(
        symbol=sym, snapshot=p.get_snapshot(sym), annual=p.get_financials(sym),
        quarterly=st.get_quarterly(sym), bars=st.get_daily(sym), index=st.get_index("^NSEI"),
        actions=st.get_actions(sym), today=date(2025, 6, 1), **kw)


def test_assembles_all_18_sections_in_order():
    a = assemble(_ctx())
    assert [s.id for s in a.sections] == [s.id for s in SECTIONS]
    ids = [m.id for s in a.sections for m in s.metrics]
    assert len(ids) == len(set(ids))  # citable ids are unique
    s4 = next(s for s in a.sections if s.id == "s4")
    assert any(m.id.endswith("composite_iv") for m in s4.metrics)
    assert a.mech_inputs is not None and 0 <= a.score <= 100


def test_missing_data_is_explicit_not_guessed():
    a = assemble(_ctx())
    s1 = next(s for s in a.sections if s.id == "s1")
    fii = next(m for m in s1.metrics if m.id.endswith("fii_pct"))
    assert fii.status == "requires_live_verification" and fii.value is None


def test_manual_input_is_used_and_labelled():
    ctx = _ctx()
    ctx.manual = {"fii_pct": {"value": 12.5, "as_of": "2025-03-31", "source_url": "https://x.com"}}
    a = assemble(ctx)
    fii = next(m for s in a.sections for m in s.metrics if m.id.endswith("s1:fii_pct"))
    assert fii.status == "manual" and fii.value == 12.5


def test_hypothetical_tax_is_labelled_and_holding_overrides():
    a = assemble(_ctx())
    t = next(t for t in next(s for s in a.sections if s.id == "s17").tables if t.title.startswith("Tax"))
    assert any("ASSUMPTION" in str(r[1]) for r in t.rows)
    b = assemble(_ctx(holding={"avg_cost": 1000, "buy_date": "2024-01-01", "qty": 10}))
    t2 = next(t for t in next(s for s in b.sections if s.id == "s17").tables if t.title.startswith("Tax"))
    assert any("user-provided" in str(r[1]) for r in t2.rows)


def test_no_price_history_does_not_crash():
    ctx = _ctx()
    ctx.bars, ctx.index = [], []
    a = assemble(ctx)
    assert len(a.sections) == 18


def test_new_manual_inputs_surface_as_manual_metrics_and_table():
    ctx = _ctx()
    d = "2025-03-31"
    ctx.manual = {
        "consensus_eps_fy1": {"value": 150.0, "as_of": d, "source_url": ""},
        "buy_count": {"value": 20, "as_of": d, "source_url": ""},
        "hold_count": {"value": 5, "as_of": d, "source_url": ""},
        "sell_count": {"value": 2, "as_of": d, "source_url": ""},
        "target_price_mean": {"value": 4500.0, "as_of": d, "source_url": ""},
        "guidance": {"value": "Mgmt guides double-digit growth", "as_of": d, "source_url": ""},
        "shareholding_history": {"value": [{"quarter": "Q1", "promoter_pct": 70.0}], "as_of": d, "source_url": ""},
    }
    a = assemble(ctx)
    mets = {m.id.rsplit(":", 1)[1]: m for s in a.sections for m in s.metrics}
    for k, v in (("consensus_eps_fy1", 150.0), ("buy_count", 20), ("target_price_mean", 4500.0)):
        assert mets[k].status == "manual" and mets[k].value == v
    assert any("Shareholding by quarter" in t.title for s in a.sections for t in s.tables)
    cons = a.values["charts"]["consensus"]
    assert cons["buy_count"] == 20 and cons["target_price_mean"] == 4500.0


def test_chart_data_is_structured_and_json_light():
    charts = assemble(_ctx()).values["charts"]
    price = charts["price"]
    n = len(price["dates"])
    assert 20 <= n <= 120
    assert len(price["close"]) == n and len(price["ema50"]) == n and len(price["ema200"]) == n
    assert price["levels"]
    assert "consensus" not in charts  # nothing fetched, nothing invented
