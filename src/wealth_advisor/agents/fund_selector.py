"""Deterministic per-category fund selection for portfolios the user spelled out in the AI notes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..config import Settings
from ..domain.categories import CATEGORIES
from ..domain.portfolio_spec import GENERIC_DEBT, GENERIC_EQUITY, request_bucket
from ..guardrails.suitability import check_fund
from ..models.core import DossierEntry, Goal, Instrument, PortfolioSpec
from ..models.enums import Bucket
from .mf_researcher import _quant_rank_score
from .tools import ResearchTools, facts_for_fund

PROMPT_VERSION = "fund-selector.v1-deterministic"


@dataclass
class FundSelection:
    entries: list[DossierEntry] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)
    shortfalls: list[str] = field(default_factory=list)
    research_log: list[dict] = field(default_factory=list)
    delivered: list[tuple[str, int, list[str]]] = field(default_factory=list)  # (category, count, fund codes)


def _categories_for_request(category: str) -> list[str]:
    if category == GENERIC_DEBT:
        return [c for c, i in CATEGORIES.items() if i.bucket == Bucket.DEBT]
    if category == GENERIC_EQUITY:
        return [c for c, i in CATEGORIES.items() if i.bucket == Bucket.EQUITY]
    return [category]


def select_funds(
    spec: PortfolioSpec,
    tools: ResearchTools,
    goal: Goal,
    as_of: date,
    s: Settings,
) -> FundSelection:
    """Best-ranked funds per requested category; generic 'Debt' ranks every debt category together."""
    out = FundSelection()
    excluded = [e.lower() for e in goal.excluded_categories]
    taken: set[str] = set()
    per_bucket: dict[Bucket, dict] = {}

    # Named categories first so a generic request cannot take their funds.
    ordered = sorted(spec.fund_requests, key=lambda r: r.category in (GENERIC_DEBT, GENERIC_EQUITY))
    for req in ordered:
        bucket = request_bucket(req.category)
        log = per_bucket.setdefault(
            bucket, {"bucket": bucket.value, "notes": {}, "dropped": [], "funnel_stats": {}},
        )
        recs = [
            r for r in tools.call("query_funds", categories=_categories_for_request(req.category))
            if r.code not in taken
            and not any(e in r.category.lower() or e in r.name.lower() for e in excluded)
        ]
        scored: list[tuple[float, float, str, object, list]] = []
        failed: list[str] = []
        for r in recs:
            facts = facts_for_fund(r, tools.call("get_metrics", code=r.code), as_of)
            fm = {f.name: f.value for f in facts}
            problems = check_fund(r.code, r.category, fm, goal, s)
            if problems:
                failed.append(f"{r.name}: {problems[0].rule}")
                log["dropped"].append({"code": r.code, "reason": problems[0].detail})
                continue
            scored.append((
                -_quant_rank_score(r, fm, s),
                -float(fm.get("pct_roll_3y", -1.0)),
                r.code,
                r,
                facts,
            ))
        scored.sort(key=lambda x: x[:3])
        chosen = scored[: req.count]
        codes: list[str] = []
        out.delivered.append((req.category, req.count, codes))
        for _, _, _, rec, facts in chosen:
            taken.add(rec.code)
            codes.append(rec.code)
            out.entries.append(DossierEntry(
                instrument=Instrument(
                    id=rec.code, name=rec.name, asset_class=rec.bucket,
                    category=rec.category, plan_type=rec.plan,
                ),
                facts=facts,
            ))
            note = f"Best-ranked {rec.category} fund by the quantitative score among eligible funds."
            out.notes[rec.code] = note
            log["notes"][rec.code] = note
        if len(chosen) < req.count:
            why = f" ({'; '.join(failed[:3])} did not meet the data rules)" if failed else ""
            if not recs:
                why = " (no funds of this category are loaded)"
            out.shortfalls.append(
                f"Requested {req.count} {req.category} fund(s); only {len(chosen)} available{why}."
            )
        log["funnel_stats"][req.category] = len(recs)

    for bucket, log in per_bucket.items():
        log["tool_calls"] = [c.model_dump() for c in tools.calls]
        out.research_log.append(log)
    return out
