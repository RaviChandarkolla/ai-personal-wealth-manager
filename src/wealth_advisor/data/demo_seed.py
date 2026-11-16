"""Synthetic demo universe (NOT real funds or data) so the pipeline runs without licensed feeds.

Replace with `load_funds_csv` (curated fact-sheet data) and the AMFI loader for real use.
"""

import zlib
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..config import get_settings
from ..domain.categories import CATEGORIES
from .store import FundRecord, MetadataStore

SOURCE = "synthetic_demo"
NAV_YEARS = 6.5

# category: (annual return %, annual volatility %, direct expense %, AUM Cr)
_SPEC = {
    "Liquid": (6.8, 0.4, 0.15, 9000),
    "Short Duration": (7.2, 1.2, 0.35, 4000),
    "Corporate Bond": (7.5, 1.8, 0.35, 5000),
    "Conservative Hybrid": (8.5, 3.5, 0.70, 2500),
    "Index Fund": (12.5, 15.0, 0.20, 12000),
    "Large Cap": (12.8, 15.5, 0.70, 8000),
    "Flexi Cap": (14.0, 16.0, 0.75, 7000),
    "Mid Cap": (17.0, 20.0, 0.80, 6000),
    "Gold ETF": (11.0, 13.0, 0.40, 1500),
}

# Extended 7 categories (Set 2 / expanded universe)
_EXTENDED_SPEC = {
    "Small Cap": (18.5, 23.0, 0.75, 5000),
    "Large & Mid Cap": (14.5, 17.0, 0.75, 6000),
    "ELSS": (14.0, 16.5, 0.80, 5000),
    "Aggressive Hybrid": (12.0, 12.0, 0.75, 7000),
    "Arbitrage": (6.5, 1.0, 0.35, 8000),
    "Banking & PSU": (7.2, 1.5, 0.35, 6000),
    "Gilt": (7.4, 3.0, 0.40, 3000),
}

# category: (base_ytm, modified_duration, avg_maturity_years)
_DEBT_METRICS = {
    "Liquid": (6.8, 0.12, 0.15),
    "Short Duration": (7.2, 1.8, 2.0),
    "Corporate Bond": (7.5, 3.0, 3.5),
    "Conservative Hybrid": (8.0, 2.5, 3.0),
    "Arbitrage": (6.5, 0.4, 0.5),
    "Banking & PSU": (7.2, 2.2, 2.5),
    "Gilt": (7.3, 3.5, 4.0),
}

# brand: (return delta %, young fund?, expense over ceiling?, low AUM?, regular plan?, rating, trusted_prefix)
_BRANDS = {
    "Orchid": (1.0, False, False, False, False, 5.0, "SBI"),
    "Juniper": (0.4, False, False, False, False, 4.0, "HDFC"),
    "Cedar": (0.2, True, False, False, False, 4.0, "ICICI Prudential"),
    "Maple": (-0.7, False, True, True, False, 3.0, "Axis"),
    "Falcon": (0.5, False, False, False, True, 3.0, "Falcon"),
}


def _fund_name(brand: str, category: str, regular: bool) -> str:
    base = category if category.endswith(("Fund", "ETF")) else f"{category} Fund"
    return f"{brand} {base} - {'Regular' if regular else 'Direct'} Plan"


def seed_demo(
    store: MetadataStore,
    as_of: date | None = None,
    include_extended: bool = False,
) -> int:
    s = get_settings()
    end = as_of or date.today()
    specs = {**_SPEC, **_EXTENDED_SPEC} if include_extended else _SPEC
    funds: list[FundRecord] = []
    for ci, (category, (mu, vol, er, aum)) in enumerate(specs.items()):
        info = CATEGORIES[category]
        no_rating = category in ("Index Fund", "Gold ETF")
        ceiling = s.expense_ceilings.get(category, s.default_expense_ceiling)
        debt_info = _DEBT_METRICS.get(category)
        for bi, (brand, (d_mu, young, over, low_aum, regular, rating, prefix)) in enumerate(_BRANDS.items()):
            code = f"DEMO{ci:02d}{bi}"
            nav_start = end - timedelta(days=int((2.5 if young else NAV_YEARS) * 365.25))

            # Manager tenure: Cedar/Maple young tenure ~1.5y, others ~5y
            manager_years = 1.5 if (young or brand == "Maple") else (5.5 if brand == "Orchid" else 4.5)
            manager_since = end - timedelta(days=int(manager_years * 365.25))

            # YTM & maturity
            if debt_info:
                base_ytm, mod_dur, avg_mat = debt_info
                # Maple has an outlier high YTM (+2.5pp) indicating credit risk
                ytm_val = round(base_ytm + 2.5 if brand == "Maple" else base_ytm + d_mu * 0.1, 2)
            else:
                ytm_val, mod_dur, avg_mat = None, None, None

            funds.append(
                FundRecord(
                    code=code,
                    isin=f"XSDEMO{ci:02d}{bi}00",
                    name=_fund_name(brand, category, regular),
                    amc=f"{prefix} {brand} Mutual Fund",
                    category=category,
                    plan="regular" if regular else "direct",
                    bucket=info.bucket,
                    expense_ratio=round(
                        ceiling + 0.3 if over else er + (0.8 if regular else 0.0), 2
                    ),
                    aum_cr=float(round(aum * (0.02 if low_aum else 1.0) * (1 - 0.05 * bi))),
                    rating=None if no_rating else rating,
                    benchmark=f"{category} Demo Index",
                    launch_date=nav_start if young else end - timedelta(days=int(8 * 365.25)),
                    manager=f"Manager {brand}",
                    source=SOURCE,
                    as_of=end,
                    manager_since=manager_since,
                    ytm=ytm_val,
                    modified_duration=mod_dur,
                    avg_maturity_years=avg_mat,
                )
            )
            rng = np.random.default_rng(zlib.crc32(code.encode()))
            days = pd.bdate_range(nav_start, end)
            rets = (mu + d_mu) / 100 / 252 + vol / 100 / np.sqrt(252) * rng.standard_normal(len(days))
            nav = 10.0 * np.cumprod(1 + rets)
            store.add_funds([funds[-1]])
            store.save_nav(code, pd.Series(nav, index=days))
    return len(funds)
