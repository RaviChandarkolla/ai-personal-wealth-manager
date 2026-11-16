"""M7 Analyst Agent: code computes scores/confidence; the LLM only writes cited pros/cons.

Pros/cons without valid fact citations are rejected (one retry, then deterministic bullets).
Conflicts are always surfaced as cons, never averaged away.
"""

import logging
import threading
from datetime import date, timedelta

from pydantic import BaseModel

from ..config import Settings, get_settings
from ..domain.scoring import score_instrument
from ..domain.subscores import (
    confidence,
    detect_conflicts,
    fit_score,
    fundamental_score,
    liquidity_cost_score,
    news_score,
    sentiment_score,
)
from ..guardrails.grounding import check_text
from ..llm.base import LLMBudgetExceeded, LLMClient
from ..models.core import AnalystVerdict, DossierEntry, Fact, Goal
from ..models.enums import RiskCategory

log = logging.getLogger(__name__)

PROMPT_VERSION = "analyst.v1"
NEUTRAL = 50.0  # news/sentiment are not researched in the MVP; neutral placeholder


class AnalystProse(BaseModel):
    pros: list[str]
    cons: list[str]
    change_conditions: list[str] = []


def _fact_map(facts: list[Fact]) -> dict[str, float | str]:
    return {f.name: f.value for f in facts}


def _id(facts: list[Fact], name: str) -> str | None:
    return next((f.id for f in facts if f.name == name), None)


def _fallback(facts: list[Fact]) -> AnalystProse:
    fm = _fact_map(facts)
    pros, cons, cond = [], [], []
    if "pct_roll_3y" in fm:
        pros.append(f"Peer percentile for 3-year rolling returns is {fm['pct_roll_3y']} "
                    f"[{_id(facts, 'pct_roll_3y')}]")
    if "expense_ratio" in fm:
        pros.append(f"Expense ratio is {fm['expense_ratio']}% [{_id(facts, 'expense_ratio')}]")
    if "max_drawdown" in fm:
        cons.append(f"Maximum drawdown over the history was {fm['max_drawdown']}% "
                    f"[{_id(facts, 'max_drawdown')}]")
    anchor = _id(facts, "rating") or _id(facts, "expense_ratio") or facts[0].id
    cond.append(f"Re-evaluate if the rating or expense ratio changes materially [{anchor}]")
    if not pros:
        pros.append(f"Category is {fm.get('category', 'unknown')} [{_id(facts, 'category') or facts[0].id}]")
    if not cons:
        cons.append(f"Limited data available [{facts[0].id}]")
    return AnalystProse(pros=pros, cons=cons, change_conditions=cond)


class AnalystAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.fallbacks: list[str] = []
        self._lock = threading.Lock()

    def _record_fallback(self, instrument_id: str) -> None:
        with self._lock:
            self.fallbacks.append(instrument_id)

    def _prose(self, entry: DossierEntry, goal: Goal, conflicts: list[str]) -> AnalystProse:
        facts = entry.facts
        known = {entry.instrument.name, entry.instrument.category, f"{entry.instrument.category} Fund"}
        table = "\n".join(f"[{f.id}] {f.name} = {f.value}" for f in facts if not f.name.startswith("doc_"))
        messages = [
            {"role": "system", "content": (
                "You are an investment analyst. Using ONLY the evidence lines below, write pros, "
                "cons and change_conditions (conditions under which the view would change). Every "
                "bullet must end with the supporting fact id(s) in square brackets, e.g. "
                "[CODE:expense_ratio]. Field notes: pct_roll_3y and pct_cagr_5y are within-category "
                "peer percentiles (0-100, higher beats more peers), NOT percentage of periods; "
                "cagr_3y, cagr_5y, roll_3y_median, volatility, max_drawdown and expense_ratio are "
                "in %; aum_cr is AUM in Rs Crore. Use no number that is not in the evidence and "
                "mention no other fund. Do not give a score or an action."
            )},
            {"role": "user", "content": (
                f"Fund: {entry.instrument.name}\nInvestor horizon: {goal.horizon_years:g} years, "
                f"risk preference {goal.risk_preference.value}\nDetected conflicts: "
                f"{conflicts or 'none'}\nEvidence:\n{table}")},
        ]
        for attempt in range(2):
            try:
                from ..llm.langchain_client import trace_context

                with trace_context(
                    step="analyst",
                    metadata={
                        "instrument_id": entry.instrument.id,
                        "instrument_name": entry.instrument.name,
                        "category": entry.instrument.category,
                        "attempt": attempt + 1,
                        "prompt_version": PROMPT_VERSION,
                    },
                ):
                    prose = self._llm.complete(messages, AnalystProse)
            except LLMBudgetExceeded:
                log.warning("LLM call budget reached on %s; using deterministic bullets", entry.instrument.id)
                self._record_fallback(entry.instrument.id)
                return _fallback(facts)
            bullets = prose.pros + prose.cons + prose.change_conditions
            problems = []
            for b in bullets:
                rep = check_text(b, facts, known, require_citation_ids=True)
                if not rep.ok:
                    problems.append(f"{b!r}: {rep.describe()}")
            if prose.pros and prose.cons and not problems:
                return prose
            if attempt == 0:
                messages = messages + [
                    {"role": "assistant", "content": prose.model_dump_json()},
                    {"role": "user", "content": "Rejected: " + "; ".join(problems or ["need pros and cons"])},
                ]
        self._record_fallback(entry.instrument.id)
        log.warning("analyst prose rejected for %s; using deterministic bullets", entry.instrument.id)
        return _fallback(facts)

    def analyse(
        self,
        entry: DossierEntry,
        goal: Goal,
        risk_category: RiskCategory,
        as_of: date,
        *,
        use_llm: bool = True,
        s: Settings | None = None,
    ) -> AnalystVerdict:
        s = s or get_settings()
        fm = _fact_map(entry.facts)
        cat = entry.instrument.category
        sub = {
            "fundamental": fundamental_score(fm, s),
            "fit": fit_score(fm, cat, risk_category.value, goal.horizon_years, s),
            "news": news_score(fm),
            "sentiment": sentiment_score(fm),
            "liquidity_cost": liquidity_cost_score(fm, cat, s),
        }
        conflicts = detect_conflicts(fm, cat, s)
        cutoff = as_of - timedelta(days=s.doc_max_age_days)
        stale = sum(1 for f in entry.facts if f.name.startswith("doc_") and f.citation.as_of < cutoff)
        conflict_text = [
            f"Conflict: {desc} [{', '.join(i for n in names if (i := _id(entry.facts, n)))}]"
            for desc, names in conflicts
        ]
        score = score_instrument(sub, s.analyst_weights)
        conf = confidence(fm, len(conflicts), stale)
        if use_llm:
            prose = self._prose(entry, goal, [c.split(" [")[0] for c in conflict_text])
        else:
            self._record_fallback(entry.instrument.id)
            prose = _fallback(entry.facts)
        return AnalystVerdict(
            instrument_id=entry.instrument.id,
            pros=prose.pros,
            cons=conflict_text + prose.cons,
            score=score,
            sub_scores=sub,
            confidence=conf,
            change_conditions=prose.change_conditions,
        )
