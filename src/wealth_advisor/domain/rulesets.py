"""Switchable fund-selection rule sets: Set 1, Set 2, and Combined.

Provides:
- Definitions for Set 1 (production default) and Set 2 (Magic of Mutual Funds).
- Dynamically merged Combined rule set via `merge_rule_sets`.
- Request-scoped ContextVar management via `use_rule_set`, `active_rule_set`, and `get_active_rule_set`.
- Global default state and rule-set metadata.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .rule_merge import merge_rule_sets

# Request-scoped active rule set override
_request_rule_set: ContextVar[str | None] = ContextVar("request_rule_set", default=None)

# Global default rule set in memory
_global_rule_set: str = "set1"


SET1_RULES: dict[str, Any] = {
    "fund_rule_set": "set1",
    "allocation_bands": {
        "Conservative": {
            "equity": (15.0, 30.0),
            "debt": (70.0, 85.0),
            "gold": (0.0, 10.0),
            "alternatives": (0.0, 0.0),
        },
        "Moderate": {
            "equity": (40.0, 60.0),
            "debt": (30.0, 45.0),
            "gold": (5.0, 10.0),
            "alternatives": (0.0, 5.0),
        },
        "Aggressive": {
            "equity": (70.0, 85.0),
            "debt": (5.0, 20.0),
            "gold": (5.0, 10.0),
            "alternatives": (0.0, 10.0),
        },
    },
    "category_min_horizons": {
        "Liquid": 0.0,
        "Short Duration": 1.0,
        "Corporate Bond": 2.0,
        "Conservative Hybrid": 2.0,
        "Index Fund": 3.0,
        "Large Cap": 3.0,
        "Flexi Cap": 3.0,
        "Mid Cap": 5.0,
        "Gold ETF": 3.0,
        "Small Cap": 7.0,
        "Large & Mid Cap": 5.0,
        "ELSS": 5.0,
        "Aggressive Hybrid": 5.0,
        "Arbitrage": 2.0,
        "Banking & PSU": 1.0,
        "Gilt": 3.0,
    },
    "allowed_categories": [
        "Liquid",
        "Short Duration",
        "Corporate Bond",
        "Conservative Hybrid",
        "Index Fund",
        "Large Cap",
        "Flexi Cap",
        "Mid Cap",
        "Gold ETF",
    ],
    "equity_cap_horizon_lt_1y": 5.0,
    "equity_cap_horizon_lt_3y": 20.0,
    "equity_cap_horizon_lt_5y": None,
    "equity_cap_low_emergency": 30.0,
    "min_track_years_equity": 5.0,
    "min_track_years_debt": 3.0,
    "min_aum_cr": 500.0,
    "min_aum_cr_small": 100.0,
    "min_rating": 4.0,
    "require_direct_plan": True,
    "require_positive_alpha": False,
    "max_beta": None,
    "min_manager_tenure_years": None,
    "trusted_amcs": [],
    "trusted_amc_bonus": 0.0,
    "fundamental_weights": {
        "pct_roll_3y": 0.25,
        "pct_cagr_5y": 0.25,
        "sharpe": 0.25,
        "max_drawdown": 0.25,
        "alpha": 0.0,
        "pct_low_vol": 0.0,
    },
    "ytm_outlier_pp": None,
    "match_maturity_to_horizon": False,
    "buy_min_score": 50.0,
    "min_confidence": 0.6,
    "max_weight_per_instrument": 30.0,
    "analyst_weights": {
        "fundamental": 0.35,
        "fit": 0.25,
        "news": 0.20,
        "sentiment": 0.10,
        "liquidity_cost": 0.10,
    },
    "expense_ceilings": {
        "Liquid": 0.30,
        "Short Duration": 0.50,
        "Corporate Bond": 0.50,
        "Conservative Hybrid": 0.90,
        "Index Fund": 0.50,
        "Gold ETF": 0.60,
        "Large Cap": 1.00,
        "Flexi Cap": 1.00,
        "Mid Cap": 1.00,
        "Small Cap": 1.00,
        "Large & Mid Cap": 1.00,
        "ELSS": 1.00,
        "Aggressive Hybrid": 1.00,
        "Arbitrage": 0.50,
        "Banking & PSU": 0.50,
        "Gilt": 0.50,
    },
    "vol_caps": {"Conservative": 0.06, "Moderate": 0.14, "Aggressive": 0.25},
}


SET2_RULES: dict[str, Any] = {
    "fund_rule_set": "set2",
    "allocation_bands": {
        "Conservative": {
            "equity": (10.0, 25.0),
            "debt": (75.0, 90.0),
            "gold": (0.0, 10.0),
            "alternatives": (0.0, 0.0),
        },
        "Moderate": {
            "equity": (40.0, 60.0),
            "debt": (30.0, 50.0),
            "gold": (5.0, 10.0),
            "alternatives": (0.0, 0.0),
        },
        "Aggressive": {
            "equity": (65.0, 80.0),
            "debt": (15.0, 30.0),
            "gold": (5.0, 10.0),
            "alternatives": (0.0, 0.0),
        },
    },
    "category_min_horizons": {
        "Large Cap": 5.0,
        "Flexi Cap": 5.0,
        "Large & Mid Cap": 5.0,
        "Mid Cap": 5.0,
        "Index Fund": 5.0,
        "ELSS": 5.0,
        "Aggressive Hybrid": 5.0,
        "Small Cap": 7.0,
        "Gilt": 3.0,
        "Conservative Hybrid": 3.0,
        "Gold ETF": 3.0,
        "Corporate Bond": 2.0,
        "Arbitrage": 2.0,
        "Banking & PSU": 1.0,
        "Short Duration": 1.0,
        "Liquid": 0.0,
    },
    "allowed_categories": [
        "Liquid",
        "Short Duration",
        "Corporate Bond",
        "Conservative Hybrid",
        "Index Fund",
        "Large Cap",
        "Flexi Cap",
        "Mid Cap",
        "Gold ETF",
        "Small Cap",
        "Large & Mid Cap",
        "ELSS",
        "Aggressive Hybrid",
        "Arbitrage",
        "Banking & PSU",
        "Gilt",
    ],
    "equity_cap_horizon_lt_1y": 0.0,
    "equity_cap_horizon_lt_3y": 0.0,
    "equity_cap_horizon_lt_5y": 25.0,
    "equity_cap_low_emergency": 30.0,
    "min_track_years_equity": 3.0,
    "min_track_years_debt": 3.0,
    "min_aum_cr": 100.0,
    "min_aum_cr_small": 100.0,
    "min_rating": None,
    "require_direct_plan": True,
    "require_positive_alpha": True,
    "max_beta": 1.2,
    "min_manager_tenure_years": 3.0,
    "trusted_amcs": ["SBI", "HDFC", "ICICI Prudential", "PPFAS", "Motilal Oswal"],
    "trusted_amc_bonus": 5.0,
    "fundamental_weights": {
        "pct_roll_3y": 0.30,
        "pct_cagr_5y": 0.15,
        "sharpe": 0.15,
        "max_drawdown": 0.15,
        "alpha": 0.15,
        "pct_low_vol": 0.10,
    },
    "ytm_outlier_pp": 2.0,
    "match_maturity_to_horizon": True,
    "buy_min_score": 50.0,
    "min_confidence": 0.6,
    "max_weight_per_instrument": 30.0,
    "analyst_weights": {
        "fundamental": 0.45,
        "fit": 0.30,
        "news": 0.05,
        "sentiment": 0.05,
        "liquidity_cost": 0.15,
    },
    "expense_ceilings": {
        "Liquid": 0.30,
        "Short Duration": 0.50,
        "Corporate Bond": 0.50,
        "Conservative Hybrid": 0.90,
        "Index Fund": 0.50,
        "Gold ETF": 0.60,
        "Large Cap": 1.00,
        "Flexi Cap": 1.00,
        "Mid Cap": 1.00,
        "Small Cap": 1.00,
        "Large & Mid Cap": 1.00,
        "ELSS": 1.00,
        "Aggressive Hybrid": 1.00,
        "Arbitrage": 0.50,
        "Banking & PSU": 0.50,
        "Gilt": 0.50,
    },
    "vol_caps": {"Conservative": 0.06, "Moderate": 0.14, "Aggressive": 0.25},
}


# Dynamically computed Combined rule set & provenance
_combined_merged, COMBINED_PROVENANCE = merge_rule_sets(SET1_RULES, SET2_RULES)
_combined_merged["fund_rule_set"] = "combined"
COMBINED_RULES: dict[str, Any] = _combined_merged


RULE_SETS: dict[str, dict[str, Any]] = {
    "set1": SET1_RULES,
    "set2": SET2_RULES,
    "combined": COMBINED_RULES,
}


AVAILABLE_RULE_SETS: list[dict[str, Any]] = [
    {
        "id": "set1",
        "label": "Set 1 · Default",
        "description": "Standard baseline selection rules with 9 asset categories and 4-star minimum rating.",
        "highlights": [
            "Current baseline behavior",
            "9 allowed categories",
            "500 Cr AUM floor (100 Cr for liquid/gold)",
            "4-star rating minimum",
        ],
    },
    {
        "id": "set2",
        "label": "Set 2 · Magic of MF",
        "description": "Rules derived from Magic of Mutual Funds: 16 categories, positive alpha requirement, maturity matching, and trusted AMC bonus.",
        "highlights": [
            "16 categories (Small Cap, Large & Mid Cap, ELSS, Aggressive Hybrid, Arbitrage, Banking & PSU, Gilt)",
            "Require positive alpha for active equity",
            "Match debt maturity to goal horizon",
            "No equity for horizons under 3 years, max 25% for 3-5y",
            "3-year fund age & manager tenure, 100 Cr AUM floor",
            "+5 bonus for trusted AMCs",
        ],
    },
    {
        "id": "combined",
        "label": "Combined · Set 1 + 2",
        "description": "Deterministically merged rules taking the stricter floor/ceiling, longer horizons, overlapping allocation ranges, and averaged weights.",
        "highlights": [
            "16 categories with stricter 500 Cr AUM & 5y history filters",
            "4-star rating minimum AND positive alpha requirement",
            "Strictest equity capacity caps (0% <3y, 25% 3-5y)",
            "Overlapping allocation bands (e.g. Moderate equity 40-60%, debt 30-45%)",
            "Blended analyst weights (fundamental 0.40, fit 0.275)",
        ],
    },
]


@contextmanager
def use_rule_set(name: str | None = None):
    """Context manager to set the active rule set for the current execution context/request."""
    if name is None:
        yield active_rule_set()
        return
    canonical = name.lower()
    if canonical not in RULE_SETS:
        raise ValueError(f"Unknown rule set {name!r}. Allowed: {list(RULE_SETS.keys())}")
    token = _request_rule_set.set(canonical)
    try:
        yield canonical
    finally:
        _request_rule_set.reset(token)


def get_active_rule_set() -> str | None:
    """Return the request-scoped active rule set if set, else None."""
    return _request_rule_set.get()


def get_default_rule_set() -> str:
    """Return the global default rule set in memory."""
    return _global_rule_set


def active_rule_set() -> str:
    """Return effective active rule set (request-scoped override if present, else global default)."""
    return _request_rule_set.get() or _global_rule_set


def set_active_rule_set(name: str) -> str:
    """Change the global in-memory default rule set and clear cached Settings."""
    global _global_rule_set
    canonical = name.lower()
    if canonical not in RULE_SETS:
        raise ValueError(f"Unknown rule set {name!r}. Allowed: {list(RULE_SETS.keys())}")
    _global_rule_set = canonical

    # Clear config settings cache so next get_settings() picks up the new global set
    from ..config import get_settings
    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()
    return _global_rule_set


def get_rule_set_overrides(name: str) -> dict[str, Any]:
    """Return the overrides dictionary for the given rule set."""
    canonical = name.lower()
    if canonical not in RULE_SETS:
        raise ValueError(f"Unknown rule set {name!r}. Allowed: {list(RULE_SETS.keys())}")
    return RULE_SETS[canonical]
