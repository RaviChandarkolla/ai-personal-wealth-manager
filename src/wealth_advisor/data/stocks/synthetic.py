"""Deterministic synthetic stock data provider for offline tests and synthetic mode."""

from __future__ import annotations

import math
import random
from datetime import date, timedelta
from typing import Any

from .provider import (
    CorporateAction,
    StockDataProvider,
    StockFinancialYear,
    StockMasterItem,
    StockOHLCV,
    StockPricePoint,
    StockSnapshot,
)

DEFAULT_AS_OF = date(2026, 10, 9)

# Base profiles for 21 NSE stocks across 6 sectors + 1 weak stock
_STOCK_PROFILES: dict[str, dict[str, Any]] = {
    "TCS": {
        "name": "Tata Consultancy Services Limited",
        "isin": "INE467B01029",
        "sector": "Information Technology",
        "industry": "IT Services & Consulting",
        "is_bank": False,
        "cmp": 3920.0,
        "market_cap_cr": 1418000.0,
        "pe": 25.8,
        "pb": 13.5,
        "ps": 5.6,
        "ev_ebitda": 18.2,
        "roe": 51.5,
        "roa": 31.2,
        "debt_to_equity": 0.08,
        "current_ratio": 2.45,
        "dividend_yield": 1.65,
        "promoter_holding_pct": 71.77,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 980.0,
        "beta": 0.78,
        "insider_net_buy_lakhs": 45.0,
        "rev_base": 164177.0,
        "rev_cagr": 0.115,
        "pat_margin": 0.215,
        "ebitda_margin": 0.268,
        "cfo_pat_ratio": 1.12,
        "capex_rev_ratio": 0.022,
        "div_payout": 0.42,
        "shares_cr": 361.8,
    },
    "INFY": {
        "name": "Infosys Limited",
        "isin": "INE009A01021",
        "sector": "Information Technology",
        "industry": "IT Services & Consulting",
        "is_bank": False,
        "cmp": 1898.0,
        "market_cap_cr": 788000.0,
        "pe": 24.4,
        "pb": 8.9,
        "ps": 4.9,
        "ev_ebitda": 16.5,
        "roe": 31.8,
        "roa": 19.8,
        "debt_to_equity": 0.11,
        "current_ratio": 2.54,
        "dividend_yield": 2.15,
        "promoter_holding_pct": 14.78,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 1150.0,
        "beta": 0.85,
        "insider_net_buy_lakhs": 25.0,
        "rev_base": 100472.0,
        "rev_cagr": 0.125,
        "pat_margin": 0.193,
        "ebitda_margin": 0.245,
        "cfo_pat_ratio": 1.20,
        "capex_rev_ratio": 0.024,
        "div_payout": 0.48,
        "shares_cr": 415.2,
    },
    "HCLTECH": {
        "name": "HCL Technologies Limited",
        "isin": "INE860A01027",
        "sector": "Information Technology",
        "industry": "IT Services & Consulting",
        "is_bank": False,
        "cmp": 1640.0,
        "market_cap_cr": 445000.0,
        "pe": 25.2,
        "pb": 6.4,
        "ps": 3.9,
        "ev_ebitda": 15.8,
        "roe": 24.8,
        "roa": 16.5,
        "debt_to_equity": 0.09,
        "current_ratio": 2.30,
        "dividend_yield": 3.10,
        "promoter_holding_pct": 60.81,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 540.0,
        "beta": 0.82,
        "insider_net_buy_lakhs": 15.0,
        "rev_base": 75379.0,
        "rev_cagr": 0.112,
        "pat_margin": 0.158,
        "ebitda_margin": 0.222,
        "cfo_pat_ratio": 1.18,
        "capex_rev_ratio": 0.025,
        "div_payout": 0.52,
        "shares_cr": 271.3,
    },
    "WIPRO": {
        "name": "Wipro Limited",
        "isin": "INE075A01022",
        "sector": "Information Technology",
        "industry": "IT Services & Consulting",
        "is_bank": False,
        "cmp": 540.0,
        "market_cap_cr": 282000.0,
        "pe": 27.5,
        "pb": 3.6,
        "ps": 3.1,
        "ev_ebitda": 17.4,
        "roe": 15.4,
        "roa": 10.2,
        "debt_to_equity": 0.22,
        "current_ratio": 2.15,
        "dividend_yield": 1.10,
        "promoter_holding_pct": 72.88,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 390.0,
        "beta": 0.88,
        "insider_net_buy_lakhs": 0.0,
        "rev_base": 61935.0,
        "rev_cagr": 0.095,
        "pat_margin": 0.135,
        "ebitda_margin": 0.195,
        "cfo_pat_ratio": 1.08,
        "capex_rev_ratio": 0.028,
        "div_payout": 0.25,
        "shares_cr": 522.2,
    },
    "HDFCBANK": {
        "name": "HDFC Bank Limited",
        "isin": "INE040A01034",
        "sector": "Financial Services",
        "industry": "Private Sector Bank",
        "is_bank": True,
        "cmp": 1680.0,
        "market_cap_cr": 1280000.0,
        "pe": 18.4,
        "pb": 2.75,
        "ps": 4.5,
        "ev_ebitda": None,
        "roe": 16.8,
        "roa": 1.92,
        "debt_to_equity": 1.15,
        "current_ratio": None,
        "dividend_yield": 1.16,
        "promoter_holding_pct": 0.0,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 2450.0,
        "beta": 0.92,
        "insider_net_buy_lakhs": 85.0,
        "rev_base": 145000.0,
        "rev_cagr": 0.145,
        "pat_margin": 0.265,
        "ebitda_margin": 0.35,
        "cfo_pat_ratio": 1.15,
        "capex_rev_ratio": 0.015,
        "div_payout": 0.22,
        "shares_cr": 761.9,
        "nim_pct": 3.65,
        "gnpa_pct": 1.24,
        "nnpa_pct": 0.33,
        "casa_pct": 42.5,
        "car_pct": 18.8,
        "credit_cost_pct": 0.55,
        "provision_coverage_pct": 74.0,
    },
    "ICICIBANK": {
        "name": "ICICI Bank Limited",
        "isin": "INE090A01021",
        "sector": "Financial Services",
        "industry": "Private Sector Bank",
        "is_bank": True,
        "cmp": 1245.0,
        "market_cap_cr": 875000.0,
        "pe": 19.2,
        "pb": 3.35,
        "ps": 4.8,
        "ev_ebitda": None,
        "roe": 18.4,
        "roa": 2.18,
        "debt_to_equity": 1.10,
        "current_ratio": None,
        "dividend_yield": 0.80,
        "promoter_holding_pct": 0.0,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 1850.0,
        "beta": 0.98,
        "insider_net_buy_lakhs": 40.0,
        "rev_base": 98000.0,
        "rev_cagr": 0.162,
        "pat_margin": 0.275,
        "ebitda_margin": 0.36,
        "cfo_pat_ratio": 1.18,
        "capex_rev_ratio": 0.014,
        "div_payout": 0.18,
        "shares_cr": 702.8,
        "nim_pct": 4.40,
        "gnpa_pct": 2.15,
        "nnpa_pct": 0.42,
        "casa_pct": 44.2,
        "car_pct": 16.7,
        "credit_cost_pct": 0.45,
        "provision_coverage_pct": 80.5,
    },
    "KOTAKBANK": {
        "name": "Kotak Mahindra Bank Limited",
        "isin": "INE237A01028",
        "sector": "Financial Services",
        "industry": "Private Sector Bank",
        "is_bank": True,
        "cmp": 1820.0,
        "market_cap_cr": 362000.0,
        "pe": 19.6,
        "pb": 2.85,
        "ps": 4.6,
        "ev_ebitda": None,
        "roe": 15.2,
        "roa": 2.25,
        "debt_to_equity": 0.95,
        "current_ratio": None,
        "dividend_yield": 0.12,
        "promoter_holding_pct": 25.91,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 920.0,
        "beta": 0.89,
        "insider_net_buy_lakhs": 10.0,
        "rev_base": 52000.0,
        "rev_cagr": 0.142,
        "pat_margin": 0.268,
        "ebitda_margin": 0.35,
        "cfo_pat_ratio": 1.10,
        "capex_rev_ratio": 0.015,
        "div_payout": 0.08,
        "shares_cr": 198.9,
        "nim_pct": 4.95,
        "gnpa_pct": 1.39,
        "nnpa_pct": 0.34,
        "casa_pct": 47.8,
        "car_pct": 20.5,
        "credit_cost_pct": 0.48,
        "provision_coverage_pct": 76.0,
    },
    "SBIN": {
        "name": "State Bank of India",
        "isin": "INE062A01020",
        "sector": "Financial Services",
        "industry": "Public Sector Bank",
        "is_bank": True,
        "cmp": 815.0,
        "market_cap_cr": 727000.0,
        "pe": 11.4,
        "pb": 1.78,
        "ps": 2.1,
        "ev_ebitda": None,
        "roe": 17.2,
        "roa": 1.08,
        "debt_to_equity": 1.45,
        "current_ratio": None,
        "dividend_yield": 1.68,
        "promoter_holding_pct": 57.54,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 1680.0,
        "beta": 1.15,
        "insider_net_buy_lakhs": 0.0,
        "rev_base": 295000.0,
        "rev_cagr": 0.138,
        "pat_margin": 0.175,
        "ebitda_margin": 0.25,
        "cfo_pat_ratio": 1.12,
        "capex_rev_ratio": 0.012,
        "div_payout": 0.20,
        "shares_cr": 892.5,
        "nim_pct": 3.28,
        "gnpa_pct": 2.24,
        "nnpa_pct": 0.57,
        "casa_pct": 41.1,
        "car_pct": 14.3,
        "credit_cost_pct": 0.62,
        "provision_coverage_pct": 75.0,
    },
    "AXISBANK": {
        "name": "Axis Bank Limited",
        "isin": "INE238A01034",
        "sector": "Financial Services",
        "industry": "Private Sector Bank",
        "is_bank": True,
        "cmp": 1165.0,
        "market_cap_cr": 360000.0,
        "pe": 14.2,
        "pb": 2.25,
        "ps": 3.4,
        "ev_ebitda": None,
        "roe": 16.5,
        "roa": 1.78,
        "debt_to_equity": 1.28,
        "current_ratio": None,
        "dividend_yield": 0.10,
        "promoter_holding_pct": 8.22,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 1120.0,
        "beta": 1.08,
        "insider_net_buy_lakhs": 12.0,
        "rev_base": 78000.0,
        "rev_cagr": 0.152,
        "pat_margin": 0.228,
        "ebitda_margin": 0.31,
        "cfo_pat_ratio": 1.09,
        "capex_rev_ratio": 0.015,
        "div_payout": 0.10,
        "shares_cr": 309.0,
        "nim_pct": 4.02,
        "gnpa_pct": 1.43,
        "nnpa_pct": 0.31,
        "casa_pct": 43.0,
        "car_pct": 16.6,
        "credit_cost_pct": 0.52,
        "provision_coverage_pct": 78.0,
    },
    "HINDUNILVR": {
        "name": "Hindustan Unilever Limited",
        "isin": "INE030A01027",
        "sector": "Fast Moving Consumer Goods",
        "industry": "Personal & Household Products",
        "is_bank": False,
        "cmp": 2740.0,
        "market_cap_cr": 644000.0,
        "pe": 58.0,
        "pb": 12.6,
        "ps": 10.4,
        "ev_ebitda": 40.5,
        "roe": 20.6,
        "roa": 14.2,
        "debt_to_equity": 0.03,
        "current_ratio": 1.65,
        "dividend_yield": 1.55,
        "promoter_holding_pct": 61.90,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 620.0,
        "beta": 0.62,
        "insider_net_buy_lakhs": 18.0,
        "rev_base": 45996.0,
        "rev_cagr": 0.102,
        "pat_margin": 0.172,
        "ebitda_margin": 0.238,
        "cfo_pat_ratio": 1.15,
        "capex_rev_ratio": 0.021,
        "div_payout": 0.65,
        "shares_cr": 235.0,
    },
    "ITC": {
        "name": "ITC Limited",
        "isin": "INE154A01025",
        "sector": "Fast Moving Consumer Goods",
        "industry": "Diversified FMCG",
        "is_bank": False,
        "cmp": 495.0,
        "market_cap_cr": 618000.0,
        "pe": 29.5,
        "pb": 8.4,
        "ps": 8.8,
        "ev_ebitda": 21.8,
        "roe": 28.4,
        "roa": 22.8,
        "debt_to_equity": 0.01,
        "current_ratio": 2.85,
        "dividend_yield": 2.78,
        "promoter_holding_pct": 0.0,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 790.0,
        "beta": 0.68,
        "insider_net_buy_lakhs": 30.0,
        "rev_base": 49272.0,
        "rev_cagr": 0.114,
        "pat_margin": 0.285,
        "ebitda_margin": 0.368,
        "cfo_pat_ratio": 1.10,
        "capex_rev_ratio": 0.032,
        "div_payout": 0.68,
        "shares_cr": 1248.5,
    },
    "NESTLEIND": {
        "name": "Nestle India Limited",
        "isin": "INE239A01024",
        "sector": "Fast Moving Consumer Goods",
        "industry": "Packaged Foods",
        "is_bank": False,
        "cmp": 2510.0,
        "market_cap_cr": 242000.0,
        "pe": 68.5,
        "pb": 62.0,
        "ps": 12.2,
        "ev_ebitda": 46.2,
        "roe": 88.5,
        "roa": 31.0,
        "debt_to_equity": 0.09,
        "current_ratio": 1.52,
        "dividend_yield": 1.25,
        "promoter_holding_pct": 62.76,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 310.0,
        "beta": 0.58,
        "insider_net_buy_lakhs": 5.0,
        "rev_base": 14709.0,
        "rev_cagr": 0.118,
        "pat_margin": 0.165,
        "ebitda_margin": 0.242,
        "cfo_pat_ratio": 1.12,
        "capex_rev_ratio": 0.038,
        "div_payout": 0.62,
        "shares_cr": 96.4,
    },
    "MARUTI": {
        "name": "Maruti Suzuki India Limited",
        "isin": "INE585B01010",
        "sector": "Automobile and Auto Components",
        "industry": "Passenger Cars & Utility Vehicles",
        "is_bank": False,
        "cmp": 12400.0,
        "market_cap_cr": 390000.0,
        "pe": 26.8,
        "pb": 4.5,
        "ps": 2.75,
        "ev_ebitda": 18.0,
        "roe": 16.8,
        "roa": 12.6,
        "debt_to_equity": 0.02,
        "current_ratio": 1.48,
        "dividend_yield": 1.01,
        "promoter_holding_pct": 58.19,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 680.0,
        "beta": 0.86,
        "insider_net_buy_lakhs": 20.0,
        "rev_base": 88295.0,
        "rev_cagr": 0.145,
        "pat_margin": 0.098,
        "ebitda_margin": 0.132,
        "cfo_pat_ratio": 1.18,
        "capex_rev_ratio": 0.045,
        "div_payout": 0.30,
        "shares_cr": 31.4,
    },
    "M&M": {
        "name": "Mahindra & Mahindra Limited",
        "isin": "INE101A01026",
        "sector": "Automobile and Auto Components",
        "industry": "Passenger Cars & Utility Vehicles",
        "is_bank": False,
        "cmp": 2920.0,
        "market_cap_cr": 363000.0,
        "pe": 25.4,
        "pb": 5.2,
        "ps": 2.6,
        "ev_ebitda": 16.2,
        "roe": 18.9,
        "roa": 11.4,
        "debt_to_equity": 0.18,
        "current_ratio": 1.55,
        "dividend_yield": 0.72,
        "promoter_holding_pct": 18.57,
        "pledge_pct": 0.05,
        "avg_daily_traded_value_cr": 740.0,
        "beta": 1.02,
        "insider_net_buy_lakhs": 15.0,
        "rev_base": 74277.0,
        "rev_cagr": 0.155,
        "pat_margin": 0.102,
        "ebitda_margin": 0.148,
        "cfo_pat_ratio": 1.14,
        "capex_rev_ratio": 0.042,
        "div_payout": 0.22,
        "shares_cr": 124.3,
    },
    "TATAMOTORS": {
        "name": "Tata Motors Limited",
        "isin": "INE155A01022",
        "sector": "Automobile and Auto Components",
        "industry": "Passenger Cars & Commercial Vehicles",
        "is_bank": False,
        "cmp": 935.0,
        "market_cap_cr": 344000.0,
        "pe": 28.2,
        "pb": 4.1,
        "ps": 0.92,
        "ev_ebitda": 8.4,
        "roe": 22.0,
        "roa": 7.8,
        "debt_to_equity": 0.65,
        "current_ratio": 1.18,
        "dividend_yield": 0.64,
        "promoter_holding_pct": 42.58,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 1120.0,
        "beta": 1.24,
        "insider_net_buy_lakhs": 0.0,
        "rev_base": 278454.0,
        "rev_cagr": 0.135,
        "pat_margin": 0.068,
        "ebitda_margin": 0.135,
        "cfo_pat_ratio": 1.25,
        "capex_rev_ratio": 0.055,
        "div_payout": 0.15,
        "shares_cr": 367.9,
    },
    "SUNPHARMA": {
        "name": "Sun Pharmaceutical Industries Limited",
        "isin": "INE044A01036",
        "sector": "Healthcare",
        "industry": "Pharmaceuticals",
        "is_bank": False,
        "cmp": 1860.0,
        "market_cap_cr": 446000.0,
        "pe": 32.5,
        "pb": 6.8,
        "ps": 8.9,
        "ev_ebitda": 24.5,
        "roe": 16.5,
        "roa": 12.4,
        "debt_to_equity": 0.05,
        "current_ratio": 2.38,
        "dividend_yield": 0.72,
        "promoter_holding_pct": 54.48,
        "pledge_pct": 0.8,
        "avg_daily_traded_value_cr": 510.0,
        "beta": 0.66,
        "insider_net_buy_lakhs": 22.0,
        "rev_base": 33498.0,
        "rev_cagr": 0.118,
        "pat_margin": 0.202,
        "ebitda_margin": 0.268,
        "cfo_pat_ratio": 1.14,
        "capex_rev_ratio": 0.035,
        "div_payout": 0.28,
        "shares_cr": 239.9,
    },
    "DRREDDY": {
        "name": "Dr. Reddy's Laboratories Limited",
        "isin": "INE089A01031",
        "sector": "Healthcare",
        "industry": "Pharmaceuticals",
        "is_bank": False,
        "cmp": 6650.0,
        "market_cap_cr": 111000.0,
        "pe": 19.8,
        "pb": 3.8,
        "ps": 3.9,
        "ev_ebitda": 13.2,
        "roe": 20.2,
        "roa": 15.1,
        "debt_to_equity": 0.07,
        "current_ratio": 2.42,
        "dividend_yield": 0.60,
        "promoter_holding_pct": 26.65,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 320.0,
        "beta": 0.64,
        "insider_net_buy_lakhs": 14.0,
        "rev_base": 19048.0,
        "rev_cagr": 0.122,
        "pat_margin": 0.198,
        "ebitda_margin": 0.275,
        "cfo_pat_ratio": 1.12,
        "capex_rev_ratio": 0.042,
        "div_payout": 0.18,
        "shares_cr": 16.7,
    },
    "CIPLA": {
        "name": "Cipla Limited",
        "isin": "INE059A01026",
        "sector": "Healthcare",
        "industry": "Pharmaceuticals",
        "is_bank": False,
        "cmp": 1540.0,
        "market_cap_cr": 124000.0,
        "pe": 28.4,
        "pb": 4.6,
        "ps": 4.8,
        "ev_ebitda": 18.6,
        "roe": 16.2,
        "roa": 12.8,
        "debt_to_equity": 0.04,
        "current_ratio": 2.75,
        "dividend_yield": 0.84,
        "promoter_holding_pct": 33.47,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 360.0,
        "beta": 0.61,
        "insider_net_buy_lakhs": 10.0,
        "rev_base": 19160.0,
        "rev_cagr": 0.112,
        "pat_margin": 0.165,
        "ebitda_margin": 0.242,
        "cfo_pat_ratio": 1.15,
        "capex_rev_ratio": 0.038,
        "div_payout": 0.22,
        "shares_cr": 80.7,
    },
    "RELIANCE": {
        "name": "Reliance Industries Limited",
        "isin": "INE002A01018",
        "sector": "Oil Gas & Consumable Fuels",
        "industry": "Integrated Oil & Gas",
        "is_bank": False,
        "cmp": 2850.0,
        "market_cap_cr": 1928000.0,
        "pe": 22.8,
        "pb": 2.35,
        "ps": 2.05,
        "ev_ebitda": 13.4,
        "roe": 15.6,
        "roa": 6.8,
        "debt_to_equity": 0.42,
        "current_ratio": 1.42,
        "dividend_yield": 0.35,
        "promoter_holding_pct": 50.31,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 2150.0,
        "beta": 0.96,
        "insider_net_buy_lakhs": 120.0,
        "rev_base": 659205.0,
        "rev_cagr": 0.124,
        "pat_margin": 0.082,
        "ebitda_margin": 0.165,
        "cfo_pat_ratio": 1.32,
        "capex_rev_ratio": 0.068,
        "div_payout": 0.12,
        "shares_cr": 676.5,
    },
    "LT": {
        "name": "Larsen & Toubro Limited",
        "isin": "INE018A01030",
        "sector": "Capital Goods",
        "industry": "Civil Construction & Engineering",
        "is_bank": False,
        "cmp": 3620.0,
        "market_cap_cr": 498000.0,
        "pe": 32.0,
        "pb": 5.7,
        "ps": 2.25,
        "ev_ebitda": 20.2,
        "roe": 16.4,
        "roa": 6.2,
        "debt_to_equity": 0.58,
        "current_ratio": 1.46,
        "dividend_yield": 0.78,
        "promoter_holding_pct": 0.0,
        "pledge_pct": 0.0,
        "avg_daily_traded_value_cr": 820.0,
        "beta": 1.04,
        "insider_net_buy_lakhs": 35.0,
        "rev_base": 145452.0,
        "rev_cagr": 0.128,
        "pat_margin": 0.072,
        "ebitda_margin": 0.125,
        "cfo_pat_ratio": 1.16,
        "capex_rev_ratio": 0.025,
        "div_payout": 0.28,
        "shares_cr": 137.5,
    },
    "WEAKCORP": {
        "name": "Weak Leveraged Enterprises Limited",
        "isin": "INE999Z01011",
        "sector": "Capital Goods",
        "industry": "Industrial Products",
        "is_bank": False,
        "cmp": 65.0,
        "market_cap_cr": 4200.0,
        "pe": 78.0,
        "pb": 4.9,
        "ps": 3.8,
        "ev_ebitda": 42.0,
        "roe": 4.2,
        "roa": 1.1,
        "debt_to_equity": 2.85,
        "current_ratio": 0.82,
        "dividend_yield": 0.0,
        "promoter_holding_pct": 38.0,
        "pledge_pct": 48.5,
        "avg_daily_traded_value_cr": 3.5,
        "beta": 1.65,
        "insider_net_buy_lakhs": -50.0,
        "rev_base": 1400.0,
        "rev_cagr": -0.04,
        "pat_margin": 0.038,
        "ebitda_margin": 0.085,
        "cfo_pat_ratio": 0.35,
        "capex_rev_ratio": 0.095,
        "div_payout": 0.0,
        "shares_cr": 64.6,
        "auditor_flags": 1,
    },
    "IDEA": {
        "name": "Vodafone Idea Limited",
        "isin": "INE669E01016",
        "sector": "Telecommunication",
        "industry": "Telecom - Cellular & Fixed line services",
        "is_bank": False,
        "cmp": 13.5,
        "market_cap_cr": 92000.0,
        "pe": 95.0,
        "pb": 8.5,
        "ps": 2.15,
        "ev_ebitda": 19.5,
        "roe": 2.5,
        "roa": 0.8,
        "debt_to_equity": 4.20,
        "current_ratio": 0.55,
        "dividend_yield": 0.0,
        "promoter_holding_pct": 37.2,
        "pledge_pct": 32.0,
        "avg_daily_traded_value_cr": 410.0,
        "beta": 1.52,
        "insider_net_buy_lakhs": -25.0,
        "rev_base": 41952.0,
        "rev_cagr": 0.015,
        "pat_margin": 0.022,
        "ebitda_margin": 0.11,
        "cfo_pat_ratio": 0.45,
        "capex_rev_ratio": 0.14,
        "div_payout": 0.0,
        "shares_cr": 6814.0,
        "auditor_flags": 1,
    },
}


class SyntheticStockProvider(StockDataProvider):
    """Deterministic synthetic stock provider covering NSE equities across multiple sectors."""

    def __init__(self, as_of: date = DEFAULT_AS_OF) -> None:
        self.as_of = as_of

    def symbols(self) -> list[str]:
        return list(_STOCK_PROFILES.keys())

    def list_universe(self) -> list[StockMasterItem]:
        ts = f"{self.as_of.isoformat()}T09:00:00+00:00"
        return [
            StockMasterItem(
                symbol=sym,
                isin=prof["isin"],
                name=prof["name"],
                series="EQ",
                exchange="NSE",
                sector=prof["sector"],
                industry=prof["industry"],
                is_bank=bool(prof["is_bank"]),
                active=True,
                is_active=True,
                updated_at=ts,
            )
            for sym, prof in _STOCK_PROFILES.items()
        ]

    def fetch_snapshot(self, symbol: str) -> StockSnapshot | None:
        return self.get_snapshot(symbol)

    def fetch_financials(self, symbol: str) -> list[StockFinancialYear]:
        return self.get_financials(symbol)

    def fetch_prices(self, symbol: str) -> list[StockPricePoint]:
        return self.get_price_history(symbol)

    def get_snapshot(self, symbol: str) -> StockSnapshot | None:
        sym = symbol.strip().upper().removesuffix(".NS")
        prof = _STOCK_PROFILES.get(sym)
        if prof is None:
            return None

        fin_years = self.get_financials(sym)
        latest = fin_years[-1] if fin_years else None
        prev = fin_years[-2] if len(fin_years) >= 2 else None

        eps_ttm = latest.eps if latest else None
        bvps = (
            round(latest.total_equity_cr / prof["shares_cr"], 2)
            if latest and latest.total_equity_cr and prof.get("shares_cr")
            else None
        )
        ev_cr = None
        if latest and latest.total_debt_cr is not None and latest.cash_and_equivalents_cr is not None:
            ev_cr = round(
                prof["market_cap_cr"] + latest.total_debt_cr - latest.cash_and_equivalents_cr,
                2,
            )

        earnings_growth_yoy = None
        if latest and prev and latest.pat_cr and prev.pat_cr and prev.pat_cr > 0:
            earnings_growth_yoy = round((latest.pat_cr - prev.pat_cr) / prev.pat_cr, 4)

        revenue_growth_yoy = None
        if latest and prev and latest.revenue_cr and prev.revenue_cr and prev.revenue_cr > 0:
            revenue_growth_yoy = round((latest.revenue_cr - prev.revenue_cr) / prev.revenue_cr, 4)

        roce_dec = None
        if (
            not prof["is_bank"]
            and latest
            and latest.ebit_cr is not None
            and latest.total_assets_cr is not None
            and latest.current_liabilities_cr is not None
        ):
            ce = latest.total_assets_cr - latest.current_liabilities_cr
            if ce > 0:
                roce_dec = round(latest.ebit_cr / ce, 4)

        int_cov = None
        if (
            not prof["is_bank"]
            and latest
            and latest.ebit_cr is not None
            and latest.interest_cr is not None
            and latest.interest_cr > 0
        ):
            int_cov = round(latest.ebit_cr / latest.interest_cr, 2)

        return StockSnapshot(
            symbol=sym,
            name=prof["name"],
            isin=prof["isin"],
            sector=prof["sector"],
            industry=prof["industry"],
            is_bank=bool(prof["is_bank"]),
            as_of=self.as_of.isoformat(),
            as_of_date=self.as_of.isoformat(),
            price=prof["cmp"],
            cmp=prof["cmp"],
            market_cap_cr=prof["market_cap_cr"],
            enterprise_value_cr=ev_cr,
            pe=prof["pe"],
            pb=prof["pb"],
            ps=prof["ps"],
            ev_ebitda=prof["ev_ebitda"],
            roe=round(float(prof["roe"]) / 100.0, 4) if prof.get("roe") is not None else None,
            roa=round(float(prof["roa"]) / 100.0, 4) if prof.get("roa") is not None else None,
            roce=roce_dec,
            debt_to_equity=prof["debt_to_equity"],
            current_ratio=prof["current_ratio"],
            interest_coverage=int_cov,
            dividend_yield=round(float(prof["dividend_yield"]) / 100.0, 4) if prof.get("dividend_yield") is not None else None,
            eps_ttm=eps_ttm,
            book_value_per_share=bvps,
            shares_outstanding=prof["shares_cr"],
            promoter_holding_pct=prof["promoter_holding_pct"],
            pledge_pct=prof["pledge_pct"],
            auditor_flags=int(prof.get("auditor_flags", 0)),
            avg_daily_traded_value_cr=prof["avg_daily_traded_value_cr"],
            beta=prof["beta"],
            return_1y=round(float(prof["rev_cagr"]) + 0.02, 4),
            volatility_1y=round(0.16 * float(prof.get("beta") or 1.0), 4),
            max_drawdown_1y=round(-0.11 * float(prof.get("beta") or 1.0), 4),
            currency="INR",
            last_filing_date="2025-03-31",
            fetched_at=f"{self.as_of.isoformat()}T09:00:00+00:00",
            source="synthetic_nse",
            insider_net_buy_lakhs=prof.get("insider_net_buy_lakhs", 0.0),
            revenue_growth_yoy=revenue_growth_yoy,
            earnings_growth_yoy=earnings_growth_yoy,
        )

    def get_financials(self, symbol: str) -> list[StockFinancialYear]:
        sym = symbol.strip().upper().removesuffix(".NS")
        prof = _STOCK_PROFILES.get(sym)
        if prof is None:
            return []

        years = [2021, 2022, 2023, 2024, 2025]
        out: list[StockFinancialYear] = []
        rev_base = float(prof["rev_base"])
        rev_cagr = float(prof["rev_cagr"])
        pat_margin = float(prof["pat_margin"])
        ebitda_margin = float(prof["ebitda_margin"])
        cfo_pat_ratio = float(prof["cfo_pat_ratio"])
        capex_rev_ratio = float(prof["capex_rev_ratio"])
        div_payout = float(prof["div_payout"])
        shares_cr = float(prof["shares_cr"])
        de_ratio = float(prof["debt_to_equity"])
        roe_pct = float(prof["roe"])
        is_bank = bool(prof["is_bank"])

        for idx, yr in enumerate(years):
            growth_factor = (1.0 + rev_cagr) ** idx
            rev = round(rev_base * growth_factor, 2)
            other_inc = round(rev * 0.035, 2)
            ebitda = round(rev * ebitda_margin, 2)
            dep = round(rev * 0.032, 2)
            ebit = round(ebitda - dep, 2)

            # Infosys exact FY2021 worked example numbers from FA-Course-Notes.md
            if sym == "INFY" and yr == 2021:
                pat = 19351.0
                cfo = 23224.0
                curr_assets = 60733.0
                curr_liab = 23865.0
                total_equity = 76351.0
                total_assets = 108386.0
                total_debt = 5325.0
                cash_eq = 24714.0
            else:
                pat = round(rev * pat_margin * (1.0 + 0.01 * idx), 2)
                cfo = round(pat * cfo_pat_ratio, 2)
                total_equity = round((pat / max(roe_pct / 100.0, 0.03)), 2)
                total_debt = round(total_equity * de_ratio, 2)
                curr_liab = round(rev * 0.22, 2)
                cr = float(prof["current_ratio"] or 1.5)
                curr_assets = round(curr_liab * cr, 2)
                cash_eq = round(curr_assets * 0.38, 2)
                total_assets = round(total_equity + total_debt + curr_liab, 2)

            interest = round(max(total_debt * 0.075, 10.0), 2) if not is_bank else None
            tax = round(pat * 0.25, 2)
            pbt = round(pat + tax, 2)
            capex = round(rev * capex_rev_ratio, 2)
            fcf = round(cfo - capex, 2)
            div_paid = round(pat * div_payout, 2)
            eps = round(pat / shares_cr, 2)
            dps = round(div_paid / shares_cr, 2)

            inv = round(rev * 0.06, 2) if not is_bank else None
            rec = round(rev * 0.14, 2) if not is_bank else None
            pay = round(rev * 0.09, 2) if not is_bank else None
            fixed_assets = round(max(total_assets - curr_assets, rev * 0.25), 2)

            # Bank specific fields
            nii = round(rev * 0.58, 2) if is_bank else None
            nim = round(float(prof.get("nim_pct", 3.8)) + 0.03 * (idx - 2), 2) if is_bank else None
            gnpa = round(max(0.5, float(prof.get("gnpa_pct", 1.5)) - 0.08 * idx), 2) if is_bank else None
            nnpa = round(max(0.15, float(prof.get("nnpa_pct", 0.4)) - 0.03 * idx), 2) if is_bank else None
            casa = round(float(prof.get("casa_pct", 42.0)) + 0.2 * idx, 2) if is_bank else None
            car = round(float(prof.get("car_pct", 16.5)), 2) if is_bank else None
            credit_cost = round(float(prof.get("credit_cost_pct", 0.5)), 2) if is_bank else None
            pcr = round(float(prof.get("provision_coverage_pct", 75.0)), 2) if is_bank else None
            advances = round(rev * 8.5, 2) if is_bank else None
            deposits = round(rev * 10.2, 2) if is_bank else None
            borrowings = round(rev * 1.4, 2) if is_bank else None

            out.append(
                StockFinancialYear(
                    symbol=sym,
                    period_end=f"{yr}-03-31",
                    period_type="annual",
                    revenue_cr=rev,
                    other_income_cr=other_inc,
                    ebitda_cr=ebitda if not is_bank else None,
                    ebit_cr=ebit if not is_bank else None,
                    interest_expense_cr=interest,
                    depreciation_cr=dep,
                    pbt_cr=pbt,
                    tax_cr=tax,
                    pat_cr=pat,
                    eps=eps,
                    dps=dps,
                    dividends_paid_cr=div_paid,
                    total_assets_cr=total_assets,
                    total_equity_cr=total_equity,
                    total_debt_cr=total_debt,
                    cash_and_equivalents_cr=cash_eq,
                    current_assets_cr=curr_assets if not is_bank else None,
                    current_liabilities_cr=curr_liab if not is_bank else None,
                    inventory_cr=inv,
                    receivables_cr=rec,
                    payables_cr=pay,
                    fixed_assets_cr=fixed_assets,
                    cfo_cr=cfo,
                    capex_cr=capex,
                    cfi_cr=round(-capex * 1.1, 2),
                    cff_cr=round(-div_paid - interest if interest else -div_paid, 2),
                    fcf_cr=fcf,
                    nii_cr=nii,
                    nim_pct=nim,
                    gnpa_pct=gnpa,
                    nnpa_pct=nnpa,
                    casa_pct=casa,
                    car_pct=car,
                    credit_cost_pct=credit_cost,
                    provision_coverage_pct=pcr,
                    advances_cr=advances,
                    deposits_cr=deposits,
                    borrowings_cr=borrowings,
                )
            )
        return out

    def get_price_history(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[StockPricePoint]:
        sym = symbol.strip().upper().removesuffix(".NS")
        prof = _STOCK_PROFILES.get(sym)
        if prof is None:
            return []

        end_dt = end or self.as_of
        start_dt = start or (end_dt - timedelta(days=365 * 5))
        cmp_val = float(prof["cmp"])
        annual_drift = max(-0.08, float(prof["rev_cagr"]) + 0.015)
        seed_offset = sum(ord(ch) for ch in sym)

        points: list[StockPricePoint] = []
        # Step every 7 calendar days (~260 weekly points over 5Y) plus exact end_dt
        cur = start_dt
        while cur.weekday() >= 5:
            cur += timedelta(days=1)
        while cur <= end_dt:
            if cur.weekday() < 5:
                years_to_end = (end_dt - cur).days / 365.25
                wave = 1.0 + 0.035 * math.sin((cur.toordinal() + seed_offset) / 28.0)
                price = round(cmp_val / ((1.0 + annual_drift) ** years_to_end) * wave, 2)
                points.append(
                    StockPricePoint(
                        symbol=sym,
                        date=cur.isoformat(),
                        close=max(1.0, price),
                        volume=round(float(prof["avg_daily_traded_value_cr"]) * 1e7 / max(price, 1.0), 0),
                    )
                )
            cur += timedelta(days=7)

        if not points or points[-1].date != end_dt.isoformat():
            points.append(
                StockPricePoint(
                    symbol=sym,
                    date=end_dt.isoformat(),
                    close=cmp_val,
                    volume=round(float(prof["avg_daily_traded_value_cr"]) * 1e7 / max(cmp_val, 1.0), 0),
                )
            )
        return points

    # ---- 360° report data (deterministic) ----

    @staticmethod
    def _weekdays(end_dt: date, days: int) -> list[date]:
        cur, out = end_dt - timedelta(days=days), []
        while cur <= end_dt:
            if cur.weekday() < 5:
                out.append(cur)
            cur += timedelta(days=1)
        return out

    def fetch_daily_ohlcv(
        self, symbol: str, range_: str = "2y"
    ) -> tuple[list[StockOHLCV], list[CorporateAction]]:
        """Seeded random walk ending exactly at the profile CMP; one split for ~1 in 5 symbols."""
        sym = symbol.strip().upper().removesuffix(".NS")
        prof = _STOCK_PROFILES.get(sym)
        if prof is None:
            return [], []
        rng = random.Random(sum(ord(ch) for ch in sym) * 7919)
        days = self._weekdays(self.as_of, 730 if range_ == "2y" else 365)
        drift = max(-0.08, float(prof["rev_cagr"]) + 0.015) / 252
        rets = [rng.gauss(drift, 0.014) for _ in days]
        # Build backwards from CMP so the last close equals the profile CMP
        closes = [0.0] * len(days)
        closes[-1] = float(prof["cmp"])
        for i in range(len(days) - 1, 0, -1):
            closes[i - 1] = closes[i] / (1 + rets[i])
        avg_vol = float(prof["avg_daily_traded_value_cr"]) * 1e7 / max(closes[-1], 1.0)
        bars: list[StockOHLCV] = []
        for i, d in enumerate(days):
            c = round(closes[i], 2)
            prev = closes[i - 1] if i else c
            o = round(prev * (1 + rng.uniform(-0.004, 0.004)), 2)
            hi = round(max(o, c) * (1 + rng.uniform(0.001, 0.01)), 2)
            lo = round(min(o, c) * (1 - rng.uniform(0.001, 0.01)), 2)
            bars.append(StockOHLCV(symbol=sym, date=d.isoformat(), open=o, high=hi, low=lo,
                                   close=c, volume=round(avg_vol * rng.uniform(0.6, 1.4), 0)))
        actions: list[CorporateAction] = []
        if sum(ord(ch) for ch in sym) % 5 == 0:
            actions.append(CorporateAction(symbol=sym, date=days[len(days) // 3].isoformat(),
                                           type="split", value=2.0, detail="2:1", source="synthetic"))
        return bars, actions

    def fetch_index_daily(
        self, index_symbol: str = "^NSEI", range_: str = "2y"
    ) -> list[tuple[str, float]]:
        rng = random.Random(42)
        days = self._weekdays(self.as_of, 730 if range_ == "2y" else 365)
        level, out = 22000.0, []
        for d in days:
            level *= 1 + rng.gauss(0.0004, 0.008)
            out.append((d.isoformat(), round(level, 2)))
        return out

    def fetch_quarterly(self, symbol: str, keep: int = 8) -> list[StockFinancialYear]:
        """8 quarters derived from the latest annual row (seasonal, deterministic)."""
        sym = symbol.strip().upper().removesuffix(".NS")
        annual = self.get_financials(sym)
        if not annual:
            return []
        base = max(annual, key=lambda f: f.period_end)
        growth = float(_STOCK_PROFILES[sym]["rev_cagr"]) if sym in _STOCK_PROFILES else 0.08
        ends = []
        y, m = self.as_of.year, (self.as_of.month - 1) // 3 * 3 + 3
        for _ in range(keep):
            ends.append(f"{y}-{m:02d}-{[31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]:02d}")
            m -= 3
            if m <= 0:
                m, y = m + 12, y - 1
        ends.reverse()

        def q(v: float | None, i: int) -> float | None:
            if v is None:
                return None
            return round(v / 4 * (1 + growth / 4) ** (i - keep + 1) * (1 + 0.03 * (i % 2)), 2)

        return [
            StockFinancialYear(
                symbol=sym, fiscal_year=int(e[:4]), period_end=e, period_type="quarterly",
                revenue_cr=q(base.revenue_cr, i), ebitda_cr=q(base.ebitda_cr, i),
                pbt_cr=q(base.pbt_cr, i), pat_cr=q(base.pat_cr, i), eps=q(base.eps, i),
                other_income_cr=q(base.other_income_cr, i), as_of_date=e, source="synthetic",
            )
            for i, e in enumerate(ends)
        ]
