"""Grounded follow-up Q&A agent and deterministic exclusion explainer."""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from ..config import get_settings
from ..data.on_demand import FundProfile, compute_fund_profile
from ..data.store import FundRecord, MetadataStore
from ..domain.categories import bucket_for, categories_for
from ..domain.portfolio_spec import active_custom, describe_spec, effective_settings
from ..domain.rulesets import use_rule_set
from ..guardrails.compliance import scrub
from ..guardrails.input import check_input
from ..guardrails.suitability import check_fund
from ..llm.base import BudgetedLLM, LLMBudgetExceeded, LLMClient
from ..llm.langchain_client import trace_context
from ..models.core import Goal
from ..models.enums import Bucket

log = logging.getLogger(__name__)

PROMPT_VERSION = "followup.v1"
MAX_FOLLOWUP_LLM_CALLS = 2
DISCLAIMER_NOTE = "This explanation is for informational purposes only and is not investment advice."


class MentionedFunds(BaseModel):
    """Structured extraction of mutual fund names or scheme codes mentioned in a question."""

    fund_queries: list[str] = Field(default_factory=list)


class FollowUpAnswerPayload(BaseModel):
    """Structured grounded answer from the follow-up agent."""

    answer: str
    fact_ids: list[str] = Field(default_factory=list)


class FollowUpResponse(BaseModel):
    """Result of answering a follow-up question."""

    answer: str
    referenced_funds: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    refused: bool = False


def _extract_fund_attrs(
    fund: Any,
    context: dict[str, Any],
    explicit_facts: dict[str, float | str] | None = None,
) -> tuple[str, str, str, dict[str, float | str]]:
    """Return (code, name, category, facts_dict) from a FundRecord, FundProfile, or dict."""
    if isinstance(fund, dict):
        code = str(fund.get("code") or fund.get("id") or fund.get("instrument_id") or "")
        name = str(fund.get("name") or code)
        category = str(fund.get("category") or "")
        raw_facts = fund.get("facts") or {}
        if isinstance(raw_facts, dict):
            facts: dict[str, float | str] = dict(raw_facts)
        elif isinstance(raw_facts, list):
            facts = {
                str(f.get("name")): f.get("value")
                for f in raw_facts
                if isinstance(f, dict) and f.get("name") is not None and f.get("value") is not None
            }
        else:
            facts = {}
        scalar_keys = (
            "expense_ratio",
            "aum_cr",
            "rating",
            "plan",
            "plan_type",
            "manager_tenure_years",
            "ytm",
            "modified_duration",
            "avg_maturity_years",
            "tracking_error",
            "track_years",
            "alpha",
            "beta",
            "score",
            "category_median_ytm",
        )
        for k in scalar_keys:
            if fund.get(k) is not None and k not in facts:
                facts[k] = fund[k]
    else:
        code = str(
            getattr(fund, "code", None)
            or getattr(fund, "id", None)
            or getattr(fund, "instrument_id", None)
            or ""
        )
        name = str(getattr(fund, "name", None) or code)
        category = str(getattr(fund, "category", None) or "")
        facts = {}
        raw_facts = getattr(fund, "facts", None)
        if isinstance(raw_facts, dict):
            facts.update(raw_facts)
        elif isinstance(raw_facts, list):
            for f in raw_facts:
                fname = getattr(f, "name", None) if not isinstance(f, dict) else f.get("name")
                fval = getattr(f, "value", None) if not isinstance(f, dict) else f.get("value")
                if fname is not None and fval is not None:
                    facts[str(fname)] = fval

        for k in (
            "expense_ratio",
            "aum_cr",
            "rating",
            "plan",
            "plan_type",
            "manager_tenure_years",
            "ytm",
            "modified_duration",
            "avg_maturity_years",
            "tracking_error",
            "track_years",
            "alpha",
            "beta",
            "score",
        ):
            val = getattr(fund, k, None)
            if val is not None and k not in facts:
                facts[k] = val

        if "track_years" not in facts:
            launch_date = getattr(fund, "launch_date", None)
            as_of = getattr(fund, "as_of", None)
            if isinstance(launch_date, date) and isinstance(as_of, date):
                facts["track_years"] = round((as_of - launch_date).days / 365.25, 2)

    cand = next((c for c in (context.get("candidates") or []) if c.get("id") == code), None)
    if cand:
        if not name or name == code:
            name = str(cand.get("name") or code)
        if not category:
            category = str(cand.get("category") or "")
        for k, v in (cand.get("facts") or {}).items():
            if k not in facts and v is not None:
                facts[k] = v

    if explicit_facts:
        facts.update(explicit_facts)

    if "plan" not in facts and "plan_type" in facts:
        facts["plan"] = str(facts["plan_type"]).lower()
    facts.setdefault("plan", "direct")

    return code, name, category, facts


def explain_exclusion(
    fund: Any,
    context: dict[str, Any],
    goal: Goal,
    rule_set: str | None = None,
    *,
    facts: dict[str, float | str] | None = None,
) -> list[dict[str, Any]]:
    """Check in stage order why `fund` was not recommended in a given advice run.

    Returns a list of structured reasons:
    `[{stage, rule, message, fact_name, fact_value, threshold}]`
    """
    code, name, category, fund_facts = _extract_fund_attrs(fund, context, facts)
    effective_rule_set = rule_set or str(context.get("rule_set") or "set2")

    with use_rule_set(effective_rule_set):
        s = get_settings()
        spec = active_custom(goal.spec)
        s = effective_settings(s, spec)
        raw_bucket = getattr(fund, "bucket", None) if not isinstance(fund, dict) else fund.get("bucket")
        if isinstance(raw_bucket, str) and raw_bucket in {b.value for b in Bucket}:
            raw_bucket = Bucket(raw_bucket)
        bucket = bucket_for(category) or (raw_bucket if isinstance(raw_bucket, Bucket) else Bucket.EQUITY)
        reasons: list[dict[str, Any]] = []

        # 1. excluded_category
        excluded_lower = {c.lower() for c in goal.excluded_categories}
        if category and category.lower() in excluded_lower:
            reasons.append(
                {
                    "stage": "excluded_category",
                    "rule": "excluded_category",
                    "message": f"Category '{category}' was excluded in the goal preferences.",
                    "fact_name": "category",
                    "fact_value": category,
                    "threshold": list(goal.excluded_categories),
                }
            )

        # 2. zero_bucket_allocation
        target = context.get("target") or {}
        bucket_weight = target.get(bucket.value, target.get(bucket))
        if bucket_weight is not None and float(bucket_weight) <= 0.0:
            reasons.append(
                {
                    "stage": "zero_bucket_allocation",
                    "rule": "zero_bucket_allocation",
                    "message": f"The {bucket.value} bucket had a 0% target allocation for this profile.",
                    "fact_name": "bucket_target_pct",
                    "fact_value": float(bucket_weight),
                    "threshold": 0.0,
                }
            )

        # 3. category_not_allowed
        allowed_cats = categories_for(bucket, s)
        if spec is None and category and category not in allowed_cats:
            reasons.append(
                {
                    "stage": "category_not_allowed",
                    "rule": "category_not_allowed",
                    "message": (
                        f"Category '{category}' is not in the allowed {bucket.value} categories "
                        f"under rule set '{effective_rule_set}'."
                    ),
                    "fact_name": "category",
                    "fact_value": category,
                    "threshold": allowed_cats,
                }
            )

        if spec is not None:
            wanted = next((r for r in spec.fund_requests if r.category == category), None)
            requested = ", ".join(describe_spec(spec))
            if wanted is None:
                msg = (
                    f"Your notes asked for: {requested}. {name} ({category}) was not one of the "
                    "requested categories, so it was not considered."
                )
            else:
                msg = (
                    f"Your notes asked for {wanted.count} {category} fund(s); the best-ranked "
                    f"{wanted.count} were chosen and {name} ranked below them."
                )
            reasons.append(
                {
                    "stage": "not_requested",
                    "rule": "not_requested",
                    "message": msg,
                    "fact_name": "requested",
                    "fact_value": requested,
                    "threshold": spec.total_funds,
                }
            )

        # 4. not_shortlisted
        shortlisted_by_bucket = context.get("shortlisted_by_bucket")
        if isinstance(shortlisted_by_bucket, dict) and bucket.value in shortlisted_by_bucket:
            shortlisted_ids = list(shortlisted_by_bucket.get(bucket.value) or [])
        elif "shortlisted_ids" in context:
            shortlisted_ids = list(context.get("shortlisted_ids") or [])
        else:
            shortlisted_ids = [
                str(c.get("id")) for c in (context.get("candidates") or []) if c.get("id")
            ]

        sizing_limits = context.get("sizing_limits") or {}
        max_picks = int(sizing_limits.get("max_research_picks") or s.max_research_picks)
        if spec is None and code and code not in shortlisted_ids:
            dropped_entry = next(
                (
                    d
                    for d in (context.get("research_dropped") or [])
                    if isinstance(d, dict) and (d.get("code") == code or d.get("id") == code)
                ),
                None,
            )
            drop_suffix = (
                f" ({dropped_entry.get('reason')})"
                if dropped_entry and dropped_entry.get("reason")
                else ""
            )
            reasons.append(
                {
                    "stage": "not_shortlisted",
                    "rule": "not_shortlisted",
                    "message": (
                        f"{name} ({code}) was not in the researcher's top {max_picks} "
                        f"shortlisted picks for the {bucket.value} bucket{drop_suffix}."
                    ),
                    "fact_name": "shortlisted",
                    "fact_value": False,
                    "threshold": max_picks,
                }
            )

        # 5. suitability_violation
        violations = check_fund(code, category, fund_facts, goal, s)
        for v in violations:
            if v.rule == "excluded_category" and any(
                r["stage"] == "excluded_category" for r in reasons
            ):
                continue
            fact_name, fact_val, threshold = _violation_metadata(
                v.rule, category, bucket, fund_facts, goal, s
            )
            reasons.append(
                {
                    "stage": "suitability_violation",
                    "rule": v.rule,
                    "message": v.detail,
                    "fact_name": fact_name,
                    "fact_value": fact_val,
                    "threshold": threshold,
                }
            )

        # 6. outranked / bucket_limit
        scores = context.get("scores") or {}
        raw_score = scores.get(code, fund_facts.get("score"))
        fund_score = float(raw_score) if raw_score is not None else None
        recommended_ids = set(context.get("recommended_ids") or [])
        recommendations = context.get("recommendations") or []
        bucket_recs = [
            r
            for r in recommendations
            if str(r.get("bucket", "")).lower() == bucket.value.lower()
        ]
        max_inst = int(
            sizing_limits.get("max_instruments_per_bucket") or s.max_instruments_per_bucket
        )
        has_suitability = any(r["stage"] == "suitability_violation" for r in reasons)

        if not has_suitability and code not in recommended_ids and bucket_recs:
            min_rec = min(bucket_recs, key=lambda r: float(r.get("score", 0.0)))
            min_rec_score = float(min_rec.get("score", 0.0))
            peer_name = min_rec.get("name") or min_rec.get("instrument_id") or "recommended peer"
            if fund_score is not None and fund_score < min_rec_score:
                reasons.append(
                    {
                        "stage": "outranked",
                        "rule": "outranked",
                        "message": (
                            f"Score {fund_score:.1f} was lower than the lowest recommended "
                            f"{bucket.value} pick ({peer_name} at {min_rec_score:.1f})."
                        ),
                        "fact_name": "score",
                        "fact_value": fund_score,
                        "threshold": min_rec_score,
                    }
                )
            elif len(bucket_recs) >= max_inst:
                reasons.append(
                    {
                        "stage": "bucket_limit",
                        "rule": "max_instruments_per_bucket",
                        "message": (
                            f"The {bucket.value} bucket reached the cap of "
                            f"{max_inst} recommended funds."
                        ),
                        "fact_name": "bucket_instruments",
                        "fact_value": len(bucket_recs),
                        "threshold": max_inst,
                    }
                )

        # 7. decision_rejected
        buy_min_score = float(sizing_limits.get("buy_min_score") or s.buy_min_score)
        min_conf = float(sizing_limits.get("min_confidence") or s.min_confidence)
        suitability_rules = {
            r["rule"] for r in reasons if r["stage"] == "suitability_violation"
        }
        rejected_list = [
            r
            for r in (context.get("rejected") or [])
            if isinstance(r, dict) and r.get("instrument_id") == code
        ]
        added_decision_rej = False
        for rej in rejected_list:
            r_rule = str(rej.get("rule") or "decision_rejected")
            if r_rule in suitability_rules:
                continue
            added_decision_rej = True
            reasons.append(
                {
                    "stage": "decision_rejected",
                    "rule": r_rule,
                    "message": str(
                        rej.get("detail") or f"Rejected at decision stage ({r_rule})."
                    ),
                    "fact_name": "score" if "score" in r_rule else r_rule,
                    "fact_value": fund_score,
                    "threshold": buy_min_score if "score" in r_rule else None,
                }
            )

        if not added_decision_rej and code not in recommended_ids:
            if fund_score is not None and fund_score < buy_min_score:
                reasons.append(
                    {
                        "stage": "decision_rejected",
                        "rule": "min_score",
                        "message": (
                            f"Score {fund_score:.1f} was below the minimum buy score "
                            f"threshold of {buy_min_score:.1f}."
                        ),
                        "fact_name": "score",
                        "fact_value": fund_score,
                        "threshold": buy_min_score,
                    }
                )
            verdicts_map = context.get("verdicts") or {}
            v_dict = verdicts_map.get(code) if isinstance(verdicts_map, dict) else None
            if isinstance(v_dict, dict) and v_dict.get("confidence") is not None:
                conf = float(v_dict["confidence"])
                if conf < min_conf:
                    reasons.append(
                        {
                            "stage": "decision_rejected",
                            "rule": "low_confidence",
                            "message": (
                                f"Analyst confidence {conf:.2f} was below minimum "
                                f"threshold {min_conf:.2f}."
                            ),
                            "fact_name": "confidence",
                            "fact_value": conf,
                            "threshold": min_conf,
                        }
                    )

        return reasons


def _violation_metadata(
    rule: str,
    category: str,
    bucket: Bucket,
    facts: dict[str, float | str],
    goal: Goal,
    s: Any,
) -> tuple[str, Any, Any]:
    if rule in ("plan", "direct_plan_only"):
        return "plan", facts.get("plan"), "direct"
    if rule in ("expense_ratio", "expense_ceiling"):
        ceil = s.expense_ceilings.get(category, s.default_expense_ceiling)
        return "expense_ratio", facts.get("expense_ratio"), ceil
    if rule in ("aum", "min_aum"):
        min_aum = s.min_aum_cr_small if category in {"Liquid", "Gold ETF"} else s.min_aum_cr
        return "aum_cr", facts.get("aum_cr"), min_aum
    if rule in ("track_record", "min_track_record"):
        min_track = (
            s.min_track_years_equity if bucket == Bucket.EQUITY else s.min_track_years_debt
        )
        return "track_years", facts.get("track_years"), min_track
    if rule in ("horizon", "min_horizon"):
        return "horizon_years", goal.horizon_years, s.category_min_horizons.get(category, 0.0)
    if rule in ("rating", "min_rating"):
        return "rating", facts.get("rating"), s.min_rating
    if rule in ("alpha", "positive_alpha"):
        return "alpha", facts.get("alpha"), 0.0
    if rule == "max_beta":
        return "beta", facts.get("beta"), s.max_beta
    if rule == "min_manager_tenure":
        return (
            "manager_tenure_years",
            facts.get("manager_tenure_years"),
            s.min_manager_tenure_years,
        )
    if rule in ("maturity", "maturity_exceeds_horizon"):
        return "avg_maturity_years", facts.get("avg_maturity_years"), goal.horizon_years
    if rule == "ytm_credit_risk_outlier":
        med = float(facts.get("category_median_ytm") or 0.0)
        outlier = float(s.ytm_outlier_pp or 0.0)
        return "ytm", facts.get("ytm"), round(med + outlier, 2)
    return rule, facts.get(rule), None


_STOP_PREFIXES = re.compile(
    r"^(?:why\s+wasn'?t|why\s+was\s+not|why\s+didn'?t\s+you\s+(?:pick|recommend|select|include)"
    r"|why\s+did\s+you\s+(?:pick|recommend|select|include|skip|exclude)"
    r"|what\s+about|how\s+about|tell\s+me\s+about|explain\s+why|is|was|why)\s+",
    flags=re.IGNORECASE,
)


def _heuristic_fund_mentions(question: str, context: dict[str, Any]) -> list[str]:
    """Deterministically extract candidate fund codes or names from a question."""
    found: list[str] = []
    q_lower = question.lower()

    # 1. Explicit DEMO codes or 5-6 digit scheme codes
    for m in re.findall(r"\b(DEMO_[A-Za-z0-9_]+|\d{5,6})\b", question):
        if m not in found:
            found.append(m)

    # 2. Known candidates or recommended funds from saved context
    known_items = list(context.get("candidates") or []) + list(
        context.get("recommendations") or []
    )
    for item in known_items:
        cid = str(item.get("id") or item.get("instrument_id") or "")
        cname = str(item.get("name") or "")
        if cid and cid.lower() in q_lower and cid not in found:
            found.append(cid)
        elif cname and cname.lower() in q_lower and cname not in found:
            found.append(cname)

    # 3. Capitalized phrases ending with Fund / ETF
    cleaned = _STOP_PREFIXES.sub("", question.strip())
    for m in re.findall(
        r"\b([A-Z][A-Za-z0-9&\-\s]{2,60}?\b(?:Fund|ETF))\b", cleaned
    ):
        phrase = _STOP_PREFIXES.sub("", m.strip()).strip()
        if phrase and phrase not in found:
            found.append(phrase)

    return found


class FollowUpAgent:
    """Answers grounded follow-up questions about a saved advice run."""

    def __init__(self, llm: LLMClient, conn: sqlite3.Connection) -> None:
        self.llm = llm
        self.conn = conn
        self.store = MetadataStore(conn)

    def answer(
        self,
        run_id: str,
        question: str,
        saved_run: dict[str, Any],
        history_messages: list[dict[str, Any]] | None = None,
    ) -> FollowUpResponse:
        # 1. Safety / input guardrail check
        guard = check_input(question)
        if not guard.allowed:
            refusal_msg = f"{guard.message} {DISCLAIMER_NOTE}"
            return FollowUpResponse(
                answer=refusal_msg,
                referenced_funds=[],
                sources=[],
                refused=True,
            )

        context = saved_run.get("context") or {}
        response_data = saved_run.get("response") or {}
        request_data = saved_run.get("request") or {}
        rule_set = str(
            saved_run.get("rule_set")
            or response_data.get("rule_set")
            or context.get("rule_set")
            or "set2"
        )
        goal_dict = response_data.get("goal") or context.get("goal") or request_data
        goal = self._build_goal(goal_dict)

        budgeted = (
            self.llm
            if isinstance(self.llm, BudgetedLLM)
            else BudgetedLLM(self.llm, MAX_FOLLOWUP_LLM_CALLS)
        )

        with trace_context(run_id=run_id, step="followup"):
            # 2. Extract mentioned funds (LLM call 1 + deterministic heuristics)
            queries = self._extract_queries(question, context, budgeted)

            # 3. Match funds against MetadataStore & context, and run explain_exclusion
            referenced_funds, sources, evidence_blocks = self._gather_evidence(
                queries, context, response_data, goal, rule_set
            )

            # 4. Generate grounded answer (LLM call 2 with deterministic fallback)
            raw_answer = self._synthesize_answer(
                question=question,
                response_data=response_data,
                context=context,
                evidence_blocks=evidence_blocks,
                referenced_funds=referenced_funds,
                history_messages=(history_messages or [])[-6:],
                budgeted_llm=budgeted,
            )

        # 5. Compliance scrub & disclaimer
        clean_answer, _ = scrub(raw_answer)
        if "not investment advice" not in clean_answer.lower():
            clean_answer = f"{clean_answer.rstrip()} {DISCLAIMER_NOTE}"

        return FollowUpResponse(
            answer=clean_answer,
            referenced_funds=referenced_funds,
            sources=sources,
            refused=False,
        )

    @staticmethod
    def _build_goal(goal_dict: dict[str, Any]) -> Goal:
        raw_pref = str(goal_dict.get("risk_preference") or "moderate").strip().lower()
        if raw_pref not in {
            "capital_preservation",
            "conservative",
            "moderate",
            "growth",
            "aggressive",
        }:
            raw_pref = "moderate"
        return Goal(
            amount=float(goal_dict.get("amount") or 100000.0),
            horizon_years=float(goal_dict.get("horizon_years") or 5.0),
            risk_preference=raw_pref,
            purpose=str(goal_dict.get("purpose") or goal_dict.get("goal_type") or "wealth_creation"),
            excluded_categories=list(goal_dict.get("excluded_categories") or []),
            spec=goal_dict.get("spec") or None,
        )

    def _extract_queries(
        self,
        question: str,
        context: dict[str, Any],
        budgeted_llm: BudgetedLLM,
    ) -> list[str]:
        heuristics = _heuristic_fund_mentions(question, context)
        # Also check if any fund in MetadataStore is directly mentioned by name or code in question
        q_lower = question.lower()
        for f in self.store.query_funds():
            if f.code.lower() in q_lower and f.code not in heuristics:
                heuristics.append(f.code)
            elif f.name.lower() in q_lower and f.code not in heuristics:
                heuristics.append(f.code)

        llm_queries: list[str] = []
        try:
            res = budgeted_llm.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Extract any specific mutual fund names or scheme codes mentioned "
                            "in the user's question. Return an empty list if none are mentioned."
                        ),
                    },
                    {"role": "user", "content": question},
                ],
                MentionedFunds,
            )
            llm_queries = [q.strip() for q in res.fund_queries if q and q.strip()]
        except (LLMBudgetExceeded, Exception) as exc:
            log.debug("FollowUpAgent fund extraction fallback: %s", exc)

        combined: list[str] = []
        for q in heuristics + llm_queries:
            if q and q not in combined:
                combined.append(q)
        return combined

    def _resolve_fund(
        self, query: str, context: dict[str, Any]
    ) -> FundRecord | FundProfile | dict[str, Any] | None:
        q = query.strip()
        if not q:
            return None

        # 1. Exact code in MetadataStore (curated scheme or scheme_master)
        rec = self.store.find_fund(q)
        if rec is not None:
            return rec

        # 2. Exact code or name in context candidates
        q_low = q.lower()
        for c in context.get("candidates") or []:
            if str(c.get("id", "")).lower() == q_low or str(c.get("name", "")).lower() == q_low:
                f_db = self.store.get_fund(str(c.get("id"))) or self.store.find_fund(str(c.get("id")))
                return f_db if f_db is not None else c

        # 3. Search MetadataStore funds by name substring / tokens
        all_funds = self.store.query_funds()
        for f in all_funds:
            if f.code.lower() == q_low or f.name.lower() == q_low:
                return f
        for f in all_funds:
            if q_low in f.name.lower() or f.name.lower() in q_low:
                return f

        # 4. Search scheme_master or on-demand ingestion for numeric codes
        master_hits = self.store.search_master(q=q, limit=3)
        if master_hits:
            code = master_hits[0].code
            f_db = self.store.get_fund(code)
            if f_db is not None:
                return f_db
            try:
                return compute_fund_profile(self.store, code, fetch_if_missing=True)
            except Exception:
                return None

        if q.isdigit() and len(q) >= 5:
            try:
                return compute_fund_profile(self.store, q, fetch_if_missing=True)
            except Exception:
                return None

        return None

    def _compute_db_facts(self, fund: FundRecord | FundProfile) -> dict[str, float | str]:
        facts: dict[str, float | str] = {"plan": getattr(fund, "plan", "direct")}
        for k in (
            "expense_ratio",
            "aum_cr",
            "rating",
            "ytm",
            "modified_duration",
            "avg_maturity_years",
        ):
            val = getattr(fund, k, None)
            if val is not None:
                facts[k] = val

        as_of = self.store.latest_nav_date() or fund.as_of
        launch_date = getattr(fund, "launch_date", None)
        if launch_date and as_of:
            facts["track_years"] = round((as_of - launch_date).days / 365.25, 2)
        elif getattr(fund, "fund_age_years", None) is not None:
            facts["track_years"] = float(getattr(fund, "fund_age_years"))

        manager_since = getattr(fund, "manager_since", None)
        if manager_since and as_of:
            facts["manager_tenure_years"] = round((as_of - manager_since).days / 365.25, 2)
        elif getattr(fund, "manager_tenure_years", None) is not None:
            facts["manager_tenure_years"] = float(getattr(fund, "manager_tenure_years"))

        stats = self.store.get_stats(fund.code)
        if stats is not None:
            for k in (
                "cagr_3y",
                "cagr_5y",
                "roll_3y_median",
                "volatility",
                "max_drawdown",
                "sharpe",
                "sortino",
                "alpha",
                "beta",
            ):
                val = getattr(stats, k, None)
                if val is not None:
                    facts[k] = round(float(val), 4)
        else:
            for k in (
                "cagr_1y",
                "cagr_3y",
                "cagr_5y",
                "roll_3y_median",
                "volatility",
                "max_drawdown",
                "sharpe",
                "sortino",
                "alpha",
                "beta",
                "tracking_error",
            ):
                val = getattr(fund, k, None)
                if val is not None:
                    facts[k] = round(float(val), 4)
        return facts

    def _gather_evidence(
        self,
        queries: list[str],
        context: dict[str, Any],
        response_data: dict[str, Any],
        goal: Goal,
        rule_set: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        recommended_ids = set(context.get("recommended_ids") or [])
        recs_by_id = {
            str(r.get("instrument_id")): r
            for r in (response_data.get("recommendations") or context.get("recommendations") or [])
            if isinstance(r, dict) and r.get("instrument_id")
        }

        referenced_funds: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        evidence_blocks: list[str] = []
        seen_codes: set[str] = set()

        for q in queries:
            resolved = self._resolve_fund(q, context)
            if resolved is None:
                if q not in seen_codes:
                    seen_codes.add(q)
                    referenced_funds.append(
                        {"code": q, "name": q, "status": "not_found", "reasons": []}
                    )
                    evidence_blocks.append(
                        f"[not_found:{q}] Fund '{q}' was not found in the available mutual fund database."
                    )
                continue

            if isinstance(resolved, (FundRecord, FundProfile)):
                code, name, category = resolved.code, resolved.name, resolved.category
                extra_facts = self._compute_db_facts(resolved)
            else:
                code = str(resolved.get("id") or resolved.get("code") or q)
                name = str(resolved.get("name") or code)
                category = str(resolved.get("category") or "")
                extra_facts = dict(resolved.get("facts") or {})

            if code in seen_codes:
                continue
            seen_codes.add(code)

            if code in recommended_ids:
                rec = recs_by_id.get(code, {})
                referenced_funds.append(
                    {
                        "code": code,
                        "name": name,
                        "category": category,
                        "status": "recommended",
                        "weight_pct": rec.get("weight_pct", rec.get("weight")),
                        "amount": rec.get("amount"),
                        "score": rec.get("score"),
                        "reasons": [],
                    }
                )
                fact_id = f"rec:{code}"
                sources.append(
                    {
                        "source_id": fact_id,
                        "doc_type": "recommendation",
                        "title": f"{name} ({code}) recommendation",
                    }
                )
                evidence_blocks.append(
                    f"[{fact_id}] {name} ({code}, {category}) WAS recommended: "
                    f"weight={rec.get('weight_pct', rec.get('weight'))}%, "
                    f"amount=₹{rec.get('amount')}, score={rec.get('score')}. "
                    f"Rationale: {rec.get('rationale', '')} "
                    f"Pros: {rec.get('pros', [])} Cons: {rec.get('cons', [])}"
                )
                for src in (rec.get("sources") or rec.get("citation_details") or []):
                    if isinstance(src, dict) and src not in sources:
                        sources.append(src)
            else:
                reasons = explain_exclusion(
                    resolved, context, goal, rule_set, facts=extra_facts
                )
                status = (
                    "not_shortlisted"
                    if reasons and all(r["stage"] == "not_shortlisted" for r in reasons)
                    else "excluded"
                )
                referenced_funds.append(
                    {
                        "code": code,
                        "name": name,
                        "category": category,
                        "status": status,
                        "reasons": reasons,
                    }
                )
                for r in reasons:
                    fid = f"excl:{code}:{r['rule']}"
                    sources.append(
                        {
                            "source_id": fid,
                            "doc_type": r["stage"],
                            "title": f"{name} ({code}) - {r['rule']}",
                        }
                    )
                    evidence_blocks.append(
                        f"[{fid}] {name} ({code}, {category}) excluded at stage '{r['stage']}' "
                        f"(rule={r['rule']}): {r['message']} "
                        f"[fact {r['fact_name']}={r['fact_value']}, threshold={r['threshold']}]"
                    )
                for fname, fval in extra_facts.items():
                    evidence_blocks.append(f"[fact:{code}:{fname}] {fname} = {fval}")

        # Always include summary facts of the saved portfolio so general questions are grounded
        for cid, rec in recs_by_id.items():
            fid = f"portfolio:{cid}"
            if not any(s.get("source_id") == fid for s in sources):
                sources.append(
                    {
                        "source_id": fid,
                        "doc_type": "portfolio",
                        "title": f"{rec.get('name', cid)} ({cid})",
                    }
                )
            evidence_blocks.append(
                f"[{fid}] Recommended pick: {rec.get('name', cid)} ({cid}), "
                f"bucket={rec.get('bucket')}, category={rec.get('category')}, "
                f"weight={rec.get('weight_pct', rec.get('weight'))}%, "
                f"amount=₹{rec.get('amount')}, score={rec.get('score')}, "
                f"rationale={rec.get('rationale', '')}"
            )

        return referenced_funds, sources, evidence_blocks

    def _synthesize_answer(
        self,
        *,
        question: str,
        response_data: dict[str, Any],
        context: dict[str, Any],
        evidence_blocks: list[str],
        referenced_funds: list[dict[str, Any]],
        history_messages: list[dict[str, Any]],
        budgeted_llm: BudgetedLLM,
    ) -> str:
        # Build deterministic fallback first so we always have a rock-solid grounded answer
        fallback = self._deterministic_answer(
            question, response_data, context, referenced_funds
        )

        history_str = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history_messages
        )
        evidence_str = "\n".join(f"- {b}" for b in evidence_blocks) or "- No additional fund evidence."
        target_map = (
            response_data.get("target")
            or response_data.get("target_allocation")
            or context.get("target")
            or {}
        )
        target_str = ", ".join(f"{k}: {v}%" for k, v in target_map.items())
        risk_cat = (
            response_data.get("risk_category")
            or (response_data.get("profile") or {}).get("category")
            or context.get("risk_category")
        )

        sys_prompt = (
            "You are a grounded financial explanation assistant for an Indian mutual-fund advisor. "
            "Explain the saved advice run using ONLY the provided facts and exclusion reasons. "
            "Do not invent any numbers, do not modify the portfolio, and do not promise returns. "
            "Include a brief note that this is informational and not investment advice."
        )
        user_prompt = (
            f"Run Summary:\n"
            f"- Risk Category: {risk_cat}\n"
            f"- Rule Set: {response_data.get('rule_set', context.get('rule_set'))}\n"
            f"- Target Allocation: {target_str}\n\n"
            f"Grounded Evidence:\n{evidence_str}\n\n"
            f"Recent Chat:\n{history_str or '(none)'}\n\n"
            f"User Question: {question}"
        )
        try:
            out = budgeted_llm.complete(
                [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                FollowUpAnswerPayload,
            )
            if out.answer and out.answer.strip():
                return out.answer.strip()
        except (LLMBudgetExceeded, Exception) as exc:
            log.debug("FollowUpAgent answer synthesis fallback: %s", exc)

        return fallback

    @staticmethod
    def _deterministic_answer(
        question: str,
        response_data: dict[str, Any],
        context: dict[str, Any],
        referenced_funds: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = []
        recs = response_data.get("recommendations") or context.get("recommendations") or []
        recs_by_id = {str(r.get("instrument_id")): r for r in recs if isinstance(r, dict)}

        for rf in referenced_funds:
            status = rf.get("status")
            code = rf.get("code")
            name = rf.get("name") or code
            if status == "not_found":
                parts.append(
                    f"'{name}' was not found in the available mutual fund database, so it could not be evaluated or recommended."
                )
            elif status == "recommended":
                rec = recs_by_id.get(str(code), {})
                w = rec.get("weight_pct", rec.get("weight"))
                amt = rec.get("amount")
                score = rec.get("score")
                rat = rec.get("rationale") or ""
                pros = "; ".join(rec.get("pros") or [])
                parts.append(
                    f"{name} ({code}) was recommended with a {w}% allocation (₹{amt}, score {score}). "
                    f"{rat} {f'Key pros: {pros}.' if pros else ''}".strip()
                )
            else:
                reasons = rf.get("reasons") or []
                if reasons:
                    msgs = "; ".join(r.get("message", "") for r in reasons if r.get("message"))
                    parts.append(f"{name} ({code}) was not recommended because: {msgs}")
                else:
                    parts.append(
                        f"{name} ({code}) was not among the final recommended instruments for this portfolio."
                    )

        if not parts:
            target = (
                response_data.get("target")
                or response_data.get("target_allocation")
                or context.get("target")
                or {}
            )
            target_summary = ", ".join(f"{k}: {v}%" for k, v in target.items())
            rec_names = ", ".join(
                f"{r.get('name', r.get('instrument_id'))} ({r.get('weight_pct', r.get('weight'))}%)"
                for r in recs
            )
            risk_cat = (
                response_data.get("risk_category")
                or (response_data.get("profile") or {}).get("category")
                or context.get("risk_category")
                or "Moderate"
            )
            parts.append(
                f"For your {risk_cat} profile "
                f"(target allocation: {target_summary}), the recommended funds are: {rec_names or 'none'}."
            )

        parts.append(DISCLAIMER_NOTE)
        return " ".join(parts)
