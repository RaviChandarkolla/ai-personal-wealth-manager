import math

import pytest

from wealth_advisor.data.stocks.provider import StockFinancialYear as FY
from wealth_advisor.data.stocks.provider import StockSnapshot
from wealth_advisor.domain.stock360 import decomposition as d
from wealth_advisor.domain.stock360 import projections as pj
from wealth_advisor.domain.stock360 import valuation as v


def fy(year, **kw):
    return FY(symbol="X", fiscal_year=year, period_end=f"{year}-03-31", **kw)


def test_multiples_and_none_rules():
    assert v.pe(200, 10) == 20 and v.pe(200, -1) is None
    assert v.pb(200, 50) == 4 and v.ps(1000, 500) == 2 and v.ev_ebitda(900, 100) == 9
    assert v.peg(20, 10) == 2 and v.peg(20, 0) is None
    assert v.peg_band(0.8).startswith("<1.0") and v.peg_band(2.5).startswith(">2.0")
    assert v.dividend_yield_pct(5, 100) == 5


def test_sustainable_growth_capped_by_roe_retention():
    rows = [fy(2025, pat_cr=100, net_worth_cr=1000, dividends_paid_cr=50)]
    assert v.sustainable_growth_pct(rows, 25) == pytest.approx(5.0)  # 10% ROE x 50% retained
    assert v.sustainable_growth_pct(rows, None) is None


def test_own_history_uses_fy_end_prices_and_labels_years():
    rows = [fy(2022, eps=10, bvps=50), fy(2023, eps=12, bvps=60), fy(2024, eps=15, bvps=70)]
    prices = [("2023-03-30", 240.0), ("2024-03-28", 450.0), ("2024-06-01", 500.0)]
    h = v.own_history_multiples(rows, prices)
    assert [p[0] for p in h["pe"]] == ["2023-03-31", "2024-03-31"]  # 2022 predates history
    assert h["pe"][0][1] == 20 and h["pe"][1][1] == 30
    med, label = v.history_median(h["pe"])
    assert med == 25 and "2" in label
    assert v.history_median([]) == (None, "no history")


def test_peers_and_verdict():
    def snap(sym, cap, pe_):
        return StockSnapshot(symbol=sym, market_cap_cr=cap, pe=pe_)

    peers = [snap("A", 100, 10), snap("B", 500, 20), snap("C", 300, 30), snap("D", 200, 40), snap("S", 900, 5)]
    top, med = v.peer_table("S", peers)
    assert [p.symbol for p in top] == ["B", "C", "D"] and med["pe"] == 25
    assert v.peer_table("S", peers[:2])[1]["pe"] is None  # too few peers
    assert v.overvaluation_verdict(150, 100)[0] == "Overvalued"
    assert v.overvaluation_verdict(100, 100)[0] == "Fairly valued"
    assert v.overvaluation_verdict(50, 100)[2] == 50
    assert v.overvaluation_verdict(50, None)[0] == "Not available"


def test_decomposition_parts_add_to_price_return():
    r = d.decompose_return(100, 250, 10, 15)
    assert r["price_return_pct"] == pytest.approx(150)
    assert r["from_eps_pct"] + r["from_rerating_pct"] == pytest.approx(150)
    assert (1 + r["eps_growth_pct"] / 100) * (1 + r["pe_change_pct"] / 100) - 1 == pytest.approx(1.5)
    assert d.decompose_return(100, 250, -1, 15) is None
    assert math.isclose(d.decompose_return(100, 100, 10, 12)["from_eps_pct"], 0.0)
    assert d.mean_reversion_price(10, 22) == 220 and d.mean_reversion_price(-1, 22) is None
    assert d.mean_reversion_price_ev(100, 10, 200, 10) == 80


def test_projections_clamp_fade_and_assumptions():
    rows = [fy(2022 + i, revenue_cr=r_, ebitda_cr=r_ * 0.2, pat_cr=r_ * 0.1, eps=r_ * 0.01)
            for i, r_ in enumerate([100, 150, 225, 337.5])]  # 50% CAGR -> clamped to 25
    ps = pj.project(rows)
    assert ps.assumptions["applied_growth_pct_year1"] == 25.0
    assert ps.rows[0].revenue_cr == pytest.approx(337.5 * 1.25, rel=1e-3)
    assert ps.rows[1].revenue_cr / ps.rows[0].revenue_cr == pytest.approx(1 + 0.225, rel=1e-3)
    assert ps.rows[0].opm_pct == pytest.approx(20.0)
    assert [r.year_label for r in ps.rows] == ["FY+1", "FY+2", "FY+3"]
    assert pj.project(rows[:2]) is None
