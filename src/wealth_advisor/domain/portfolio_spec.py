"""Turn the user's AI notes into an enforceable portfolio spec (all rules here are deterministic)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..models.core import AllocationBand, FundRequest, PortfolioSpec
from ..models.enums import Bucket
from .categories import ALL_CATEGORIES, normalize_category

if TYPE_CHECKING:
    from ..config import Settings

GENERIC_DEBT = "Debt"
GENERIC_EQUITY = "Equity"
GOLD = "Gold ETF"
_GENERIC_BUCKET = {GENERIC_DEBT: Bucket.DEBT, GENERIC_EQUITY: Bucket.EQUITY}

# Ordered: the first matching rule wins, so specific names come before generic ones.
_KEYWORD_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (("large", "mid"), (), "Large & Mid Cap"),
    (("flexi",), (), "Flexi Cap"),
    (("multi cap",), (), "Flexi Cap"),
    (("small",), (), "Small Cap"),
    (("mid",), (), "Mid Cap"),
    (("large",), (), "Large Cap"),
    (("elss",), (), "ELSS"),
    (("tax sav",), (), "ELSS"),
    (("index",), (), "Index Fund"),
    (("nifty",), (), "Index Fund"),
    (("liquid",), (), "Liquid"),
    (("overnight",), (), "Liquid"),
    (("money market",), (), "Liquid"),
    (("short",), (), "Short Duration"),
    (("corporate",), (), "Corporate Bond"),
    (("banking",), (), "Banking & PSU"),
    (("psu",), (), "Banking & PSU"),
    (("gilt",), (), "Gilt"),
    (("arbitrage",), (), "Arbitrage"),
    (("aggressive", "hybrid"), (), "Aggressive Hybrid"),
    (("conservative", "hybrid"), (), "Conservative Hybrid"),
    (("hybrid",), (), "Aggressive Hybrid"),
    (("gold",), (), GOLD),
    (("debt",), (), GENERIC_DEBT),
    (("bond",), (), GENERIC_DEBT),
    (("income",), (), GENERIC_DEBT),
    (("equity",), (), GENERIC_EQUITY),
)


def map_category(text: str) -> str | None:
    """Map the user's wording ('flexi', 'small cap fund', 'debt') to a canonical category."""
    t = " ".join((text or "").lower().replace("-", " ").replace("_", " ").split())
    if not t:
        return None
    for name in ALL_CATEGORIES:
        if t == name.lower():
            return name
    norm = normalize_category(text.strip())
    if norm in ALL_CATEGORIES:
        return norm
    for needles, _, target in _KEYWORD_RULES:
        if all(n in t for n in needles):
            return target
    return None


def request_bucket(category: str) -> Bucket:
    if category in _GENERIC_BUCKET:
        return _GENERIC_BUCKET[category]
    return ALL_CATEGORIES[category].bucket


def build_spec(
    fund_requests: Iterable[tuple[str, int]],
    stock_count: int | None,
    trendy_stock_count: int | None,
    include_gold: bool | None,
) -> PortfolioSpec | None:
    """Validate raw extracted values. Returns None when the notes ask for nothing specific."""
    warnings: list[str] = []
    merged: dict[str, int] = {}
    for raw_cat, raw_count in fund_requests:
        cat = map_category(raw_cat)
        if cat is None:
            warnings.append(f"Could not match '{raw_cat}' to a fund category; it was ignored.")
            continue
        count = int(raw_count or 1)
        if count < 1:
            continue
        if count > 10:
            warnings.append(f"Asked for {count} {cat} funds; limited to 10.")
            count = 10
        merged[cat] = min(10, merged.get(cat, 0) + count)

    if include_gold and GOLD not in merged:
        merged[GOLD] = 1

    stocks = max(0, int(stock_count)) if stock_count is not None else None
    trendy = max(0, int(trendy_stock_count)) if trendy_stock_count is not None else None
    if stocks is not None and stocks > 30:
        warnings.append(f"Asked for {stocks} stocks; limited to 30.")
        stocks = 30
    if trendy and not stocks:
        stocks = trendy
    if trendy is not None and stocks is not None and trendy > stocks:
        warnings.append(
            f"Asked for {trendy} trendy stocks out of {stocks}; trendy count reduced to {stocks}."
        )
        trendy = stocks

    spec = PortfolioSpec(
        fund_requests=[FundRequest(category=c, count=n) for c, n in merged.items()],
        stock_count=stocks or None,
        trendy_stock_count=trendy or None,
        include_gold=include_gold,
        warnings=warnings,
    )
    return spec if (spec.fund_requests or spec.stock_count or warnings) else None


def active_custom(spec: PortfolioSpec | None) -> PortfolioSpec | None:
    return spec if spec is not None and spec.is_custom else None


def bucket_counts(spec: PortfolioSpec) -> dict[Bucket, int]:
    out: dict[Bucket, int] = {}
    for r in spec.fund_requests:
        b = request_bucket(r.category)
        out[b] = out.get(b, 0) + r.count
    return out


def custom_target(spec: PortfolioSpec, s: Settings) -> dict[Bucket, float]:
    """60/40 equity/debt when both are asked for; gold is carved out of debt; 'only X' wins."""
    counts = bucket_counts(spec)
    has_eq = Bucket.EQUITY in counts or bool(spec.stock_count)
    has_debt = Bucket.DEBT in counts
    has_gold = Bucket.GOLD in counts
    target = {b: 0.0 for b in (Bucket.EQUITY, Bucket.DEBT, Bucket.GOLD, Bucket.ALTERNATIVES)}
    gold = s.custom_gold_pct if has_gold else 0.0
    if has_eq and has_debt:
        target[Bucket.EQUITY] = s.custom_equity_pct
        target[Bucket.DEBT] = round(s.custom_debt_pct - gold, 6)
        target[Bucket.GOLD] = gold
    elif has_eq:
        target[Bucket.EQUITY] = round(100.0 - gold, 6)
        target[Bucket.GOLD] = gold
    elif has_debt:
        target[Bucket.DEBT] = round(100.0 - gold, 6)
        target[Bucket.GOLD] = gold
    else:
        target[Bucket.GOLD] = 100.0
    return target


def custom_band(target: dict[Bucket, float]) -> AllocationBand:
    return AllocationBand(ranges={b: (w, w) for b, w in target.items()})


def stock_sleeve_weight(spec: PortfolioSpec, equity_weight: float) -> float:
    """Share of the equity weight for direct stocks: all of it, or half when equity funds exist."""
    if not spec.stock_count:
        return 0.0
    has_equity_funds = bucket_counts(spec).get(Bucket.EQUITY, 0) > 0
    return round(equity_weight * (0.5 if has_equity_funds else 1.0), 6)


def effective_settings(s: Settings, spec: PortfolioSpec | None) -> Settings:
    """Settings for a run: defaults, or the user's counts and relaxed caps when the notes ask."""
    spec = active_custom(spec)
    if spec is None:
        return s
    per_bucket = bucket_counts(spec)
    return s.model_copy(
        update={
            "max_weight_per_instrument": 100.0,
            "max_weight_per_stock": 100.0,
            "max_sector_pct_of_stock_sleeve": 100.0,
            "max_instruments_per_bucket": max([s.max_instruments_per_bucket, *per_bucket.values()]),
            "max_research_picks": max(s.max_research_picks, spec.total_funds),
            "max_stocks": spec.stock_count or s.max_stocks,
            "max_stock_candidates": max(s.max_stock_candidates, spec.stock_count or 0),
        }
    )


def llm_budget_for(spec: PortfolioSpec | None, s: Settings) -> int:
    """LLM calls needed: 1 parse + up to 2 analyst attempts per requested fund; stocks use none."""
    spec = active_custom(spec)
    if spec is None:
        return s.max_llm_calls_per_run
    need = s.llm_budget_base + s.llm_budget_per_fund * spec.total_funds
    return min(s.llm_budget_cap, max(s.max_llm_calls_per_run, need))


def describe_spec(spec: PortfolioSpec | None) -> list[str]:
    """Plain-language lines for the 'here is what I understood' confirmation."""
    if spec is None:
        return []
    lines = [f"{r.count} × {r.category} fund{'s' if r.count > 1 else ''}" for r in spec.fund_requests]
    if spec.stock_count:
        trendy = spec.trendy_stock_count or 0
        extra = ""
        if trendy and trendy == spec.stock_count:
            extra = " (all from the web trend view)"
        elif trendy:
            extra = f" ({trendy} from the web trend view)"
        lines.append(f"{spec.stock_count} direct stocks{extra}")
    return lines
