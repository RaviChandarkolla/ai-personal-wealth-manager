import pytest

from wealth_advisor.data.stocks.provider import StockFinancialYear as FY
from wealth_advisor.data.stocks.synthetic import SyntheticStockProvider
from wealth_advisor.domain.stock360 import adapters as ad
from wealth_advisor.domain.stock360 import fundamentals as f
from wealth_advisor.domain.stock360 import market as m


def fy(year, **kw):
    return FY(symbol="X", fiscal_year=year, period_end=f"{year}-03-31", **kw)


def test_cagr_growth_and_nones():
    assert f.cagr(100, 133.1, 3) == pytest.approx(10.0, abs=0.01)
    assert f.cagr(-1, 5, 3) is None and f.growth_pct(0, 5) is None


def test_balance_and_working_capital():
    r = fy(2025, revenue_cr=1000, ebitda_cr=200, receivables_cr=100, inventory_cr=80,
           payables_cr=60, total_debt_cr=300, cash_and_equivalents_cr=100,
           current_assets_cr=400, current_liabilities_cr=200)
    assert f.net_debt(r) == 200 and f.net_debt_to_ebitda(r) == 1.0
    assert f.receivable_days(r) == pytest.approx(36.5)
    assert f.inventory_days(r) == pytest.approx(80 / 800 * 365)
    assert f.cash_conversion_cycle(r) == pytest.approx(36.5 + 36.5 - 27.375)
    assert f.quick_ratio(r) == pytest.approx(1.6)
    assert f.net_debt(fy(2025)) is None


def test_cash_conversion_ratio_needs_all_years():
    rows = [fy(2023, cfo_cr=60, ebitda_cr=100), fy(2024, cfo_cr=70, ebitda_cr=100),
            fy(2025, cfo_cr=80, ebitda_cr=100)]
    assert f.cash_conversion_ratio(rows) == pytest.approx(0.7)
    rows[0].cfo_cr = None
    assert f.cash_conversion_ratio(rows) is None


def test_returns_and_forensics():
    r = fy(2025, ebit_cr=200, pat_cr=150, net_worth_cr=1000, total_assets_cr=2000,
           current_liabilities_cr=500, total_debt_cr=200, cash_and_equivalents_cr=100,
           tax_cr=50, pbt_cr=200, other_income_cr=40, revenue_cr=1000)
    assert f.roe_pct(r) == 15 and f.roa_pct(r) == 7.5
    assert f.roce_pct(r) == pytest.approx(200 / 1500 * 100)
    assert f.roic_pct(r) == pytest.approx(200 * 0.75 / 1100 * 100)
    assert f.other_income_pct_pbt(r) == 20 and f.asset_turnover(r) == 0.5


def test_adapter_choice_and_cycle():
    assert ad.pick_adapter("Financial Services", "Private Sector Bank", True) == "bfsi"
    assert ad.pick_adapter("Automobile", "Commercial Vehicles") == "cyclical"
    assert ad.pick_adapter("Information Technology") == "general"
    rows = [fy(2021 + i, revenue_cr=100, ebitda_cr=e, eps=e / 10) for i, e in enumerate([10, 12, 14, 11, 20])]
    assert ad.cycle_position(rows)[0] == "Peak"
    assert ad.cycle_position(rows[:2]) == ("Insufficient history", None)
    assert ad.normalized_eps(rows) == pytest.approx(1.34)


def test_market_metrics_synthetic():
    p = SyntheticStockProvider()
    bars, _ = p.fetch_daily_ohlcv("TCS")
    idx = p.fetch_index_daily()
    ms = {x.id.split(":", 1)[1]: x for x in m.market_metrics("TCS", bars, idx)}
    assert ms["s1:cmp"].value == bars[-1].close
    assert ms["s1:high52"].value >= ms["s1:cmp"].value >= ms["s1:low52"].value
    assert ms["s1:ret_1y"].value is not None
    assert ms["s1:ret_3y"].status == "unavailable"  # only 2y fetched -> not extrapolated
    assert ms["s1:rs_1y"].value is not None and ms["s1:beta"].value is not None
    assert m.market_metrics("TCS", [], idx)[0].status == "unavailable"


def test_beta_of_index_is_one():
    closes = [100 * 1.001 ** i * (1 + 0.01 * (i % 3)) for i in range(300)]
    assert m.beta(closes, closes) == pytest.approx(1.0)
