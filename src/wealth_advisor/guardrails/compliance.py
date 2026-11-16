"""Compliance gate: suitability engine + post-generation grounding validator + disclosures."""

import re
from collections.abc import Mapping

from pydantic import BaseModel

from ..agents.decision import DecisionResult
from ..config import Settings, get_settings
from ..domain.portfolio_spec import effective_settings
from ..domain.rulesets import active_rule_set
from ..models.core import DossierEntry, Goal
from .disclosures import DEMO_DISCLOSURE, DISCLOSURES, STOCK_DISCLOSURE
from .grounding import check_text
from .suitability import PlanLine, SuitabilityError, Violation, verify_plan

_BANNED_TERMS = re.compile(r"\b(guaranteed|risk[- ]free|assured|multibagger|sure[- ]shot)\b", re.IGNORECASE)


def scrub(text: str) -> tuple[str, list[str]]:
    """Strip promotional/non-compliant terms from generated text."""
    found = [m.group(0) for m in _BANNED_TERMS.finditer(text)]
    cleaned = _BANNED_TERMS.sub("historically observed", text)
    return cleaned.strip(), found


class ComplianceReport(BaseModel):
    disclosures: list[str]
    violations: list[Violation] = []


def validate_output(result: DecisionResult, entries: list[DossierEntry]) -> list[Violation]:
    """Second hallucination layer: rationale numbers/names/citations must trace to the dossier."""
    by_id = {e.instrument.id: e for e in entries}
    known = {e.instrument.name for e in entries} | {e.instrument.category for e in entries} | {e.instrument.id for e in entries}
    known |= {f"{e.instrument.category} Fund" for e in entries}
    for e in entries:
        for f in e.facts:
            if f.name == "sector" and isinstance(f.value, str):
                known.add(f.value)
    out: list[Violation] = []
    recs = {r.instrument_id: r for r in result.record.recommendations}
    for row in result.rows:
        entry = by_id.get(row.instrument_id)
        if entry is None:
            out.append(Violation(instrument_id=row.instrument_id, rule="unknown_instrument",
                                 detail="not present in the research dossier"))
            continue
        rec = recs.get(row.instrument_id)
        if rec is None or not rec.citations or not row.citation_ids:
            out.append(Violation(instrument_id=row.instrument_id, rule="uncited", detail="no citation"))
        rep = check_text(row.rationale, entry.facts, known)
        if not rep.ok:
            out.append(Violation(instrument_id=row.instrument_id, rule="ungrounded",
                                 detail=rep.describe()))
    return out


def review(
    result: DecisionResult, entries: list[DossierEntry], goal: Goal, raise_on_violation: bool = True,
    s: Settings | None = None,
) -> ComplianceReport:
    s = effective_settings(s or get_settings(), goal.spec)
    facts_by_id: Mapping[str, Mapping[str, float | str]] = {
        e.instrument.id: {f.name: f.value for f in e.facts} for e in entries
    }
    cat = {e.instrument.id: e.instrument.category for e in entries}
    by_id = {e.instrument.id: e for e in entries}
    lines = [
        PlanLine(instrument_id=r.instrument_id, bucket=r.bucket, category=cat.get(r.instrument_id, r.category),
                 weight=r.weight)
        for r in result.rows
    ]
    violations = verify_plan(lines, facts_by_id, goal, result.band, s) + validate_output(result, entries)
    if violations and raise_on_violation:
        raise SuitabilityError(violations)
    disclosures = list(DISCLOSURES)
    if any(c.source == "synthetic_demo" for r in result.record.recommendations for c in r.citations):
        disclosures.append(DEMO_DISCLOSURE)

    active_set = active_rule_set()
    if active_set == "set1":
        disclosures.append("Fund selection rule set: Set 1 · Default (standard baseline criteria).")
    elif active_set == "set2":
        disclosures.append(
            "Fund selection rule set: Set 2 · Magic of MF (16 categories, positive alpha filter, maturity matching)."
        )
    elif active_set == "combined":
        disclosures.append(
            "Fund selection rule set: Combined · Set 1 + 2 (merged stricter floors/ceilings, overlapping allocation bands)."
        )
        disclosures.append(
            "Combined merge note: Stricter 500 Cr AUM floor, 4-star rating & positive alpha enforced; blended weights applied."
        )

    has_elss = any(
        r.category == "ELSS"
        or getattr(r, "category", "") == "ELSS"
        or (by_id.get(r.instrument_id) and by_id[r.instrument_id].instrument.category == "ELSS")
        for r in result.rows
    )
    if has_elss:
        disclosures.append(
            "Statutory lock-in: ELSS funds require a mandatory 3-year lock-in period for every purchase or SIP installment."
        )

    has_direct_equity = any(
        r.category == "Direct Equity"
        or (by_id.get(r.instrument_id) and by_id[r.instrument_id].instrument.category == "Direct Equity")
        for r in result.rows
    )
    if has_direct_equity:
        disclosures.append(STOCK_DISCLOSURE)
    if any(
        any(f.name == "web_trend_score" for f in e.facts)
        for e in entries
        if e.instrument.id in {r.instrument_id for r in result.rows}
    ):
        disclosures.append(
            "Trend-view stocks are chosen from sector and news research (not price data) and are "
            "not fundamentally screened; review each company's financials before investing."
        )

    return ComplianceReport(disclosures=disclosures, violations=violations)
