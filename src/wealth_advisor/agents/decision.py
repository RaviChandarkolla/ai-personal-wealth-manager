"""M8 Decision Maker: hard suitability -> rank by score -> shortlist -> size (M2) -> cited action.

Fully deterministic: rationale text is assembled from already-validated analyst bullets.
"""

from collections.abc import Mapping

from pydantic import BaseModel

from ..config import Settings, get_settings
from ..domain.momentum import tilt_factor
from ..domain.portfolio_spec import active_custom, custom_band, effective_settings
from ..domain.portfolio_spec import stock_sleeve_weight as spec_stock_sleeve
from ..domain.sizing import Candidate, size_positions, size_stock_sleeve
from ..guardrails.grounding import citations_in
from ..guardrails.suitability import Violation, check_fund
from ..models.core import (
    AllocationBand,
    AnalystVerdict,
    Citation,
    DecisionRecord,
    DossierEntry,
    Fact,
    Goal,
    NoData,
    PortfolioSpec,
    Recommendation,
    ResearchDossier,
    UserFinancialProfile,
)
from ..models.enums import Action, Bucket

PROMPT_VERSION = "decision.v1-deterministic"


class PlanRow(BaseModel):
    instrument_id: str
    name: str
    bucket: Bucket
    category: str
    weight: float
    amount: float
    score: float
    expense_ratio: float | None
    rating: float | None
    cagr_5y: float | None
    rationale: str
    citation_ids: list[str]
    action: Action = Action.BUY
    current_amount: float | None = None
    amount_delta: float | None = None
    warning: str | None = None
    instrument_type: str = "mf"
    symbol: str | None = None
    sector: str | None = None
    market_cap_cr: float | None = None
    checks_passed: str | None = None
    checks_passed_label: str | None = None


class DecisionResult(BaseModel):
    record: DecisionRecord
    rows: list[PlanRow]
    rejected: list[Violation] = []
    escalations: list[str] = []
    unallocated_weight: float = 0.0
    band: AllocationBand
    total_amount: float
    # per-bucket inputs/outputs of the deterministic sizing step, for replay
    sizing: dict[str, dict] = {}
    stock_sizing: dict = {}
    exits: list[Recommendation] = []


def determine_action(
    score: float,
    *,
    is_held: bool,
    hard_suitability_failed: bool = False,
    drift_needs_increase: bool = False,
    s: Settings | None = None,
) -> Action | None:
    """Deterministic action rules from §1.5 of plan-by-claude.md.

    - Not held: `score >= 70` -> `BUY` (else None)
    - Held:
      - hard suitability fail or `score < 35` -> `EXIT`
      - `35 <= score < 50` -> `DECREASE`
      - `50 <= score < 70` -> `HOLD`
      - `score >= 70` -> `INCREASE` if `drift_needs_increase` else `HOLD`
    """
    s = s or get_settings()
    if hard_suitability_failed:
        return Action.EXIT if is_held else None
    if not is_held:
        return Action.BUY if score >= s.rebalance_buy_min_score else None
    if score < s.rebalance_decrease_min_score:
        return Action.EXIT
    if score < s.rebalance_hold_min_score:
        return Action.DECREASE
    if score < s.rebalance_buy_min_score:
        return Action.HOLD
    return Action.INCREASE if drift_needs_increase else Action.HOLD


def _rationale(v: AnalystVerdict) -> str:
    text = v.pros[0].rstrip(".") if v.pros else ""
    if v.cons:
        text += f". Watch: {v.cons[0]}"
    return text


def _citations(entry_facts: list[Fact], text: str) -> tuple[list[Citation], list[str]]:
    by_id = {f.id: f for f in entry_facts}
    ids = [i for i in dict.fromkeys(citations_in(text)) if i in by_id]
    if not ids:
        ids = [next(f.id for f in entry_facts if f.name == "expense_ratio")] if any(
            f.name == "expense_ratio" for f in entry_facts) else [entry_facts[0].id]
    return [by_id[i].citation for i in ids], ids


def decide(
    *,
    run_id: str,
    profile: UserFinancialProfile,
    goal: Goal,
    target: Mapping[Bucket, float],
    entries: list[DossierEntry],
    verdicts: Mapping[str, AnalystVerdict],
    model: str = "",
    s: Settings | None = None,
    current_holdings: Mapping[str, float] | None = None,
    drift_breached_buckets: set[Bucket] | None = None,
    dossier: ResearchDossier | None = None,
    include_stocks: bool = False,
    trend_based: bool = False,
    sleeve_shift_pct: float = 0.0,
    spec: PortfolioSpec | None = None,
) -> DecisionResult:
    spec = active_custom(spec)
    s = effective_settings(s or get_settings(), spec)
    rejected: list[Violation] = []
    escalations: list[str] = []
    exits: list[Recommendation] = []
    held_map = {k: float(v) for k, v in (current_holdings or {}).items() if v > 0}
    recalibrating = current_holdings is not None

    timeout_penalty = 0.0
    if dossier is not None:
        for sec_name in ("equity", "mf", "commodity", "fixed_income", "alt", "news_context", "sentiment"):
            sec_val = getattr(dossier, sec_name, None)
            if isinstance(sec_val, NoData) and sec_val.reason == "timeout":
                escalations.append(
                    f"ESCALATE: dossier section '{sec_name}' timed out (NoData: timeout); human review required"
                )
                timeout_penalty += 0.25

    by_bucket: dict[Bucket, list[tuple[DossierEntry, AnalystVerdict]]] = {}
    for e in entries:
        if not include_stocks and e.instrument.category == "Direct Equity":
            continue
        fm = {f.name: f.value for f in e.facts}
        is_held = e.instrument.id in held_map
        violations = check_fund(e.instrument.id, e.instrument.category, fm, goal, s)
        v = verdicts.get(e.instrument.id)
        if violations:
            rejected.extend(violations)
            if is_held and v is not None:
                text = _rationale(v)
                citations, _ = _citations(e.facts, text)
                exits.append(
                    Recommendation(
                        instrument_id=e.instrument.id,
                        action=Action.EXIT,
                        target_weight=0.0,
                        rationale=text,
                        citations=citations,
                    )
                )
            continue
        if v is None:
            continue
        eff_conf = max(0.0, v.confidence - timeout_penalty)
        if spec is None and eff_conf < s.min_confidence:
            escalations.append(f"{e.instrument.id}: confidence {eff_conf:.2f} below "
                               f"{s.min_confidence:.2f}; needs human review")
            continue


        min_score = s.rebalance_decrease_min_score if (recalibrating and is_held) else (
            s.rebalance_buy_min_score if recalibrating else s.buy_min_score
        )
        # Note: in recalibration mode, if a bucket has no candidates >= 70, allow candidates >= buy_min_score (50)
        # Funds and stocks the user asked for by name are not dropped for a low score.
        if spec is None and v.score < s.buy_min_score and not (
            recalibrating and is_held and v.score >= s.rebalance_decrease_min_score
        ):
            rejected.append(Violation(instrument_id=e.instrument.id, rule="score",
                                      detail=f"score {v.score:.1f} below {min_score}"))
            if is_held:
                text = _rationale(v)
                citations, _ = _citations(e.facts, text)
                exits.append(
                    Recommendation(
                        instrument_id=e.instrument.id,
                        action=Action.EXIT,
                        target_weight=0.0,
                        rationale=text,
                        citations=citations,
                    )
                )
            continue
        by_bucket.setdefault(e.instrument.asset_class, []).append((e, v))

    rows: list[PlanRow] = []
    recs: list[Recommendation] = []
    sizing: dict[str, dict] = {}
    stock_sizing: dict = {}
    for bucket, weight in target.items():
        if weight <= 0:
            continue
        all_items = by_bucket.get(bucket, [])
        if bucket == Bucket.EQUITY and include_stocks:
            stock_items = [(e, v) for e, v in all_items if e.instrument.category == "Direct Equity"]
            items = [(e, v) for e, v in all_items if e.instrument.category != "Direct Equity"]
        else:
            stock_items = []
            items = [
                (e, v) for e, v in all_items
                if e.instrument.category != "Direct Equity"
            ]

        mf_weight = weight
        if bucket == Bucket.EQUITY and include_stocks:
            if spec is not None:
                stock_sleeve_weight = spec_stock_sleeve(spec, weight)
            else:
                sleeve_pct = (
                    s.stock_sleeve_pct_of_equity.get(profile.category.value, 0.0)
                    if goal.horizon_years >= s.stock_min_horizon_years
                    else 0.0
                )
                if trend_based and sleeve_pct > 0:
                    sleeve_pct = max(0.0, sleeve_pct + sleeve_shift_pct)
                stock_sleeve_weight = round(weight * sleeve_pct / 100.0, 6)
            if stock_sleeve_weight > 0 and stock_items:
                tilts: dict[str, float] = {}
                for e, v in stock_items:
                    mom = {f.name: f.value for f in e.facts}.get("momentum_score")
                    tilts[e.instrument.id] = (
                        tilt_factor(float(mom), s.trend_tilt_min, s.trend_tilt_max)
                        if trend_based and isinstance(mom, int | float)
                        else 1.0
                    )
                stock_cands = [
                    Candidate(id=e.instrument.id, score=v.score * tilts[e.instrument.id])
                    for e, v in stock_items
                ]
                sectors = {
                    e.instrument.id: str({f.name: f.value for f in e.facts}.get("sector") or "Unknown")
                    for e, _ in stock_items
                }
                stock_positions = size_stock_sleeve(
                    stock_cands, sectors, stock_sleeve_weight, goal.amount, s, max_stocks=s.max_stocks
                )
                actual_stock_weight = round(sum(p.weight for p in stock_positions), 6)
                stock_sizing = {
                    "sleeve_weight": stock_sleeve_weight,
                    "actual_weight": actual_stock_weight,
                    "max_stocks": s.max_stocks,
                    "sectors": sectors,
                    "candidates": [c.model_dump() for c in stock_cands],
                    "trend_tilts": tilts if trend_based else {},
                    "positions": [p.model_dump() for p in stock_positions],
                }
                stock_lookup = {e.instrument.id: (e, v) for e, v in stock_items}
                for p in stock_positions:
                    if p.weight <= 0:
                        continue
                    e, v = stock_lookup[p.id]
                    fm = {f.name: f.value for f in e.facts}
                    text = _rationale(v)
                    citations, ids = _citations(e.facts, text)
                    cp = fm.get("checks_passed")
                    ct = fm.get("checks_total")
                    cp_label = (
                        f"{int(cp)}/{int(ct)}"
                        if isinstance(cp, int | float) and isinstance(ct, int | float)
                        else None
                    )
                    mcap_val = fm.get("market_cap_cr")
                    mcap_float = float(mcap_val) if isinstance(mcap_val, int | float) else None
                    cagr_val = fm.get("pat_cagr_5y") or fm.get("rev_cagr_5y") or fm.get("cagr_5y")
                    cagr_float = float(cagr_val) if isinstance(cagr_val, int | float) else None
                    recs.append(Recommendation(
                        instrument_id=p.id, action=Action.BUY, target_weight=p.weight,
                        rationale=text, citations=citations))
                    rows.append(PlanRow(
                        instrument_id=p.id, name=e.instrument.name, bucket=bucket,
                        category=e.instrument.category, weight=p.weight, amount=p.amount, score=v.score,
                        expense_ratio=None,
                        rating=None,
                        cagr_5y=cagr_float,
                        rationale=text, citation_ids=ids,
                        action=Action.BUY,
                        instrument_type="stock",
                        symbol=p.id,
                        sector=sectors.get(p.id),
                        market_cap_cr=mcap_float,
                        checks_passed=cp_label,
                        checks_passed_label=cp_label,
                    ))
                mf_weight = round(max(0.0, weight - actual_stock_weight), 6)

        if mf_weight <= 0:
            continue
        if not items:
            if not (bucket == Bucket.EQUITY and include_stocks and stock_sizing.get("actual_weight", 0) > 0):
                escalations.append(f"{bucket.value}: no eligible instruments for a {mf_weight:.1f}% target")
            continue
        cands = [Candidate(id=e.instrument.id, score=v.score) for e, v in items]
        positions = size_positions(cands, mf_weight, goal.amount, s)
        sizing[bucket.value] = {
            "weight": mf_weight,
            "candidates": [c.model_dump() for c in cands],
            "positions": [p.model_dump() for p in positions],
        }
        lookup = {e.instrument.id: (e, v) for e, v in items}
        pos_ids = {p.id for p in positions if p.weight > 0}
        # Any held instrument in this bucket that didn't get sized into positions is exited
        if recalibrating:
            for e, v in items:
                if e.instrument.id in held_map and e.instrument.id not in pos_ids:
                    text = _rationale(v)
                    citations, _ = _citations(e.facts, text)
                    exits.append(
                        Recommendation(
                            instrument_id=e.instrument.id,
                            action=Action.EXIT,
                            target_weight=0.0,
                            rationale=text,
                            citations=citations,
                        )
                    )
        for p in positions:
            if p.weight <= 0:
                continue
            e, v = lookup[p.id]
            fm = {f.name: f.value for f in e.facts}
            text = _rationale(v)
            citations, ids = _citations(e.facts, text)

            if recalibrating:
                is_held = p.id in held_map
                cur_amt = held_map.get(p.id, 0.0)
                delta = round(p.amount - cur_amt, 2)
                drift_inc = bool(
                    (drift_breached_buckets and bucket in drift_breached_buckets and delta > 0)
                    or (is_held and delta > max(s.min_ticket, cur_amt * 0.05))
                )
                act = determine_action(
                    v.score,
                    is_held=is_held,
                    hard_suitability_failed=False,
                    drift_needs_increase=drift_inc,
                    s=s,
                ) or (Action.BUY if not is_held else Action.HOLD)
                if is_held and delta < -max(s.min_ticket, cur_amt * 0.05) and act == Action.HOLD:
                    act = Action.DECREASE
            else:
                act = Action.BUY
                cur_amt = None
                delta = None

            recs.append(Recommendation(
                instrument_id=p.id, action=act, target_weight=p.weight,
                rationale=text, citations=citations))
            rows.append(PlanRow(
                instrument_id=p.id, name=e.instrument.name, bucket=bucket,
                category=e.instrument.category, weight=p.weight, amount=p.amount, score=v.score,
                expense_ratio=fm.get("expense_ratio"),  # type: ignore[arg-type]
                rating=fm.get("rating"),  # type: ignore[arg-type]
                cagr_5y=fm.get("cagr_5y"),  # type: ignore[arg-type]
                rationale=text, citation_ids=ids,
                action=act, current_amount=cur_amt, amount_delta=delta,
                instrument_type="mf"))
    return DecisionResult(
        record=DecisionRecord(
            run_id=run_id, profile_id=profile.profile_id, profile_version=profile.version,
            recommendations=recs, model=model, prompt_version=PROMPT_VERSION),
        rows=rows, rejected=rejected, escalations=escalations,
        unallocated_weight=round(100 - sum(r.weight for r in rows), 6),
        band=custom_band(dict(target)) if spec is not None else profile.allocation_band,
        total_amount=goal.amount, sizing=sizing,
        stock_sizing=stock_sizing,
        exits=exits,
    )

