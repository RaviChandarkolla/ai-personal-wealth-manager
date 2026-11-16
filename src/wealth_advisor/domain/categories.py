"""Static fund-category taxonomy: bucket and minimum sensible holding horizon."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models.enums import Bucket

if TYPE_CHECKING:
    from ..config import Settings


@dataclass(frozen=True)
class CategoryInfo:
    bucket: Bucket
    min_horizon_years: float
    small_aum_ok: bool = False  # liquid / gold use the lower AUM floor
    benchmark: str = ""


CATEGORIES: dict[str, CategoryInfo] = {
    # Original 9 categories (Set 1 baseline)
    "Liquid": CategoryInfo(Bucket.DEBT, 0.0, True, "CRISIL Liquid Debt A-I Index"),
    "Short Duration": CategoryInfo(Bucket.DEBT, 1.0, False, "CRISIL Short Duration Debt A-II Index"),
    "Corporate Bond": CategoryInfo(Bucket.DEBT, 2.0, False, "CRISIL Corporate Debt A-II Index"),
    "Conservative Hybrid": CategoryInfo(Bucket.DEBT, 2.0, False, "CRISIL Hybrid 85+15 - Conservative Index"),
    "Index Fund": CategoryInfo(Bucket.EQUITY, 3.0, False, "Nifty 50 TRI"),
    "Large Cap": CategoryInfo(Bucket.EQUITY, 3.0, False, "Nifty 100 TRI"),
    "Flexi Cap": CategoryInfo(Bucket.EQUITY, 3.0, False, "Nifty 500 TRI"),
    "Mid Cap": CategoryInfo(Bucket.EQUITY, 5.0, False, "Nifty Midcap 150 TRI"),
    "Gold ETF": CategoryInfo(Bucket.GOLD, 3.0, True, "Domestic Price of Gold"),
    # 7 new categories (Set 2 & Combined)
    "Small Cap": CategoryInfo(Bucket.EQUITY, 7.0, False, "Nifty Smallcap 250 TRI"),
    "Large & Mid Cap": CategoryInfo(Bucket.EQUITY, 5.0, False, "Nifty LargeMidcap 250 TRI"),
    "ELSS": CategoryInfo(Bucket.EQUITY, 5.0, False, "Nifty 500 TRI"),
    "Aggressive Hybrid": CategoryInfo(Bucket.EQUITY, 5.0, False, "CRISIL Hybrid 35+65 - Aggressive Index"),
    "Arbitrage": CategoryInfo(Bucket.DEBT, 2.0, False, "Nifty 50 Arbitrage Index"),
    "Banking & PSU": CategoryInfo(Bucket.DEBT, 1.0, False, "CRISIL Banking and PSU Debt Index"),
    "Gilt": CategoryInfo(Bucket.DEBT, 3.0, False, "CRISIL Dynamic Gilt Index"),
}

MULTI_ASSET_CATEGORIES: dict[str, CategoryInfo] = {
    "Direct Equity": CategoryInfo(Bucket.EQUITY, 5.0, False, "Nifty 500 TRI"),
    "Bank FD": CategoryInfo(Bucket.DEBT, 0.0, True, "RBI Scheduled Commercial Bank FD Rate"),
    "SGB": CategoryInfo(Bucket.GOLD, 5.0, True, "IBJA 999 Purity Gold"),
    "Large Cap Equity": CategoryInfo(Bucket.EQUITY, 3.0, False, "Nifty 50 TRI"),
    "REIT": CategoryInfo(Bucket.ALTERNATIVES, 5.0, False, "Nifty REITs & InvITs Index"),
    "InvIT": CategoryInfo(Bucket.ALTERNATIVES, 5.0, False, "Nifty REITs & InvITs Index"),
}

ALL_CATEGORIES: dict[str, CategoryInfo] = {**CATEGORIES, **MULTI_ASSET_CATEGORIES}

CATEGORY_ALIASES: dict[str, str] = {
    "Index": "Index Fund",
    "Index Funds": "Index Fund",
    "Gold ETF / FoF": "Gold ETF",
    "ELSS- Tax Saver": "ELSS",
    "ELSS - Tax Saver": "ELSS",
    "Banking and PSU Debt": "Banking & PSU",
    "Banking & PSU Debt": "Banking & PSU",
    "Short Term": "Short Duration",
    "Value / Contra": "Flexi Cap",
    "Focused": "Flexi Cap",
    "Balanced Advantage": "Aggressive Hybrid",
    "Multi Asset": "Aggressive Hybrid",
    "Equity Savings": "Conservative Hybrid",
    "Overnight": "Liquid",
    "Ultra Short Duration": "Liquid",
    "Low Duration": "Short Duration",
    "Money Market": "Liquid",
}


def normalize_category(category: str) -> str:
    """Return canonical category name if `category` is a known AMFI/SEBI alias."""
    return CATEGORY_ALIASES.get(category, category)


def bucket_for(category: str) -> Bucket | None:
    info = ALL_CATEGORIES.get(category) or ALL_CATEGORIES.get(normalize_category(category))
    return info.bucket if info else None


def min_horizon(category: str, s: "Settings | None" = None) -> float:
    """Minimum holding horizon in years for category, reading active Settings override first."""
    if s is None:
        from ..config import get_settings
        s = get_settings()
    norm = normalize_category(category)
    if s and s.category_min_horizons:
        if category in s.category_min_horizons:
            return s.category_min_horizons[category]
        if norm in s.category_min_horizons:
            return s.category_min_horizons[norm]
    info = ALL_CATEGORIES.get(category) or ALL_CATEGORIES.get(norm)
    return info.min_horizon_years if info else 0.0


def categories_for(bucket: Bucket, s: "Settings | None" = None) -> list[str]:
    """Return fund categories for a given bucket, filtered by allowed_categories in Settings."""
    if s is None:
        from ..config import get_settings
        s = get_settings()
    allowed = set(s.allowed_categories) if s and s.allowed_categories else None
    return [
        c
        for c, i in CATEGORIES.items()
        if i.bucket == bucket and (allowed is None or c in allowed)
    ]

