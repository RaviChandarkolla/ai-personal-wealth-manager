"""Unit tests for stock fundamental ratio and CAGR calculations (FA Course Notes)."""

from __future__ import annotations

import pytest
from wealth_advisor.data.stocks.provider import StockFinancialYear, StockSnapshot
from wealth_advisor.domain.stock_ratios import (
    ExpertAcademyStatements,
    compute_cagr,
    compute_stock_ratios,
)


def test_expert_academy_statements_worked_example():
    """Verify Expert Academy P&L identities from FA Course Notes Section 3."""
    ea = ExpertAcademyStatements()
    assert ea.total_income == 600_000.0
    assert ea.ebitda == 360_000.0
    assert ea.ebit == 270_000.0
    assert ea.total_expenses == 390_000.0
    assert ea.pbt == 210_000.0
    assert ea.tax == 21_000.0
    assert ea.pat == 189_000.0


def test_expert_academy_cagr_worked_example():
    """Verify 4Y CAGR worked example from FA Course Notes Section 4.1."""
    # Revenue: FY21 = 550 -> FY25 = 1000 => ~16.10% (0.161)
    rev_cagr = compute_cagr(550.0, 1000.0, 4)
    assert rev_cagr is not None
    assert rev_cagr * 100.0 == pytest.approx(16.1, abs=0.1)

    # PAT: FY21 = 55 -> FY25 = 120 => ~21.50% (0.215)
    pat_cagr = compute_cagr(55.0, 120.0, 4)
    assert pat_cagr is not None
    assert pat_cagr * 100.0 == pytest.approx(21.5, abs=0.1)

    # EPS: FY21 = 5.5 -> FY25 = 12.0 => ~21.50% (0.215)
    eps_cagr = compute_cagr(5.5, 12.0, 4)
    assert eps_cagr is not None
    assert eps_cagr * 100.0 == pytest.approx(21.5, abs=0.1)


def test_compute_stock_ratios_5y_and_latest_fy():
    """Verify 5-year ratios and latest FY ratios match FA Course Notes formulas."""
    financials = [
        StockFinancialYear(
            symbol="EXPERT",
            fiscal_year=2021,
            revenue_cr=550.0,
            ebitda_cr=120.0,
            ebit_cr=100.0,
            interest_cr=20.0,
            pat_cr=55.0,
            eps=5.5,
            dps=1.0,
            bvps=35.0,
            total_assets_cr=600.0,
            total_debt_cr=200.0,
            net_worth_cr=350.0,
            current_assets_cr=210.0,
            current_liabilities_cr=120.0,
            inventory_cr=70.0,
            receivables_cr=60.0,
            payables_cr=45.0,
            cfo_cr=60.0,
            capex_cr=25.0,
            fcf_cr=35.0,
            dividends_paid_cr=10.0,
        ),
        StockFinancialYear(
            symbol="EXPERT",
            fiscal_year=2022,
            revenue_cr=640.0,
            ebitda_cr=145.0,
            ebit_cr=122.0,
            interest_cr=22.0,
            pat_cr=68.0,
            eps=6.8,
            dps=1.2,
            bvps=40.0,
            total_assets_cr=680.0,
            total_debt_cr=220.0,
            net_worth_cr=400.0,
            current_assets_cr=240.0,
            current_liabilities_cr=135.0,
            inventory_cr=80.0,
            receivables_cr=70.0,
            payables_cr=50.0,
            cfo_cr=75.0,
            capex_cr=30.0,
            fcf_cr=45.0,
            dividends_paid_cr=12.0,
        ),
        StockFinancialYear(
            symbol="EXPERT",
            fiscal_year=2023,
            revenue_cr=750.0,
            ebitda_cr=170.0,
            ebit_cr=145.0,
            interest_cr=25.0,
            pat_cr=82.0,
            eps=8.2,
            dps=1.5,
            bvps=46.0,
            total_assets_cr=780.0,
            total_debt_cr=250.0,
            net_worth_cr=460.0,
            current_assets_cr=275.0,
            current_liabilities_cr=155.0,
            inventory_cr=95.0,
            receivables_cr=82.0,
            payables_cr=58.0,
            cfo_cr=90.0,
            capex_cr=35.0,
            fcf_cr=55.0,
            dividends_paid_cr=15.0,
        ),
        StockFinancialYear(
            symbol="EXPERT",
            fiscal_year=2024,
            revenue_cr=870.0,
            ebitda_cr=200.0,
            ebit_cr=172.0,
            interest_cr=28.0,
            pat_cr=100.0,
            eps=10.0,
            dps=1.8,
            bvps=53.0,
            total_assets_cr=890.0,
            total_debt_cr=275.0,
            net_worth_cr=530.0,
            current_assets_cr=310.0,
            current_liabilities_cr=175.0,
            inventory_cr=108.0,
            receivables_cr=95.0,
            payables_cr=65.0,
            cfo_cr=110.0,
            capex_cr=40.0,
            fcf_cr=70.0,
            dividends_paid_cr=18.0,
        ),
        StockFinancialYear(
            symbol="EXPERT",
            fiscal_year=2025,
            revenue_cr=1000.0,
            ebitda_cr=230.0,
            ebit_cr=200.0,
            interest_cr=30.0,
            pat_cr=120.0,
            eps=12.0,
            dps=2.4,
            bvps=60.0,
            total_assets_cr=1000.0,
            total_debt_cr=300.0,
            net_worth_cr=600.0,
            current_assets_cr=350.0,
            current_liabilities_cr=200.0,
            inventory_cr=120.0,
            receivables_cr=110.0,
            payables_cr=75.0,
            cfo_cr=135.0,
            capex_cr=45.0,
            fcf_cr=90.0,
            dividends_paid_cr=24.0,
        ),
    ]
    snapshot = StockSnapshot(
        symbol="EXPERT",
        as_of="2026-04-06",
        price=240.0,
        market_cap_cr=2400.0,
        pe=20.0,
        pb=4.0,
        ps=2.4,
        ev_ebitda=11.0,
        dividend_yield=0.01,
    )

    ratios = compute_stock_ratios(snapshot, financials, is_bank=False)
    assert ratios.revenue_cagr_5y is not None
    assert ratios.revenue_cagr_5y * 100.0 == pytest.approx(16.1, abs=0.1)
    assert ratios.pat_cagr_5y is not None
    assert ratios.pat_cagr_5y * 100.0 == pytest.approx(21.5, abs=0.1)
    assert ratios.eps_cagr_5y is not None
    assert ratios.eps_cagr_5y * 100.0 == pytest.approx(21.5, abs=0.1)

    # FY25 ratios:
    # EBITDA Margin = 230 / 1000 = 0.23
    assert ratios.ebitda_margin == pytest.approx(0.23, abs=0.005)
    # PAT Margin = 120 / 1000 = 0.12
    assert ratios.pat_margin == pytest.approx(0.12, abs=0.005)
    # ROE = 120 / 600 = 0.20
    assert ratios.roe == pytest.approx(0.20, abs=0.005)
    # ROCE = 200 / (1000 - 200) = 0.25
    assert ratios.roce == pytest.approx(0.25, abs=0.005)
    # D/E = 300 / 600 = 0.50
    assert ratios.debt_to_equity == pytest.approx(0.50, abs=0.01)
    # Interest Coverage = 200 / 30 = 6.67x
    assert ratios.interest_coverage == pytest.approx(6.67, abs=0.05)
    # Current Ratio = 350 / 200 = 1.75x
    assert ratios.current_ratio == pytest.approx(1.75, abs=0.01)
    # Quick Ratio = (350 - 120) / 200 = 1.15x
    assert ratios.quick_ratio == pytest.approx(1.15, abs=0.01)
    # Asset Turnover = 1000 / 1000 = 1.00x
    assert ratios.asset_turnover == pytest.approx(1.00, abs=0.01)
    #Dividends increasing: 1.0 -> 1.2 -> 1.5 -> 1.8 -> 2.4 => True
    assert ratios.dividends_increasing is True


def test_cagr_handles_negative_or_zero_base():
    """Verify CAGR returns None when start or end value is <= 0."""
    assert compute_cagr(-10.0, 100.0, 4) is None
    assert compute_cagr(0.0, 100.0, 4) is None
    assert compute_cagr(100.0, -5.0, 4) is None
    assert compute_cagr(100.0, 150.0, 0) is None
