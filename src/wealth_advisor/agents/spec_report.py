"""Requested-vs-delivered report for portfolios the user spelled out in the AI notes."""

from __future__ import annotations

from collections.abc import Mapping

from ..config import Settings
from ..domain.portfolio_spec import describe_spec
from ..guardrails.suitability import overridden_violations
from ..models.core import DossierEntry, Goal, PortfolioSpec
from ..models.enums import Bucket
from .decision import DecisionResult


def build_spec_report(
    spec: PortfolioSpec,
    decision: DecisionResult,
    entries: list[DossierEntry],
    goal: Goal,
    s: Settings,
    *,
    default_s: Settings,
    profile_category: str,
    selector_shortfalls: list[str],
    fund_delivered: list[tuple[str, int, list[str]]],
    web_symbols: list[str],
    warnings: list[str],
) -> dict:
    """What was asked, what the plan holds, what fell short, and which rules the request waived."""
    by_id: Mapping[str, DossierEntry] = {e.instrument.id: e for e in entries}
    rows = decision.rows
    fund_rows = [r for r in rows if r.instrument_type != "stock"]
    stock_rows = [r for r in rows if r.instrument_type == "stock"]

    shortfalls = list(selector_shortfalls)
    delivered: list[dict] = []
    for category, count, codes in fund_delivered:
        got = [r for r in fund_rows if r.instrument_id in set(codes)]
        delivered.append({"request": f"{count} × {category}", "delivered": len(got),
                          "instruments": [r.name for r in got]})
        if len(got) < count and not any(category in msg for msg in shortfalls):
            shortfalls.append(
                f"Requested {count} {category} fund(s); the plan holds {len(got)}."
            )
        delivered.append({"request": f"{spec.stock_count} stocks", "delivered": len(stock_rows),
                          "instruments": [r.name for r in stock_rows]})
        if len(stock_rows) < spec.stock_count and not any("stock" in m.lower() for m in shortfalls):
            shortfalls.append(
                f"Requested {spec.stock_count} stocks; the plan holds {len(stock_rows)}."
            )

    overrides: list[str] = [
        f"Allocation follows your request ({_split_text(decision)}) instead of the "
        f"{profile_category} risk-profile bands."
    ]
    for r in rows:
        e = by_id.get(r.instrument_id)
        if e is None:
            continue
        facts = {f.name: f.value for f in e.facts}
        for v in overridden_violations(r.instrument_id, r.category, facts, goal, default_s):
            overrides.append(f"{r.name}: {v.detail} (allowed because you asked for it).")
        cap = default_s.max_weight_per_stock if r.instrument_type == "stock" else default_s.max_weight_per_instrument
        if r.weight > cap + 1e-6:
            overrides.append(
                f"{r.name}: {r.weight:.1f}% of the portfolio is above the usual {cap:g}% single-holding cap."
            )

    return {
        "requested": describe_spec(spec),
        "delivered": delivered,
        "shortfalls": shortfalls,
        "overrides": overrides,
        "warnings": [*spec.warnings, *warnings],
        "web_trend_symbols": web_symbols,
        "weights": {r.value: round(sum(x.weight for x in rows if x.bucket == r), 2) for r in Bucket},
    }


def _split_text(decision: DecisionResult) -> str:
    parts = []
    for b in (Bucket.EQUITY, Bucket.DEBT, Bucket.GOLD):
        w = sum(r.weight for r in decision.rows if r.bucket == b)
        if w > 0:
            parts.append(f"{w:.0f}% {b.value}")
    return ", ".join(parts) or "no allocation"
