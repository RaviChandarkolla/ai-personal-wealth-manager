"""M6 Mutual Fund Research Agent: the LLM picks from tool-returned candidates; code writes the facts.

The LLM can neither add facts nor invent funds: unknown scheme codes are dropped and free-text
reasons must pass the grounding validator (one regeneration, then the reason is stripped).
"""

import logging
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel

from ..config import Settings, get_settings
from ..data.store import FundRecord
from ..domain.subscores import fundamental_score, liquidity_cost_score
from ..guardrails.grounding import check_text
from ..guardrails.suitability import check_fund
from ..llm.base import LLMBudgetExceeded, LLMClient
from ..models.core import DossierEntry, DossierSection, Fact, Goal, Instrument, NoData
from ..models.enums import Bucket
from .tools import ResearchTools, ToolCall, facts_for_fund

log = logging.getLogger(__name__)

PROMPT_VERSION = "mf-research.v1"
MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class ResearcherConfig:
    asset_class: Bucket
    persona: str
    tools: tuple[str, ...]
    output_field: str
    categories: tuple[str, ...]
    max_picks: int = 3
    top_per_category: int = 3


class Pick(BaseModel):
    scheme_code: str
    reason: str = ""


class Selection(BaseModel):
    picks: list[Pick]


class ResearchResult(BaseModel):
    config_field: str
    section: DossierSection
    notes: dict[str, str] = {}
    dropped: list[str] = []
    tool_calls: list[ToolCall] = []
    facts: list[Fact] = []
    funnel_stats: dict[str, int] = {}


def _quant_rank_score(rec: FundRecord, fm: dict[str, float | str], s: Settings) -> float:
    """Stage-3 composite quantitative score (70% fundamental + 30% liquidity/cost)."""
    return round(
        0.70 * fundamental_score(fm, s) + 0.30 * liquidity_cost_score(fm, rec.category, s),
        4,
    )


def _candidate_table(cands: list[tuple[FundRecord, list[Fact]]]) -> str:
    lines = []
    for rec, facts in cands:
        vals = ", ".join(f"{f.name}={f.value}" for f in facts if not f.name.startswith("doc_"))
        lines.append(f"- {rec.code} | {rec.name} | {vals}")
    return "\n".join(lines)


@dataclass
class MFResearchAgent:
    llm: LLMClient
    tools: ResearchTools
    config: ResearcherConfig
    last_prompts: list[list[dict]] = field(default_factory=list)

    def _system(self, goal: Goal, max_picks: int) -> str:
        return (
            f"{self.config.persona}\n"
            f"Investor goal: horizon {goal.horizon_years:g} years, risk preference "
            f"{goal.risk_preference.value}. Select up to {max_picks} funds from the candidate "
            "list ONLY, using their scheme_code. For each, write a one-sentence reason that uses "
            "no numbers except values shown in the table and never names a fund that is not in "
            "the table. Do not introduce any other facts."
        )

    def run(self, goal: Goal, as_of: date) -> ResearchResult:
        cfg = self.config
        s = get_settings()
        excluded = [e.lower() for e in goal.excluded_categories]
        recs = [
            r
            for r in self.tools.call("query_funds", categories=list(cfg.categories))
            if not any(e in r.category.lower() or e in r.name.lower() for e in excluded)
        ]

        # Stage 1b: On-demand hydration of top eligible scheme_master funds for under-represented categories in real mode
        is_test_llm = getattr(self.llm, "model", "") in ("fake", "scripted")
        if (
            not is_test_llm
            and not any(r.code.startswith("DEMO") for r in recs)
            and hasattr(self.tools, "store")
            and self.tools.store is not None
        ):
            active_cats = [
                c for c in cfg.categories if not any(e in c.lower() for e in excluded)
            ]
            cat_counts: dict[str, int] = {c: 0 for c in active_cats}
            for r in recs:
                if r.category in cat_counts:
                    cat_counts[r.category] += 1
            sparse_cats = [c for c, cnt in cat_counts.items() if cnt < cfg.top_per_category]
            if sparse_cats and hasattr(self.tools.store, "eligible_master_funds"):
                unseeded = self.tools.store.eligible_master_funds(
                    categories=sparse_cats,
                    per_category_limit=cfg.top_per_category,
                    only_unseeded=True,
                )
                if unseeded:
                    from ..data.on_demand import ensure_nav
                    from ..data.stats import compute_all_stats

                    hydrated_any = False
                    seen_rec_codes = {r.code for r in recs}
                    for m_fund in unseeded:
                        if m_fund.code in seen_rec_codes:
                            continue
                        try:
                            nav_df = ensure_nav(self.tools.store, m_fund.code, fetch_benchmark=False)
                            if len(nav_df) >= 30:
                                hydrated_any = True
                        except Exception as exc:  # noqa: BLE001
                            log.debug("On-demand hydration skipped for %s: %s", m_fund.code, exc)
                    if hydrated_any:
                        compute_all_stats(self.tools.store, as_of=as_of)
                        fresh_recs = self.tools.store.query_funds(categories=list(cfg.categories))
                        for fr in fresh_recs:
                            if fr.code not in seen_rec_codes and not any(
                                e in fr.category.lower() or e in fr.name.lower() for e in excluded
                            ):
                                recs.append(fr)
                                seen_rec_codes.add(fr.code)

        if not recs:
            return ResearchResult(
                config_field=cfg.output_field, section=NoData(reason="no_candidates"),
                tool_calls=self.tools.calls,
            )

        cand_facts: dict[str, list[Fact]] = {}
        fact_maps: dict[str, dict[str, float | str]] = {}
        for r in recs:
            stats = self.tools.call("get_metrics", code=r.code)
            fs = facts_for_fund(r, stats, as_of)
            cand_facts[r.code] = fs
            fact_maps[r.code] = {f.name: f.value for f in fs}
        by_code = {r.code: r for r in recs}
        known = {r.name for r in recs} | {f"{c} Fund" for c in cfg.categories} | set(cfg.categories)

        # Stage 2: Hard suitability hygiene filter (Direct plan, TER <= ceiling, AUM >= floor, track record & horizon)
        is_demo_only = all(r.code.startswith("DEMO") for r in recs)
        suitable_recs = [
            r for r in recs if not check_fund(r.code, r.category, fact_maps[r.code], goal, s)
        ]
        if is_demo_only:
            shortlisted_recs = list(recs)
        else:
            pool_recs = suitable_recs if suitable_recs else recs

            # Stage 3: Category-balanced quantitative ranking (70% fundamental + 30% liquidity/cost)
            cat_order = list(dict.fromkeys([*cfg.categories, *(r.category for r in pool_recs)]))
            by_cat: dict[str, list[FundRecord]] = {c: [] for c in cat_order}
            for r in pool_recs:
                by_cat.setdefault(r.category, []).append(r)

            top_by_cat: list[list[FundRecord]] = []
            for c in cat_order:
                group = by_cat.get(c) or []
                if not group:
                    continue
                ranked_group = sorted(
                    group,
                    key=lambda item: (
                        -_quant_rank_score(item, fact_maps[item.code], s),
                        -float(fact_maps[item.code].get("pct_roll_3y", -1.0)),
                        item.code,
                    ),
                )
                top_by_cat.append(ranked_group[: max(1, cfg.top_per_category)])

            shortlisted_recs = []
            max_depth = max((len(g) for g in top_by_cat), default=0)
            for depth_idx in range(max_depth):
                for group in top_by_cat:
                    if depth_idx < len(group):
                        shortlisted_recs.append(group[depth_idx])
            if not shortlisted_recs:
                shortlisted_recs = list(pool_recs)

        messages = [
            {"role": "system", "content": self._system(goal, cfg.max_picks)},
            {"role": "user", "content": "Candidates:\n" + _candidate_table(
                [(r, cand_facts[r.code]) for r in shortlisted_recs])},
        ]
        dropped: list[str] = []
        picks: list[Pick] = []
        for attempt in range(MAX_ATTEMPTS):
            self.last_prompts.append(messages)
            try:
                from ..llm.langchain_client import trace_context

                with trace_context(
                    step="mf_researcher",
                    metadata={
                        "bucket": cfg.asset_class.value,
                        "attempt": attempt + 1,
                        "prompt_version": PROMPT_VERSION,
                        "universe_count": len(recs),
                        "suitable_count": len(suitable_recs),
                        "shortlisted_count": len(shortlisted_recs),
                    },
                ):
                    sel = self.llm.complete(messages, Selection)
            except LLMBudgetExceeded:
                log.warning("LLM call budget reached in researcher (%s); using deterministic selection", cfg.asset_class.value)
                if picks:
                    picks = [Pick(scheme_code=p.scheme_code, reason="") for p in picks]
                elif is_demo_only:
                    ranked = sorted(
                        cand_facts.keys(),
                        key=lambda c: -float(next((f.value for f in cand_facts[c] if f.name == "pct_roll_3y"), -1.0)),
                    )
                    picks = [Pick(scheme_code=c, reason="") for c in ranked[: cfg.max_picks]]
                else:
                    picks = [
                        Pick(scheme_code=r.code, reason="")
                        for r in shortlisted_recs[: cfg.max_picks]
                    ]
                break
            picks, dropped = [], []
            problems: list[str] = []
            for p in sel.picks[: cfg.max_picks]:
                if p.scheme_code not in by_code:
                    dropped.append(f"unknown scheme_code {p.scheme_code!r}")
                    continue
                if p.reason:
                    rep = check_text(p.reason, cand_facts[p.scheme_code], known)
                    if not rep.ok:
                        problems.append(f"{p.scheme_code}: {rep.describe()}")
                        if attempt == MAX_ATTEMPTS - 1:
                            dropped.append(f"reason stripped for {p.scheme_code}: {rep.describe()}")
                            p = Pick(scheme_code=p.scheme_code, reason="")
                picks.append(p)
            if not problems and not any(d.startswith("unknown") for d in dropped):
                break
            if attempt < MAX_ATTEMPTS - 1:
                feedback = "; ".join(problems + dropped)
                log.warning("grounding failure, regenerating: %s", feedback)
                messages = messages + [
                    {"role": "assistant", "content": sel.model_dump_json()},
                    {"role": "user", "content": f"Rejected: {feedback}. Fix and answer again."},
                ]

        seen: set[str] = set()
        entries: list[DossierEntry] = []
        notes: dict[str, str] = {}
        facts: list[Fact] = []
        for p in picks:
            if p.scheme_code in seen:
                continue
            seen.add(p.scheme_code)
            rec = by_code[p.scheme_code]
            docs = self.tools.call(
                "retrieve_docs", query=f"{rec.name} exit load risks", isin=rec.isin, k=1
            ) if "retrieve_docs" in self.config.tools else []
            fs = facts_for_fund(rec, self.tools.call("get_metrics", code=rec.code), as_of, docs)
            entries.append(
                DossierEntry(
                    instrument=Instrument(
                        id=rec.code, name=rec.name, asset_class=rec.bucket,
                        category=rec.category, plan_type=rec.plan,
                    ),
                    facts=fs,
                )
            )
            facts.extend(fs)
            if p.reason:
                notes[rec.code] = p.reason
        section: DossierSection = entries if entries else NoData(reason="no_grounded_candidates")
        funnel_stats = {
            "universe_count": len(recs),
            "suitable_count": len(suitable_recs),
            "shortlisted_count": len(shortlisted_recs),
            "picked_count": len(entries),
            "categories_covered": len({r.category for r in shortlisted_recs}),
        }
        return ResearchResult(
            config_field=cfg.output_field, section=section, notes=notes, dropped=dropped,
            tool_calls=list(self.tools.calls), facts=facts, funnel_stats=funnel_stats,
        )


def default_config(bucket: Bucket, categories: list[str]) -> ResearcherConfig:
    return ResearcherConfig(
        asset_class=bucket,
        persona=f"You are a mutual-fund researcher covering the {bucket.value} bucket. "
        "You gather evidence only; you do not score or decide.",
        tools=ResearchTools.ALL,
        output_field="mf",
        categories=tuple(categories),
        max_picks=get_settings().max_research_picks,
    )
