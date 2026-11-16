"""M10: Daily 08:00 IST recalibration cycle — HOLD / REBALANCE / ESCALATE state machine.

Flow:
  Scheduler (08:00 IST) -> refresh NAV stats -> data-hash cache check ->
  Research Supervisor / MFResearchAgent -> Analyst Agent ->
  Decision Maker (with yesterday's recommendation + current holdings, conviction delta & 5/25 drift) ->
  HOLD (silent, logged "checked, nothing changed") |
  REBALANCE (compliance gate -> notification -> user confirmation required) |
  ESCALATE (human review queue).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from .agents.analyst import PROMPT_VERSION as ANALYST_PV
from .agents.analyst import AnalystAgent
from .agents.decision import PROMPT_VERSION as DECISION_PV
from .agents.decision import DecisionResult, PlanRow, decide
from .agents.mf_researcher import PROMPT_VERSION as RESEARCH_PV
from .agents.mf_researcher import MFResearchAgent, default_config
from .agents.profiling import ProfileAssumptions
from .agents.tools import ResearchTools, facts_for_fund
from .audit.log import AuditLog
from .config import Settings, get_settings
from .data.news import enrich_entries_with_news
from .data.stats import compute_all_stats
from .data.store import MetadataStore
from .domain.categories import categories_for
from .domain.rulesets import use_rule_set
from .guardrails.compliance import ComplianceReport, review
from .guardrails.suitability import SuitabilityError, check_fund
from .llm.base import BudgetedLLM, LLMClient
from .logging_setup import new_run_id
from .models.core import (
    AnalystVerdict,
    DossierEntry,
    Fact,
    Goal,
    Instrument,
    NoData,
    Recommendation,
    ResearchDossier,
    UserFinancialProfile,
)
from .models.enums import Action, Bucket, DailyOutcome
from .notifications import (
    ConsoleNotifier,
    NotificationMessage,
    Notifier,
    format_notification_body,
)
from .pipeline import AdviceResult
from .portfolio import (
    PortfolioDriftReport,
    PortfolioHoldingInput,
    PortfolioStore,
    PortfolioValuation,
    RebalanceTradeSuggestion,
    analyze_portfolio_drift,
    build_sell_warnings,
    valuate_portfolio,
)
from .rag.index import VectorIndex

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

_DAILY_SCHEMA = """
CREATE TABLE IF NOT EXISTS active_position (
    profile_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (profile_id, goal_id)
);

CREATE TABLE IF NOT EXISTS escalation_queue (
    escalation_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING_HUMAN_REVIEW',
    created_at TEXT NOT NULL
);
"""


class ConvictionDeltaItem(BaseModel):
    instrument_id: str
    name: str
    prev_score: float
    curr_score: float
    score_delta: float
    hard_events: list[str] = Field(default_factory=list)
    breached: bool = False


class PendingRebalance(BaseModel):
    run_id: str
    as_of: date
    decision: DecisionResult
    compliance: ComplianceReport
    verdicts: dict[str, AnalystVerdict]
    entries: list[DossierEntry]
    dossier: ResearchDossier
    proposed_holdings: list[PortfolioHoldingInput]
    trades: list[RebalanceTradeSuggestion]
    reasons: list[str]
    data_hash: str


class ActivePosition(BaseModel):
    profile_id: str
    goal_id: str
    run_id: str
    established_date: date
    last_rebalance_date: date
    last_checked_date: date | None = None
    last_data_hash: str | None = None
    rule_set: str = "set1"
    profile: UserFinancialProfile
    goal: Goal
    assumptions: ProfileAssumptions
    target: dict[Bucket, float]
    recommendations: list[Recommendation]
    rows: list[PlanRow]
    verdicts: dict[str, AnalystVerdict]
    entries: list[DossierEntry]
    dossier: ResearchDossier
    holdings: list[PortfolioHoldingInput]
    pending_rebalance: PendingRebalance | None = None


class EscalationItem(BaseModel):
    escalation_id: str
    profile_id: str
    goal_id: str
    run_id: str
    as_of: date
    reasons: list[str]
    status: str = "PENDING_HUMAN_REVIEW"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DailyCycleResult(BaseModel):
    run_id: str
    profile_id: str
    goal_id: str
    as_of: date
    outcome: DailyOutcome
    message: str
    cache_hit: bool = False
    cooldown_suppressed: bool = False
    data_hash: str
    conviction_deltas: list[ConvictionDeltaItem] = Field(default_factory=list)
    hard_events: list[str] = Field(default_factory=list)
    drift_report: PortfolioDriftReport | None = None
    decision: DecisionResult | None = None
    compliance: ComplianceReport | None = None
    trades: list[RebalanceTradeSuggestion] = Field(default_factory=list)
    exit_load_warnings: list[str] = Field(default_factory=list)
    escalation_reasons: list[str] = Field(default_factory=list)
    notification: NotificationMessage | None = None
    confirmed: bool = False


class DailySimulationReport(BaseModel):
    profile_id: str
    goal_id: str
    start_date: date
    end_date: date
    total_days: int
    hold_count: int
    rebalance_count: int
    escalate_count: int
    cache_hits: int
    cooldown_suppressions: int
    days: list[DailyCycleResult]
    notifications: list[NotificationMessage] = Field(default_factory=list)


class PositionStore:
    """SQLite persistence for current active position and the human escalation queue."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        conn.executescript(_DAILY_SCHEMA)
        conn.commit()

    def save(self, pos: ActivePosition) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO active_position (profile_id, goal_id, updated_at, body) "
            "VALUES (?,?,?,?)",
            (
                pos.profile_id,
                pos.goal_id,
                datetime.now(UTC).isoformat(),
                pos.model_dump_json(),
            ),
        )
        self.conn.commit()

    def get(self, profile_id: str, goal_id: str | None = None) -> ActivePosition | None:
        if goal_id is not None:
            row = self.conn.execute(
                "SELECT body FROM active_position WHERE profile_id = ? AND goal_id = ?",
                (profile_id, goal_id),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT body FROM active_position WHERE profile_id = ? ORDER BY updated_at DESC LIMIT 1",
                (profile_id,),
            ).fetchone()
        return ActivePosition.model_validate_json(row["body"]) if row else None

    def list_all(self) -> list[ActivePosition]:
        rows = self.conn.execute(
            "SELECT body FROM active_position ORDER BY updated_at DESC"
        ).fetchall()
        return [ActivePosition.model_validate_json(r["body"]) for r in rows]

    def enqueue_escalation(self, item: EscalationItem) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO escalation_queue "
            "(escalation_id, profile_id, goal_id, run_id, as_of, reasons_json, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                item.escalation_id,
                item.profile_id,
                item.goal_id,
                item.run_id,
                item.as_of.isoformat(),
                json.dumps(item.reasons),
                item.status,
                item.created_at.isoformat(),
            ),
        )
        self.conn.commit()

    def list_escalations(self, profile_id: str | None = None) -> list[EscalationItem]:
        if profile_id:
            rows = self.conn.execute(
                "SELECT * FROM escalation_queue WHERE profile_id = ? ORDER BY created_at DESC",
                (profile_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM escalation_queue ORDER BY created_at DESC"
            ).fetchall()
        return [
            EscalationItem(
                escalation_id=r["escalation_id"],
                profile_id=r["profile_id"],
                goal_id=r["goal_id"],
                run_id=r["run_id"],
                as_of=date.fromisoformat(r["as_of"]),
                reasons=json.loads(r["reasons_json"]),
                status=r["status"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]


def _holdings_from_rows(
    rows: Sequence[PlanRow],
    store: MetadataStore,
    as_of: date,
) -> list[PortfolioHoldingInput]:
    """Convert recommended PlanRows into synthetic unit holdings at `as_of` NAVs."""
    holdings: list[PortfolioHoldingInput] = []
    for idx, r in enumerate(rows, start=1):
        if r.amount <= 0:
            continue
        fund = store.find_fund(r.instrument_id)
        nav_pair = store.latest_nav_as_of(r.instrument_id, as_of) or store.latest_nav_as_of(
            r.instrument_id, None
        )
        nav = nav_pair[1] if nav_pair else 10.0
        units = round(r.amount / nav, 6)
        if units <= 0:
            continue
        holdings.append(
            PortfolioHoldingInput(
                row=idx,
                scheme_key=r.instrument_id,
                code=r.instrument_id,
                isin=fund.isin if fund else "",
                name=r.name,
                bucket=r.bucket,
                category=r.category,
                units=units,
                avg_cost=round(nav, 4),
                as_of=as_of,
            )
        )
    return holdings


def compute_data_hash(
    store: MetadataStore,
    target: dict[Bucket, float],
    held_codes: Sequence[str],
    as_of: date,
) -> str:
    """Compute a deterministic hash of scheme metadata + NAV/stats up to `as_of` for cache gating."""
    categories: list[str] = []
    for bucket, weight in target.items():
        if weight > 0:
            categories.extend(categories_for(bucket))
    funds = store.query_funds(categories=categories) if categories else store.query_funds()
    by_code = {f.code: f for f in funds}
    for code in held_codes:
        if code not in by_code:
            f = store.find_fund(code)
            if f is not None:
                by_code[code] = f

    items = []
    for code in sorted(by_code.keys()):
        f = by_code[code]
        nav_pair = store.latest_nav_as_of(code, as_of)
        stats = store.get_stats(code)
        items.append(
            {
                "code": f.code,
                "category": f.category,
                "plan": f.plan,
                "expense_ratio": f.expense_ratio,
                "aum_cr": f.aum_cr,
                "rating": f.rating,
                "manager": f.manager,
                "manager_since": f.manager_since.isoformat() if f.manager_since else None,
                "latest_nav_date": nav_pair[0].isoformat() if nav_pair else None,
                "latest_nav": round(nav_pair[1], 6) if nav_pair else None,
                "stats": stats.model_dump(mode="json") if stats else None,
            }
        )
    blob = json.dumps(items, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def save_initial_position(
    conn: sqlite3.Connection,
    advice: AdviceResult,
    *,
    as_of: date | None = None,
    holdings: Sequence[PortfolioHoldingInput] | None = None,
    goal_id: str = "default",
) -> ActivePosition:
    """Persist an AdviceResult as the active baseline position per user/goal."""
    store = MetadataStore(conn)
    pos_store = PositionStore(conn)
    port_store = PortfolioStore(conn)
    effective_as_of = as_of or store.latest_nav_date() or date.today()
    entries = [] if isinstance(advice.dossier.mf, NoData) else list(advice.dossier.mf)

    if holdings is not None:
        active_holdings = list(holdings)
    else:
        existing = port_store.get_holdings(advice.profile.profile_id)
        active_holdings = (
            existing
            if existing
            else _holdings_from_rows(advice.decision.rows, store, effective_as_of)
        )

    held_codes = [h.code for h in active_holdings]
    d_hash = compute_data_hash(store, advice.target, held_codes, effective_as_of)

    pos = ActivePosition(
        profile_id=advice.profile.profile_id,
        goal_id=goal_id,
        run_id=advice.run_id,
        established_date=effective_as_of,
        last_rebalance_date=effective_as_of,
        last_checked_date=effective_as_of,
        last_data_hash=d_hash,
        rule_set=advice.rule_set,
        profile=advice.profile,
        goal=advice.goal,
        assumptions=advice.assumptions,
        target=advice.target,
        recommendations=list(advice.decision.record.recommendations),
        rows=list(advice.decision.rows),
        verdicts={v.instrument_id: v for v in advice.verdicts},
        entries=entries,
        dossier=advice.dossier,
        holdings=active_holdings,
    )
    pos_store.save(pos)
    return pos


def _fact_dict(facts: Sequence[Fact]) -> dict[str, float | str]:
    return {f.name: f.value for f in facts}


def compute_conviction_deltas(
    *,
    pos: ActivePosition,
    curr_entries: Sequence[DossierEntry],
    curr_verdicts: dict[str, AnalystVerdict],
    held_codes: Sequence[str],
    s: Settings,
) -> tuple[list[ConvictionDeltaItem], list[str]]:
    """Compare today's Analyst scores & facts against baseline position for conviction delta & hard events."""
    prev_entries_by_id = {e.instrument.id: e for e in pos.entries}
    curr_entries_by_id = {e.instrument.id: e for e in curr_entries}

    tracked_ids = list(
        dict.fromkeys(
            list(held_codes) + [r.instrument_id for r in pos.rows]
        )
    )

    deltas: list[ConvictionDeltaItem] = []
    all_hard_events: list[str] = []

    for inst_id in tracked_ids:
        prev_v = pos.verdicts.get(inst_id)
        curr_v = curr_verdicts.get(inst_id)
        curr_e = curr_entries_by_id.get(inst_id)
        prev_e = prev_entries_by_id.get(inst_id)
        if curr_v is None or curr_e is None:
            continue

        prev_score = prev_v.score if prev_v is not None else curr_v.score
        curr_score = curr_v.score
        delta = round(curr_score - prev_score, 2)

        prev_fm = _fact_dict(prev_e.facts) if prev_e else {}
        curr_fm = _fact_dict(curr_e.facts)
        hard_events: list[str] = []

        # 1. Rating downgrade check
        prev_rating = prev_fm.get("rating")
        curr_rating = curr_fm.get("rating")
        if isinstance(prev_rating, (int, float)) and isinstance(curr_rating, (int, float)):
            if curr_rating < prev_rating:
                hard_events.append(
                    f"{inst_id} ({curr_e.instrument.name}): rating downgraded from "
                    f"{prev_rating:.1f}★ to {curr_rating:.1f}★"
                )

        # 2. Manager exit / change check
        prev_mgr = prev_fm.get("manager")
        curr_mgr = curr_fm.get("manager")
        if prev_mgr and curr_mgr and str(prev_mgr) != str(curr_mgr):
            hard_events.append(
                f"{inst_id} ({curr_e.instrument.name}): fund manager changed from "
                f"{prev_mgr!r} to {curr_mgr!r}"
            )

        # 3. Hard suitability violation check on held instrument
        violations = check_fund(
            inst_id, curr_e.instrument.category, curr_fm, pos.goal, s
        )
        for v in violations:
            msg = f"{inst_id} ({curr_e.instrument.name}): hard suitability failure ({v.rule}: {v.detail})"
            if msg not in hard_events:
                hard_events.append(msg)

        # 4. Regulatory / SEBI action or severe negative news
        news_sub = curr_v.sub_scores.get("news", 50.0)
        if news_sub < 30.0:
            hard_events.append(
                f"{inst_id} ({curr_e.instrument.name}): high-materiality negative news/regulatory signal (news_score={news_sub:.1f})"
            )
        for f in curr_e.facts:
            if isinstance(f.value, str) and any(
                kw in f.value.lower() for kw in ("sebi action", "regulatory freeze", "winding up")
            ):
                hard_events.append(
                    f"{inst_id} ({curr_e.instrument.name}): regulatory alert — {f.value}"
                )

        breached = (
            abs(delta) >= s.conviction_delta_min
            or len(hard_events) > 0
            or curr_score < s.rebalance_decrease_min_score
        )
        all_hard_events.extend(hard_events)
        deltas.append(
            ConvictionDeltaItem(
                instrument_id=inst_id,
                name=curr_e.instrument.name,
                prev_score=prev_score,
                curr_score=curr_score,
                score_delta=delta,
                hard_events=hard_events,
                breached=breached,
            )
        )

    # Also check if an unheld shortlisted candidate's own score jumped by >= conviction_delta_min into BUY territory
    held_set = set(tracked_ids)
    for e in curr_entries:
        if e.instrument.id in held_set:
            continue
        cand_v = curr_verdicts.get(e.instrument.id)
        prev_cand_v = pos.verdicts.get(e.instrument.id)
        if cand_v is None or prev_cand_v is None or cand_v.score < s.rebalance_buy_min_score:
            continue
        fm = _fact_dict(e.facts)
        if check_fund(e.instrument.id, e.instrument.category, fm, pos.goal, s):
            continue
        diff = round(cand_v.score - prev_cand_v.score, 2)
        if abs(diff) >= s.conviction_delta_min:
            deltas.append(
                ConvictionDeltaItem(
                    instrument_id=e.instrument.id,
                    name=e.instrument.name,
                    prev_score=prev_cand_v.score,
                    curr_score=cand_v.score,
                    score_delta=diff,
                    hard_events=[],
                    breached=True,
                )
            )

    return deltas, all_hard_events


def _build_rebalance_trades(
    decision: DecisionResult,
    valuation: PortfolioValuation,
    s: Settings,
) -> tuple[list[RebalanceTradeSuggestion], list[str]]:
    """Construct rupee trade suggestions and attach exit-load / tax warnings before suggesting any sell."""
    valued_by_code: dict[str, list] = {}
    for h in valuation.holdings:
        valued_by_code.setdefault(h.code, []).append(h)

    trades: list[RebalanceTradeSuggestion] = []
    warnings: list[str] = []

    target_by_code = {r.instrument_id: r for r in decision.rows}
    all_codes = list(
        dict.fromkeys(
            [h.code for h in valuation.holdings] + [r.instrument_id for r in decision.rows]
        )
    )

    for code in all_codes:
        h_list = valued_by_code.get(code, [])
        cur_amt = round(sum(h.current_value for h in h_list), 2)
        holding_days = min((h.holding_days for h in h_list), default=None)
        unrealized_gain = round(sum(h.unrealized_gain for h in h_list), 2) if h_list else None
        row = target_by_code.get(code)

        if row is not None:
            tgt_amt = round(row.amount, 2)
            delta = round(tgt_amt - cur_amt, 2)
            name = row.name
            bucket = row.bucket
            category = row.category
            action = row.action
        else:
            tgt_amt = 0.0
            delta = round(-cur_amt, 2)
            first_h = h_list[0]
            name = first_h.name
            bucket = first_h.bucket
            category = first_h.category
            action = Action.EXIT

        if abs(delta) < s.min_ticket and action not in (Action.EXIT, Action.BUY):
            continue

        if delta < 0 or action in (Action.DECREASE, Action.EXIT):
            if action == Action.HOLD:
                action = Action.EXIT if tgt_amt == 0 else Action.DECREASE
            exit_w, tax_w, warns = build_sell_warnings(
                name=name,
                category=category,
                bucket=bucket,
                sell_amount=abs(delta),
                holding_days=holding_days,
                unrealized_gain=unrealized_gain,
                current_value=cur_amt,
                s=s,
            )
            warnings.extend(warns)
            if row is not None and warns:
                row.warning = "; ".join(warns)
        else:
            if action == Action.HOLD and delta >= s.min_ticket:
                action = Action.INCREASE if cur_amt > 0 else Action.BUY
            exit_w, tax_w, warns = None, None, []

        trades.append(
            RebalanceTradeSuggestion(
                instrument_id=code,
                name=name,
                bucket=bucket,
                category=category,
                action=action,
                current_amount=cur_amt,
                target_amount=tgt_amt,
                amount_delta=delta,
                holding_days=holding_days,
                unrealized_gain=unrealized_gain,
                exit_load_warning=exit_w,
                tax_warning=tax_w,
                warnings=warns,
            )
        )

    return trades, warnings


def run_daily_cycle(
    conn: sqlite3.Connection,
    llm: LLMClient,
    *,
    profile_id: str,
    goal_id: str | None = None,
    as_of: date | None = None,
    notifier: Notifier | None = None,
    refresh_nav_stats: bool = False,
    force_refresh: bool = False,
    auto_confirm: bool = False,
    run_id: str | None = None,
) -> DailyCycleResult:
    """Run one 08:00 IST daily recalibration cycle for an active position."""
    pos_store = PositionStore(conn)
    pos = pos_store.get(profile_id, goal_id)
    if pos is None:
        raise ValueError(f"No active position found for profile_id={profile_id!r}, goal_id={goal_id!r}")

    with use_rule_set(pos.rule_set):
        s = get_settings()
        budgeted_llm = (
            llm if isinstance(llm, BudgetedLLM) else BudgetedLLM(llm, s.max_llm_calls_per_run)
        )
        store, index = MetadataStore(conn), VectorIndex(conn)
        audit = AuditLog(conn)
        port_store = PortfolioStore(conn)
        effective_as_of = as_of or store.latest_nav_date() or date.today()
        rid = run_id or new_run_id()
        active_notifier = notifier or ConsoleNotifier(quiet=True)

        if refresh_nav_stats:
            compute_all_stats(store, as_of=effective_as_of)

        # Load latest holdings (from uploaded CSV if available, else position holdings)
        uploaded_holdings = port_store.get_holdings(pos.profile_id)
        active_holdings = uploaded_holdings if uploaded_holdings is not None else pos.holdings
        held_codes = list(dict.fromkeys(h.code for h in active_holdings))

        # Valuate current portfolio and check 5/25 drift
        valuation = valuate_portfolio(active_holdings, store, as_of=effective_as_of)
        row_buckets = {r.bucket for r in pos.rows}
        held_buckets = {b for b, w in valuation.bucket_weights.items() if w > 0}
        total_row_amt = sum(r.amount for r in pos.rows)
        if row_buckets and total_row_amt > 0 and held_buckets <= row_buckets:
            drift_target = {
                b: (sum(r.amount for r in pos.rows if r.bucket == b) / total_row_amt) * 100.0
                for b in row_buckets
            }
        else:
            allocated_buckets = row_buckets | held_buckets
            raw_drift_target = {
                b: w for b, w in pos.target.items() if b in allocated_buckets
            } or dict(pos.target)
            drift_target_sum = sum(raw_drift_target.values())
            drift_target = (
                {b: (w / drift_target_sum) * 100.0 for b, w in raw_drift_target.items()}
                if drift_target_sum > 0
                else dict(pos.target)
            )
        drift_report = analyze_portfolio_drift(valuation, drift_target, s=s)
        held_amounts: dict[str, float] = {}
        for vh in valuation.holdings:
            held_amounts[vh.code] = round(held_amounts.get(vh.code, 0.0) + vh.current_value, 2)

        # Data hash check: skip unchanged dossier/analyst via cache keyed by data hash
        data_hash = compute_data_hash(store, pos.target, held_codes, effective_as_of)
        cache_hit = bool(
            not force_refresh
            and pos.last_data_hash == data_hash
            and pos.entries
            and pos.verdicts
        )

        no_data_buckets: list[str] = []
        if cache_hit:
            entries = list(pos.entries)
            verdicts = dict(pos.verdicts)
            dossier = pos.dossier
        else:
            entries = []
            research_log: list[dict] = []
            for bucket, weight in pos.target.items():
                if weight <= 0:
                    continue
                cats = categories_for(bucket)
                if not cats:
                    continue
                tools = ResearchTools(store, index, as_of=effective_as_of)
                agent = MFResearchAgent(
                    budgeted_llm, tools, default_config(bucket, cats)
                )
                res = agent.run(pos.goal, effective_as_of)
                if isinstance(res.section, NoData):
                    no_data_buckets.append(f"{bucket.value}: {res.section.reason}")
                    research_log.append({"bucket": bucket.value, "no_data": res.section.reason})
                else:
                    entries.extend(res.section)
                research_log.append(
                    {
                        "bucket": bucket.value,
                        "notes": res.notes,
                        "dropped": res.dropped,
                        "tool_calls": [c.model_dump() for c in res.tool_calls],
                    }
                )

            # Ensure all currently held instruments are present in `entries` with fresh facts
            existing_ids = {e.instrument.id for e in entries}
            for code in held_codes:
                if code in existing_ids:
                    continue
                rec = store.find_fund(code)
                if rec is None:
                    continue
                stats = store.get_stats(code)
                docs = index.search(
                    query=f"{rec.name} exit load risks",
                    isin=rec.isin,
                    k=1,
                    as_of=effective_as_of,
                )
                fs = facts_for_fund(rec, stats, effective_as_of, docs)
                entries.append(
                    DossierEntry(
                        instrument=Instrument(
                            id=rec.code,
                            name=rec.name,
                            asset_class=rec.bucket,
                            category=rec.category,
                            plan_type=rec.plan,
                        ),
                        facts=fs,
                    )
                )
                existing_ids.add(code)

            news_sec, sent_sec, _ = enrich_entries_with_news(
                entries, as_of=effective_as_of, fetch_live=False
            )
            dossier = ResearchDossier(
                mf=entries if entries else NoData(reason="no_candidates"),
                news_context=news_sec if news_sec else NoData(reason="no_candidates"),
                sentiment=sent_sec if sent_sec else NoData(reason="no_candidates"),
            )
            audit.append(
                rid,
                "mf_researcher",
                "daily_research",
                {"as_of": effective_as_of.isoformat(), "cache_hit": False, "data_hash": data_hash},
                model=budgeted_llm.model,
                prompt_version=RESEARCH_PV,
            )

            analyst = AnalystAgent(budgeted_llm)
            verdicts = {}
            for e in entries:
                fm = _fact_dict(e.facts)
                eligible = not check_fund(
                    e.instrument.id, e.instrument.category, fm, pos.goal, s
                )
                v = analyst.analyse(
                    e, pos.goal, pos.profile.category, effective_as_of, use_llm=eligible, s=s
                )
                verdicts[v.instrument_id] = v

            audit.append(
                rid,
                "analyst",
                "daily_analysis",
                {
                    "as_of": effective_as_of.isoformat(),
                    "verdicts": [v.model_dump(mode="json") for v in verdicts.values()],
                },
                model=budgeted_llm.model,
                prompt_version=ANALYST_PV,
            )

        # Compute conviction deltas & hard events
        conviction_deltas, hard_events = compute_conviction_deltas(
            pos=pos,
            curr_entries=entries,
            curr_verdicts=verdicts,
            held_codes=held_codes,
            s=s,
        )

        # 1. Check ESCALATE conditions (NoData in required bucket or low confidence on eligible held instrument)
        escalation_reasons: list[str] = []
        if no_data_buckets or isinstance(dossier.mf, NoData):
            escalation_reasons.extend(
                no_data_buckets or ["dossier.mf returned NoData (no_candidates)"]
            )
        entry_by_id = {e.instrument.id: e for e in entries}
        for code in held_codes:
            v = verdicts.get(code)
            e_item = entry_by_id.get(code)
            eligible_held = True
            if e_item is not None:
                eligible_held = not check_fund(
                    code, e_item.instrument.category, _fact_dict(e_item.facts), pos.goal, s
                )
            if eligible_held and v is not None and v.confidence < s.min_confidence:
                escalation_reasons.append(
                    f"{code}: analyst confidence {v.confidence:.2f} below minimum {s.min_confidence:.2f}"
                )

        if escalation_reasons:
            esc_item = EscalationItem(
                escalation_id=f"esc-{rid}",
                profile_id=pos.profile_id,
                goal_id=pos.goal_id,
                run_id=rid,
                as_of=effective_as_of,
                reasons=escalation_reasons,
            )
            pos_store.enqueue_escalation(esc_item)
            pos.last_checked_date = effective_as_of
            pos_store.save(pos)
            msg = f"Escalated to human review: {'; '.join(escalation_reasons)}"
            audit.append(
                rid,
                "daily_loop",
                "daily_cycle",
                {
                    "as_of": effective_as_of.isoformat(),
                    "outcome": DailyOutcome.ESCALATE.value,
                    "message": msg,
                    "reasons": escalation_reasons,
                    "cache_hit": cache_hit,
                    "data_hash": data_hash,
                },
                prompt_version=DECISION_PV,
            )
            return DailyCycleResult(
                run_id=rid,
                profile_id=pos.profile_id,
                goal_id=pos.goal_id,
                as_of=effective_as_of,
                outcome=DailyOutcome.ESCALATE,
                message=msg,
                cache_hit=cache_hit,
                data_hash=data_hash,
                conviction_deltas=conviction_deltas,
                hard_events=hard_events,
                drift_report=drift_report,
                escalation_reasons=escalation_reasons,
            )

        # 2. Check material triggers: conviction delta >= threshold, hard event, or 5/25 drift breach
        conviction_breached = any(d.breached for d in conviction_deltas)
        has_hard_event = bool(hard_events) or any(
            d.curr_score < s.rebalance_decrease_min_score for d in conviction_deltas
        )
        drift_breached = drift_report.breached

        if not conviction_breached and not drift_breached and not has_hard_event:
            pos.last_checked_date = effective_as_of
            pos.last_data_hash = data_hash
            pos.entries = entries
            pos.dossier = dossier
            pos_store.save(pos)
            msg = "checked, nothing changed"
            audit.append(
                rid,
                "daily_loop",
                "daily_cycle",
                {
                    "as_of": effective_as_of.isoformat(),
                    "outcome": DailyOutcome.HOLD.value,
                    "message": msg,
                    "cache_hit": cache_hit,
                    "cooldown_suppressed": False,
                    "data_hash": data_hash,
                    "conviction_deltas": [d.model_dump() for d in conviction_deltas],
                    "drift_breached": drift_breached,
                },
                prompt_version=DECISION_PV,
            )
            return DailyCycleResult(
                run_id=rid,
                profile_id=pos.profile_id,
                goal_id=pos.goal_id,
                as_of=effective_as_of,
                outcome=DailyOutcome.HOLD,
                message=msg,
                cache_hit=cache_hit,
                cooldown_suppressed=False,
                data_hash=data_hash,
                conviction_deltas=conviction_deltas,
                hard_events=hard_events,
                drift_report=drift_report,
            )

        # 3. Anti-overtrading cool-down check (bypassed only on hard events)
        days_since_rebalance = (effective_as_of - pos.last_rebalance_date).days
        if not has_hard_event and 0 <= days_since_rebalance < s.rebalance_cooldown_days:
            pos.last_checked_date = effective_as_of
            pos.last_data_hash = data_hash
            pos_store.save(pos)
            msg = "checked, nothing changed"
            audit.append(
                rid,
                "daily_loop",
                "daily_cycle",
                {
                    "as_of": effective_as_of.isoformat(),
                    "outcome": DailyOutcome.HOLD.value,
                    "message": msg,
                    "detail": (
                        f"rebalance suppressed by {s.rebalance_cooldown_days}-day cool-down "
                        f"({days_since_rebalance}d since {pos.last_rebalance_date.isoformat()})"
                    ),
                    "cache_hit": cache_hit,
                    "cooldown_suppressed": True,
                    "data_hash": data_hash,
                },
                prompt_version=DECISION_PV,
            )
            return DailyCycleResult(
                run_id=rid,
                profile_id=pos.profile_id,
                goal_id=pos.goal_id,
                as_of=effective_as_of,
                outcome=DailyOutcome.HOLD,
                message=msg,
                cache_hit=cache_hit,
                cooldown_suppressed=True,
                data_hash=data_hash,
                conviction_deltas=conviction_deltas,
                hard_events=hard_events,
                drift_report=drift_report,
            )

        # 4. Run Decision Maker with current holdings & drift context
        effective_amount = (
            valuation.total_value if valuation.total_value > 0 else pos.goal.amount
        )
        sizing_goal = pos.goal.model_copy(update={"amount": effective_amount})
        breached_buckets = {d.bucket for d in drift_report.drift_items if d.breached}
        decision = decide(
            run_id=rid,
            profile=pos.profile,
            goal=sizing_goal,
            target=pos.target,
            entries=entries,
            verdicts=verdicts,
            model=budgeted_llm.model,
            s=s,
            current_holdings=held_amounts,
            drift_breached_buckets=breached_buckets,
            spec=pos.goal.spec,
        )

        # If Decision Maker could not fill a previously allocated bucket or raised confidence escalations -> ESCALATE
        prev_buckets = {r.bucket for r in pos.rows}
        new_buckets = {r.bucket for r in decision.rows}
        dropped_buckets = prev_buckets - new_buckets
        fatal_escalations = [
            e
            for e in decision.escalations
            if "confidence" in e
            or any(e.startswith(f"{b.value}:") for b in dropped_buckets)
        ]
        if fatal_escalations or not decision.rows or dropped_buckets:
            esc_reasons = (
                fatal_escalations
                or list(decision.escalations)
                or ["Decision Maker produced an empty plan"]
            )
            esc_item = EscalationItem(
                escalation_id=f"esc-{rid}",
                profile_id=pos.profile_id,
                goal_id=pos.goal_id,
                run_id=rid,
                as_of=effective_as_of,
                reasons=esc_reasons,
            )
            pos_store.enqueue_escalation(esc_item)
            pos.last_checked_date = effective_as_of
            pos_store.save(pos)
            msg = f"Escalated to human review: {'; '.join(esc_reasons)}"
            audit.append(
                rid,
                "daily_loop",
                "daily_cycle",
                {
                    "as_of": effective_as_of.isoformat(),
                    "outcome": DailyOutcome.ESCALATE.value,
                    "message": msg,
                    "reasons": esc_reasons,
                },
                prompt_version=DECISION_PV,
            )
            return DailyCycleResult(
                run_id=rid,
                profile_id=pos.profile_id,
                goal_id=pos.goal_id,
                as_of=effective_as_of,
                outcome=DailyOutcome.ESCALATE,
                message=msg,
                cache_hit=cache_hit,
                data_hash=data_hash,
                conviction_deltas=conviction_deltas,
                hard_events=hard_events,
                drift_report=drift_report,
                decision=decision,
                escalation_reasons=esc_reasons,
            )

        # 5. Compliance check on the new decision
        try:
            compliance = review(decision, entries, sizing_goal)
        except SuitabilityError as exc:
            esc_reasons = [f"Compliance gate violation ({v.instrument_id} {v.rule}): {v.detail}" for v in exc.violations]
            esc_item = EscalationItem(
                escalation_id=f"esc-{rid}",
                profile_id=pos.profile_id,
                goal_id=pos.goal_id,
                run_id=rid,
                as_of=effective_as_of,
                reasons=esc_reasons,
            )
            pos_store.enqueue_escalation(esc_item)
            pos.last_checked_date = effective_as_of
            pos_store.save(pos)
            msg = f"Escalated to human review: {'; '.join(esc_reasons)}"
            audit.append(
                rid,
                "daily_loop",
                "daily_cycle",
                {
                    "as_of": effective_as_of.isoformat(),
                    "outcome": DailyOutcome.ESCALATE.value,
                    "message": msg,
                    "reasons": esc_reasons,
                },
                prompt_version=DECISION_PV,
            )
            return DailyCycleResult(
                run_id=rid,
                profile_id=pos.profile_id,
                goal_id=pos.goal_id,
                as_of=effective_as_of,
                outcome=DailyOutcome.ESCALATE,
                message=msg,
                cache_hit=cache_hit,
                data_hash=data_hash,
                conviction_deltas=conviction_deltas,
                hard_events=hard_events,
                drift_report=drift_report,
                decision=decision,
                escalation_reasons=esc_reasons,
            )

        # Build rupee trade deltas & exit-load / tax warnings
        trades, exit_load_warnings = _build_rebalance_trades(decision, valuation, s)
        reasons: list[str] = list(hard_events)
        for d in conviction_deltas:
            if abs(d.score_delta) >= s.conviction_delta_min:
                sign = "+" if d.score_delta >= 0 else ""
                reasons.append(
                    f"Conviction delta on {d.name} ({d.instrument_id}): score {d.prev_score:.1f} -> "
                    f"{d.curr_score:.1f} ({sign}{d.score_delta:.1f} pts)"
                )
        for di in drift_report.drift_items:
            if di.breached:
                reasons.append(
                    f"5/25 Drift breach on {di.bucket.value} bucket: current {di.current:.1f}% vs "
                    f"target {di.target:.1f}% (abs {di.abs_drift:+.1f}pp, rel {di.rel_drift:.1f}%)"
                )

        proposed_holdings = _holdings_from_rows(decision.rows, store, effective_as_of)
        pending = PendingRebalance(
            run_id=rid,
            as_of=effective_as_of,
            decision=decision,
            compliance=compliance,
            verdicts=verdicts,
            entries=entries,
            dossier=dossier,
            proposed_holdings=proposed_holdings,
            trades=trades,
            reasons=reasons,
            data_hash=data_hash,
        )
        pos.pending_rebalance = pending
        pos.last_checked_date = effective_as_of
        pos.last_rebalance_date = effective_as_of
        pos.last_data_hash = data_hash
        pos_store.save(pos)

        notif_body = format_notification_body(
            profile_id=pos.profile_id,
            goal_id=pos.goal_id,
            as_of=effective_as_of,
            outcome=DailyOutcome.REBALANCE,
            reasons=reasons,
            trades=trades,
            exit_load_warnings=exit_load_warnings,
            disclosures=compliance.disclosures,
        )
        notification = NotificationMessage(
            notification_id=f"notif-{rid}",
            profile_id=pos.profile_id,
            goal_id=pos.goal_id,
            run_id=rid,
            as_of=effective_as_of,
            outcome=DailyOutcome.REBALANCE,
            subject=f"[Wealth Advisor] Rebalance Recommended ({effective_as_of.isoformat()}) — Confirmation Required",
            body=notif_body,
            reasons=reasons,
            trades=trades,
            exit_load_warnings=exit_load_warnings,
            disclosures=compliance.disclosures,
            requires_confirmation=True,
        )
        active_notifier.send(notification)

        confirmed = False
        if auto_confirm:
            confirm_rebalance(conn, pos.profile_id, pos.goal_id)
            confirmed = True

        msg = f"Rebalance recommended ({len(trades)} trade(s)): {'; '.join(reasons[:3])}"
        audit.append(
            rid,
            "daily_loop",
            "daily_cycle",
            {
                "as_of": effective_as_of.isoformat(),
                "outcome": DailyOutcome.REBALANCE.value,
                "message": msg,
                "reasons": reasons,
                "trades": [t.model_dump(mode="json") for t in trades],
                "exit_load_warnings": exit_load_warnings,
                "confirmed": confirmed,
                "cache_hit": cache_hit,
                "data_hash": data_hash,
            },
            prompt_version=DECISION_PV,
        )
        return DailyCycleResult(
            run_id=rid,
            profile_id=pos.profile_id,
            goal_id=pos.goal_id,
            as_of=effective_as_of,
            outcome=DailyOutcome.REBALANCE,
            message=msg,
            cache_hit=cache_hit,
            cooldown_suppressed=False,
            data_hash=data_hash,
            conviction_deltas=conviction_deltas,
            hard_events=hard_events,
            drift_report=drift_report,
            decision=decision,
            compliance=compliance,
            trades=trades,
            exit_load_warnings=exit_load_warnings,
            notification=notification,
            confirmed=confirmed,
        )


def confirm_rebalance(
    conn: sqlite3.Connection,
    profile_id: str,
    goal_id: str | None = None,
) -> ActivePosition:
    """Confirm a pending recommend-only rebalance proposal and promote it to the active position."""
    pos_store = PositionStore(conn)
    port_store = PortfolioStore(conn)
    audit = AuditLog(conn)
    pos = pos_store.get(profile_id, goal_id)
    if pos is None:
        raise ValueError(f"No active position found for profile_id={profile_id!r}, goal_id={goal_id!r}")
    if pos.pending_rebalance is None:
        raise ValueError(f"No pending rebalance to confirm for profile_id={profile_id!r}")

    pending = pos.pending_rebalance
    pos.run_id = pending.run_id
    pos.last_rebalance_date = pending.as_of
    pos.last_data_hash = pending.data_hash
    pos.recommendations = list(pending.decision.record.recommendations)
    pos.rows = list(pending.decision.rows)
    pos.verdicts = dict(pending.verdicts)
    pos.entries = list(pending.entries)
    pos.dossier = pending.dossier
    pos.holdings = list(pending.proposed_holdings)
    pos.pending_rebalance = None
    pos_store.save(pos)

    # If the user had a portfolio snapshot in PortfolioStore, update it as well
    if port_store.get_holdings(profile_id) is not None:
        store = MetadataStore(conn)
        val = valuate_portfolio(pos.holdings, store, as_of=pending.as_of)
        port_store.save(profile_id, pos.holdings, val)

    audit.append(
        pending.run_id,
        "daily_loop",
        "rebalance_confirmed",
        {
            "profile_id": pos.profile_id,
            "goal_id": pos.goal_id,
            "as_of": pending.as_of.isoformat(),
            "recommendations": [r.model_dump(mode="json") for r in pos.recommendations],
        },
        prompt_version=DECISION_PV,
    )
    return pos


class DailyScheduler:
    """08:00 IST daily recalibration scheduler (cron calculator + runner)."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        llm: LLMClient,
        *,
        notifier: Notifier | None = None,
        hour_ist: int | None = None,
        minute_ist: int | None = None,
    ) -> None:
        s = get_settings()
        self.conn = conn
        self.llm = llm
        self.notifier = notifier or ConsoleNotifier(quiet=True)
        self.hour_ist = hour_ist if hour_ist is not None else s.daily_schedule_hour_ist
        self.minute_ist = minute_ist if minute_ist is not None else s.daily_schedule_minute_ist

    @property
    def cron_expression(self) -> str:
        return f"{self.minute_ist} {self.hour_ist} * * *"

    def next_run_time(self, now: datetime | None = None) -> datetime:
        """Compute the next 08:00 Asia/Kolkata fire timestamp."""
        ref = (now or datetime.now(IST)).astimezone(IST)
        candidate = datetime.combine(
            ref.date(), time(self.hour_ist, self.minute_ist), tzinfo=IST
        )
        if candidate <= ref:
            candidate += timedelta(days=1)
        return candidate

    def run_all(
        self,
        *,
        as_of: date | None = None,
        refresh_nav_stats: bool = True,
        auto_confirm: bool = False,
    ) -> list[DailyCycleResult]:
        """Run the daily recalibration cycle for every active position in the store."""
        pos_store = PositionStore(self.conn)
        results: list[DailyCycleResult] = []
        for pos in pos_store.list_all():
            res = run_daily_cycle(
                self.conn,
                self.llm,
                profile_id=pos.profile_id,
                goal_id=pos.goal_id,
                as_of=as_of,
                notifier=self.notifier,
                refresh_nav_stats=refresh_nav_stats,
                auto_confirm=auto_confirm,
            )
            results.append(res)
        return results


def simulate_daily_loop(
    conn: sqlite3.Connection,
    llm: LLMClient,
    *,
    profile_id: str,
    goal_id: str | None = None,
    days: int = 30,
    start_date: date | None = None,
    day_hooks: dict[int, object] | None = None,
    notifier: Notifier | None = None,
    refresh_nav_stats: bool = False,
    auto_confirm: bool = True,
) -> DailySimulationReport:
    """Replay `days` daily recalibration cycles sequentially and collect outcomes.

    `day_hooks`: optional mapping `{day_index (1..days): callable(conn, day_date)}` allowing
    tests or simulations to inject market shocks, rating downgrades, or NAV updates on specific days.
    """
    pos_store = PositionStore(conn)
    pos = pos_store.get(profile_id, goal_id)
    if pos is None:
        raise ValueError(f"No active position found for profile_id={profile_id!r}")

    sim_notifier = notifier or ConsoleNotifier(quiet=True)
    base_date = start_date or (pos.established_date + timedelta(days=1))
    day_results: list[DailyCycleResult] = []

    for day_idx in range(1, days + 1):
        curr_date = base_date + timedelta(days=day_idx - 1)
        if day_hooks and day_idx in day_hooks:
            hook = day_hooks[day_idx]
            if callable(hook):
                hook(conn, curr_date)

        res = run_daily_cycle(
            conn,
            llm,
            profile_id=pos.profile_id,
            goal_id=pos.goal_id,
            as_of=curr_date,
            notifier=sim_notifier,
            refresh_nav_stats=refresh_nav_stats,
            auto_confirm=auto_confirm,
        )
        day_results.append(res)

    hold_count = sum(1 for r in day_results if r.outcome == DailyOutcome.HOLD)
    rebalance_count = sum(1 for r in day_results if r.outcome == DailyOutcome.REBALANCE)
    escalate_count = sum(1 for r in day_results if r.outcome == DailyOutcome.ESCALATE)
    cache_hits = sum(1 for r in day_results if r.cache_hit)
    cooldown_suppressions = sum(1 for r in day_results if r.cooldown_suppressed)
    notifications = [r.notification for r in day_results if r.notification is not None]

    return DailySimulationReport(
        profile_id=pos.profile_id,
        goal_id=pos.goal_id,
        start_date=base_date,
        end_date=base_date + timedelta(days=max(0, days - 1)),
        total_days=days,
        hold_count=hold_count,
        rebalance_count=rebalance_count,
        escalate_count=escalate_count,
        cache_hits=cache_hits,
        cooldown_suppressions=cooldown_suppressions,
        days=day_results,
        notifications=notifications,
    )
