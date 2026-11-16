"""End-to-end MVP: profile -> research -> analyse -> decide -> compliance, all audited."""

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date

from pydantic import BaseModel

from .agents.analyst import PROMPT_VERSION as ANALYST_PV
from .agents.analyst import AnalystAgent
from .agents.decision import PROMPT_VERSION as DECISION_PV
from .agents.decision import DecisionResult, decide
from .agents.mf_researcher import PROMPT_VERSION as RESEARCH_PV
from .agents.mf_researcher import MFResearchAgent, default_config
from .agents.profiling import PROMPT_VERSION as PROFILING_PV
from .agents.profiling import ProfileAssumptions, ProfilingAgent
from .agents.stock_researcher import StockResearcherAgent
from .agents.fund_selector import select_funds
from .agents.spec_report import build_spec_report
from .agents.web_stock_picker import select_stocks
from .agents.tools import ResearchTools
from .audit.log import AuditLog, ProfileStore
from .config import get_settings
from .data.news import FundNewsSummary, enrich_entries_with_news
from .data.store import MetadataStore
from .data.trend_view import load_trend_view
from .domain.allocation import target_allocation
from .domain.categories import categories_for
from .domain.portfolio_spec import active_custom, custom_target, effective_settings, llm_budget_for
from .domain.rulesets import COMBINED_PROVENANCE, active_rule_set, use_rule_set
from .guardrails.compliance import ComplianceReport, review
from .guardrails.suitability import SuitabilityError, check_fund
from .llm.base import BudgetedLLM, LLMClient
from .llm.langchain_client import flush_langfuse, trace_context
from .logging_setup import new_run_id
from .models.core import (
    AnalystVerdict,
    DossierEntry,
    Goal,
    NoData,
    PortfolioSpec,
    ResearchDossier,
    UserFinancialProfile,
)
from .models.enums import Bucket
from .rag.index import VectorIndex

log = logging.getLogger(__name__)


class AdviceResult(BaseModel):
    run_id: str
    profile: UserFinancialProfile
    goal: Goal
    target: dict[Bucket, float]
    dossier: ResearchDossier
    verdicts: list[AnalystVerdict]
    decision: DecisionResult
    compliance: ComplianceReport
    assumptions: ProfileAssumptions
    rule_set: str = "set1"
    provenance: list[dict] | None = None
    news_summaries: dict[str, FundNewsSummary] = {}
    research_log: list[dict] = []
    include_stocks: bool = False
    stock_rule_set: str = "set2"
    trend_based_stocks: bool = False
    trend_regime: dict = {}
    stock_filters_applied: dict[str, float | None] = {}
    stock_sync_as_of: str | None = None
    stock_warnings: list[str] = []
    portfolio_spec: PortfolioSpec | None = None
    spec_report: dict = {}

    def build_context(self) -> dict:
        return build_advice_context(self)


def build_advice_context(res: AdviceResult) -> dict:
    """Build compact run context JSON for history persistence and grounded follow-up Q&A."""
    from .orchestration.supervisor import all_dossier_entries

    s = effective_settings(get_settings(res.rule_set), res.portfolio_spec)
    entries = all_dossier_entries(res.dossier)
    research_notes: dict[str, str] = {}

    research_dropped: list[dict] = []
    for run in res.research_log:
        for code, note in (run.get("notes") or {}).items():
            research_notes[str(code)] = str(note)
        for d in run.get("dropped") or []:
            if isinstance(d, dict):
                research_dropped.append(d)

    shortlisted_by_bucket: dict[str, list[str]] = {}
    candidates: list[dict] = []
    for e in entries:
        b_val = e.instrument.asset_class.value
        shortlisted_by_bucket.setdefault(b_val, []).append(e.instrument.id)
        candidates.append(
            {
                "id": e.instrument.id,
                "name": e.instrument.name,
                "bucket": b_val,
                "category": e.instrument.category,
                "plan_type": e.instrument.plan_type,
                "facts": {f.name: f.value for f in e.facts},
                "fact_list": [f.model_dump(mode="json") for f in e.facts],
                "research_note": research_notes.get(e.instrument.id, ""),
            }
        )

    return {
        "run_id": res.run_id,
        "rule_set": res.rule_set,
        "include_stocks": res.include_stocks,
        "stock_rule_set": res.stock_rule_set,
        "trend_based_stocks": res.trend_based_stocks,
        "trend_regime": res.trend_regime,
        "stock_filters_applied": res.stock_filters_applied,
        "stock_sync_as_of": res.stock_sync_as_of,
        "stock_warnings": res.stock_warnings,
        "portfolio_spec": res.portfolio_spec.model_dump(mode="json") if res.portfolio_spec else None,
        "spec_report": res.spec_report,
        "risk_category": res.profile.category.value,
        "goal": res.goal.model_dump(mode="json"),
        "target": {b.value: w for b, w in res.target.items()},
        "candidates": candidates,
        "shortlisted_ids": [e.instrument.id for e in entries],
        "shortlisted_by_bucket": shortlisted_by_bucket,
        "research_log": res.research_log,
        "research_notes": research_notes,
        "research_dropped": research_dropped,
        "scores": {v.instrument_id: v.score for v in res.verdicts},
        "verdicts": {v.instrument_id: v.model_dump(mode="json") for v in res.verdicts},
        "recommended_ids": [r.instrument_id for r in res.decision.rows],
        "recommendations": [
            {
                "instrument_id": r.instrument_id,
                "name": r.name,
                "bucket": r.bucket.value,
                "category": r.category,
                "score": r.score,
                "weight": r.weight,
                "amount": r.amount,
            }
            for r in res.decision.rows
        ],
        "rejected": [v.model_dump(mode="json") for v in res.decision.rejected],
        "escalations": list(res.decision.escalations),
        "unallocated_weight": res.decision.unallocated_weight,
        "sizing_limits": {
            "max_weight_per_instrument": s.max_weight_per_instrument,
            "max_instruments_per_bucket": s.max_instruments_per_bucket,
            "min_ticket": s.min_ticket,
            "max_research_picks": s.max_research_picks,
            "buy_min_score": s.buy_min_score,
            "min_confidence": s.min_confidence,
        },
    }


def advise(
    goal: Goal,
    llm: LLMClient,
    conn: sqlite3.Connection,
    *,
    assumptions: ProfileAssumptions | None = None,
    as_of: date | None = None,
    run_id: str | None = None,
    rule_set: str | None = None,
    supervisor: object | None = None,
    include_stocks: bool = False,
    stock_rule_set: str | None = None,
    trend_based_stocks: bool = False,
    min_market_cap_cr: float | None = None,
    min_traded_value_cr: float | None = None,
    stock360_veto: bool | None = None,
    stock360_min_sell_signals: int | None = None,
) -> AdviceResult:
    with use_rule_set(rule_set) as active_set:
        s_default = get_settings()
        spec = active_custom(goal.spec)
        budget = llm_budget_for(spec, s_default)
        budgeted_llm = llm if isinstance(llm, BudgetedLLM) else BudgetedLLM(llm, budget)
        budgeted_llm.max_calls = max(budgeted_llm.max_calls, budget)
        s = effective_settings(s_default, spec)
        if spec is not None:
            include_stocks = bool(spec.stock_count)
        eff_stock_rule_set = stock_rule_set if stock_rule_set in ("set1", "set2") else s.stock_rule_set
        store, index = MetadataStore(conn), VectorIndex(conn)
        audit, profiles = AuditLog(conn), ProfileStore(conn)
        as_of = as_of or store.latest_nav_date()
        if as_of is None:
            raise RuntimeError(
                "No fund data loaded. Run `wealth-advisor seed-demo` (synthetic) or `wealth-advisor seed-real` (real) first."
            )
        rid = run_id or new_run_id()
        provenance_data = COMBINED_PROVENANCE if active_set == "combined" else None

        with trace_context(run_id=rid):
            # M3: profile
            profile, assumptions = ProfilingAgent.build_profile(goal, assumptions)
            profiles.save(profile)
            target = target_allocation(profile.allocation_band)
            if spec is not None:
                target = custom_target(spec, s)
            profile_payload = {
                "profile_id": profile.profile_id, "version": profile.version,
                "fund_rule_set": active_set,
                "portfolio_spec": spec.model_dump(mode="json") if spec else None,
                "include_stocks": include_stocks,
                "stock_rule_set": eff_stock_rule_set,
                "stock_filters": {
                    "min_market_cap_cr": min_market_cap_cr,
                    "min_traded_value_cr": min_traded_value_cr,
                },
                "goal": goal.model_dump(mode="json"), "assumptions": assumptions.model_dump(mode="json"),
                "risk_inputs": profile.inputs["risk_inputs"], "risk_weights": s.risk_weights.model_dump(),
                "risk": {"score": profile.risk_score, "category": profile.category.value},
                "band": profile.allocation_band.model_dump(mode="json"),
                "target": {b.value: w for b, w in target.items()},
            }
            if active_set == "combined":
                profile_payload["provenance"] = provenance_data
            audit.append(rid, "profiling", "profile", profile_payload, prompt_version=PROFILING_PV)

            # M6 / M12: research via supervisor (if provided) or per-bucket MF researcher
            entries: list[DossierEntry] = []
            research_log: list[dict] = []
            news_summaries: dict[str, FundNewsSummary] = {}
            fund_shortfalls: list[str] = []
            fund_delivered: list[tuple[str, int, list[str]]] = []
            if spec is not None:
                fsel = select_funds(spec, ResearchTools(store, index, as_of=as_of), goal, as_of, s)
                entries = list(fsel.entries)
                research_log = list(fsel.research_log)
                fund_shortfalls = fsel.shortfalls
                fund_delivered = fsel.delivered
                live_news = bool(
                    entries
                    and not any(e.instrument.id.startswith("DEMO") for e in entries)
                    and getattr(budgeted_llm, "model", "") != "scripted"
                )
                news_sec, sent_sec, news_summaries = enrich_entries_with_news(
                    entries, as_of=as_of, fetch_live=live_news
                )
                dossier = ResearchDossier(
                    mf=entries if entries else NoData(reason="no_candidates"),
                    news_context=news_sec if news_sec else NoData(reason="no_candidates"),
                    sentiment=sent_sec if sent_sec else NoData(reason="no_candidates"),
                )
            elif supervisor is not None:
                from .orchestration.supervisor import ResearchSupervisor, all_dossier_entries

                if isinstance(supervisor, ResearchSupervisor):
                    if supervisor.store is None:
                        supervisor.store = store
                    if supervisor.index is None:
                        supervisor.index = index
                    if supervisor.llm is None:
                        supervisor.llm = budgeted_llm
                    sup_res = supervisor.run(goal, as_of, profile=profile, target=target)
                    dossier = sup_res.dossier
                    entries = all_dossier_entries(dossier)
                    research_log = sup_res.research_log
                    _, _, news_summaries = enrich_entries_with_news(
                        entries, as_of=as_of, fetch_live=False
                    )
                else:
                    raise TypeError(f"Expected ResearchSupervisor, got {type(supervisor)!r}")
            else:
                for bucket, weight in target.items():
                    if weight <= 0:
                        continue
                    tools = ResearchTools(store, index, as_of=as_of)
                    agent = MFResearchAgent(budgeted_llm, tools, default_config(bucket, categories_for(bucket)))
                    res = agent.run(goal, as_of)
                    if isinstance(res.section, NoData):
                        research_log.append({"bucket": bucket.value, "no_data": res.section.reason})
                    else:
                        entries.extend(res.section)
                    research_log.append({
                        "bucket": bucket.value, "notes": res.notes, "dropped": res.dropped,
                        "tool_calls": [c.model_dump() for c in res.tool_calls],
                        "funnel_stats": res.funnel_stats,
                    })
                live_news = bool(
                    entries
                    and not any(e.instrument.id.startswith("DEMO") for e in entries)
                    and getattr(budgeted_llm, "model", "") != "scripted"
                )
                news_sec, sent_sec, news_summaries = enrich_entries_with_news(
                    entries, as_of=as_of, fetch_live=live_news
                )
                dossier = ResearchDossier(
                    mf=entries if entries else NoData(reason="no_candidates"),
                    news_context=news_sec if news_sec else NoData(reason="no_candidates"),
                    sentiment=sent_sec if sent_sec else NoData(reason="no_candidates"),
                )

            stock_filters_applied: dict[str, float | None] = {
                "min_market_cap_cr": min_market_cap_cr,
                "min_traded_value_cr": min_traded_value_cr,
            }
            stock_sync_as_of: str | None = None
            stock_warnings: list[str] = []
            trend_regime: dict = {}
            stock_entries: list[DossierEntry] = []
            stock_verdicts: list[AnalystVerdict] = []
            stock_shortfalls: list[str] = []
            web_symbols: list[str] = []

            if include_stocks and spec is not None:
                data_mode = (
                    "synthetic"
                    if any(e.instrument.id.startswith("DEMO") for e in entries)
                    else s.data_mode
                )
                view = load_trend_view(s.trend_view_path)
                ssel = select_stocks(
                    spec, goal, conn, view=view, data_mode=data_mode,
                    risk_category=profile.category, as_of=as_of, stock_rule_set=eff_stock_rule_set,
                    min_market_cap_cr=min_market_cap_cr, min_traded_value_cr=min_traded_value_cr, s=s,
                )
                stock_entries = ssel.entries
                stock_verdicts = ssel.verdicts
                stock_shortfalls = ssel.shortfalls
                web_symbols = ssel.web_symbols
                stock_warnings = list(ssel.warnings)
                stock_sync_as_of = ssel.sync_as_of
                research_log.extend(ssel.research_log)
                research_log.append({
                    "bucket": "equity", "output_field": "equity_stocks_web",
                    "web_symbols": ssel.web_symbols, "screened_symbols": ssel.screened_symbols,
                    "trend_view_as_of": view.as_of.isoformat() if view else None,
                })
                if stock_entries and isinstance(dossier.equity, NoData):
                    dossier = dossier.model_copy(update={"equity": stock_entries})
            elif include_stocks:
                data_mode = (
                    "synthetic"
                    if any(e.instrument.id.startswith("DEMO") for e in entries)
                    else s.data_mode
                )
                stock_agent = StockResearcherAgent(
                    conn, data_mode=data_mode, stale_after_days=s.stock_stale_after_days
                )
                stock_res = stock_agent.run(
                    goal=goal,
                    risk_category=profile.category,
                    as_of=as_of,
                    rule_set=eff_stock_rule_set,
                    min_market_cap_cr=min_market_cap_cr,
                    min_traded_value_cr=min_traded_value_cr,
                    trend_based=trend_based_stocks,
                    s=s,
                )
                stock_entries = stock_res.entries
                stock_verdicts = stock_res.verdicts
                stock_filters_applied = stock_res.filters_applied
                stock_sync_as_of = stock_res.sync_as_of
                stock_warnings = list(stock_res.warnings)
                trend_regime = stock_res.trend_regime
                if stock_res.research_log_entry:
                    research_log.append(stock_res.research_log_entry)
                if stock_entries and isinstance(dossier.equity, NoData):
                    dossier = dossier.model_copy(update={"equity": stock_entries})

            audit.append(rid, "mf_researcher", "research", {
                "dossier": dossier.model_dump(mode="json"), "runs": research_log,
            }, model=budgeted_llm.model, prompt_version=RESEARCH_PV)

            # M7: analyse (skip LLM prose for candidates failing hard suitability; run suitable ones in parallel)
            analyst = AnalystAgent(budgeted_llm)
            from .orchestration.supervisor import dossier_timeout_sections

            n_timeouts = len(dossier_timeout_sections(dossier))

            def _analyse_entry(e: DossierEntry) -> AnalystVerdict:
                fm = {f.name: f.value for f in e.facts}
                eligible = not check_fund(e.instrument.id, e.instrument.category, fm, goal, s)
                v = analyst.analyse(e, goal, profile.category, as_of, use_llm=eligible, s=s)
                if n_timeouts > 0:
                    v = v.model_copy(
                        update={"confidence": round(max(0.0, v.confidence - 0.15 * n_timeouts), 4)}
                    )
                return v

            workers = max(1, min(s.llm_max_concurrency, len(entries) or 1))
            if workers > 1 and len(entries) > 1:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = [pool.submit(copy_context().run, _analyse_entry, e) for e in entries]
                    verdicts = [f.result() for f in futs]
            else:
                verdicts = [_analyse_entry(e) for e in entries]

            veto_on = s.stock360_veto_enabled if stock360_veto is None else stock360_veto
            if stock_entries and veto_on:
                from .orchestration.stock360_veto import apply_veto

                min_sig = stock360_min_sell_signals or s.stock360_veto_min_sell_signals
                before = stock_entries
                stock_entries, stock_verdicts, veto_warns, veto_rows = apply_veto(
                    conn, stock_entries, stock_verdicts, data_mode=data_mode, s=s, min_signals=min_sig)
                if dossier.equity is before:
                    dossier = dossier.model_copy(update={"equity": stock_entries or NoData(reason="no_candidates")})
                stock_warnings.extend(veto_warns)
                research_log.append({"bucket": "equity", "output_field": "stock360_veto",
                                     "min_sell_signals": min_sig, "results": veto_rows})
                audit.append(rid, "stock_360", "veto", {"min_sell_signals": min_sig, "results": veto_rows})
            if stock_entries:
                entries.extend(stock_entries)
                verdicts.extend(stock_verdicts)
            flush_langfuse()

        audit.append(rid, "analyst", "analysis", {
            "weights": s.analyst_weights.model_dump(),
            "verdicts": [v.model_dump(mode="json") for v in verdicts],
            "deterministic_fallbacks": analyst.fallbacks,
            "llm_calls_used": budgeted_llm.calls,
            "llm_calls_budget": budgeted_llm.max_calls,
        }, model=budgeted_llm.model, prompt_version=ANALYST_PV)

        # M8: decide + compliance
        decision = decide(
            run_id=rid, profile=profile, goal=goal, target=target, entries=entries,
            verdicts={v.instrument_id: v for v in verdicts}, model=budgeted_llm.model,
            dossier=dossier, include_stocks=include_stocks,
            trend_based=trend_based_stocks and include_stocks,
            sleeve_shift_pct=float(trend_regime.get("sleeve_shift_pct", 0.0)),
            spec=spec, s=s,
        )

        spec_report: dict = {}
        if spec is not None:
            spec_report = build_spec_report(
                spec, decision, entries, goal, s, default_s=s_default,
                profile_category=profile.category.value,
                selector_shortfalls=[*fund_shortfalls, *stock_shortfalls],
                fund_delivered=fund_delivered,
                web_symbols=web_symbols, warnings=stock_warnings,
            )

        decision_payload = {
            "fund_rule_set": active_set,
            "include_stocks": include_stocks,
            "stock_rule_set": eff_stock_rule_set,
            "portfolio_spec": spec.model_dump(mode="json") if spec else None,
            "spec_report": spec_report,
            "settings": {
                "max_weight_per_instrument": s.max_weight_per_instrument,
                "max_instruments_per_bucket": s.max_instruments_per_bucket,
                "min_ticket": s.min_ticket,
                "max_weight_per_stock": s.max_weight_per_stock,
                "max_sector_pct_of_stock_sleeve": s.max_sector_pct_of_stock_sleeve,
                "max_stocks": s.max_stocks,
            },
            "total_amount": goal.amount, "sizing": decision.sizing,
            "stock_sizing": decision.stock_sizing,
            "record": decision.record.model_dump(mode="json"),
            "rejected": [v.model_dump() for v in decision.rejected],
            "escalations": decision.escalations, "unallocated_weight": decision.unallocated_weight,
        }
        if active_set == "combined":
            decision_payload["provenance"] = provenance_data
        audit.append(rid, "decision_maker", "decision", decision_payload, prompt_version=DECISION_PV)
        try:
            report = review(decision, entries, goal, s=s)
        except SuitabilityError as exc:
            audit.append(rid, "compliance", "compliance",
                         {"blocked": True, "violations": [v.model_dump() for v in exc.violations]})
            raise
        audit.append(rid, "compliance", "compliance", {
            "blocked": False, "disclosures": report.disclosures,
        })
        result = AdviceResult(
            run_id=rid, profile=profile, goal=goal, target=target, dossier=dossier,
            verdicts=verdicts, decision=decision, compliance=report, assumptions=assumptions,
            rule_set=active_set, provenance=provenance_data, news_summaries=news_summaries,
            research_log=research_log,
            include_stocks=include_stocks,
            stock_rule_set=eff_stock_rule_set,
            trend_based_stocks=trend_based_stocks and include_stocks,
            trend_regime=trend_regime,
            stock_filters_applied=stock_filters_applied,
            stock_sync_as_of=stock_sync_as_of,
            stock_warnings=stock_warnings,
            portfolio_spec=spec,
            spec_report=spec_report,
        )
        from .daily import save_initial_position

        save_initial_position(conn, result, as_of=as_of)
        return result

