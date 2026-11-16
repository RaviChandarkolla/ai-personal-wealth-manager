"""Deterministic suitability rules: hard filters (plan 1.3) and a final gate over a whole plan."""

from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from ..config import Settings, get_settings
from ..domain.categories import ALL_CATEGORIES, CATEGORIES, MULTI_ASSET_CATEGORIES, min_horizon
from ..models.core import AllocationBand, Goal
from ..models.enums import Bucket


class Violation(BaseModel):
    instrument_id: str
    rule: str
    detail: str


class SuitabilityError(Exception):
    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        super().__init__("; ".join(f"{v.instrument_id}: {v.rule} ({v.detail})" for v in violations))


def check_stock(
    instrument_id: str, category: str, facts: Mapping[str, float | str], goal: Goal,
    s: Settings | None = None,
) -> list[Violation]:
    """Hard suitability for a Direct Equity stock candidate."""
    s = s or get_settings()
    out: list[Violation] = []

    def bad(rule: str, detail: str) -> None:
        out.append(Violation(instrument_id=instrument_id, rule=rule, detail=detail))

    min_h = max(min_horizon(category, s), float(s.stock_min_horizon_years))
    if goal.horizon_years < min_h:
        bad("horizon", f"{category} needs >= {min_h:g}y, goal is {goal.horizon_years:g}y")
    rs = str(facts.get("stock_rule_set") or s.stock_rule_set)
    is_bank = bool(int(facts.get("is_bank", 0))) if isinstance(facts.get("is_bank"), int | float) else False
    pledge_cap = 20.0 if rs == "set2" else 25.0
    pledge = facts.get("promoter_pledge_pct")
    if isinstance(pledge, int | float) and float(pledge) > pledge_cap:
        bad("promoter_pledge", f"promoter pledging {pledge}% exceeds {pledge_cap}% hard limit")
    auditor = facts.get("auditor_flags")
    if isinstance(auditor, int | float) and int(auditor) > 0:
        bad("auditor_flags", f"auditor qualification flags ({int(auditor)}) > 0")
    nw = facts.get("net_worth_cr")
    if isinstance(nw, int | float) and float(nw) <= 0:
        bad("negative_net_worth", f"net worth {nw} Cr <= 0")
    de = facts.get("debt_to_equity")
    de_cap = 1.0 if rs == "set2" else 2.0
    if not is_bank and isinstance(de, int | float) and float(de) > de_cap:
        bad("debt_to_equity", f"debt/equity {de} exceeds {de_cap}x hard limit")
    cfo_ratio = facts.get("cfo_to_pat")
    if not is_bank and isinstance(cfo_ratio, int | float) and float(cfo_ratio) < 0:
        bad("negative_cfo", f"CFO/PAT {cfo_ratio}% indicates negative operating cash flow")
    ic = facts.get("interest_coverage")
    if not is_bank and rs == "set2" and isinstance(ic, int | float) and float(ic) < 1.5:
        bad("interest_coverage", f"interest coverage {ic}x below 1.5x floor")
    nnpa = facts.get("nnpa_pct")
    if is_bank and rs == "set2" and isinstance(nnpa, int | float) and float(nnpa) > 2.0:
        bad("bank_nnpa", f"bank net NPA {nnpa}% exceeds 2.0% hard limit")
    sp = facts.get("screen_passed")
    if isinstance(sp, int | float) and int(sp) == 0:
        bad("stock_screen", f"failed {rs} fundamental stock screen")
    return out


# Context rules (fit to the user's horizon / rule set). AI notes naming specific funds or stocks
# override these; data-integrity rules (plan, missing data, expense, AUM, track record, stock
# screens) always stay on.
OVERRIDABLE_RULES = frozenset({"horizon", "allowed_category", "alpha", "maturity"})


def _notes_override(goal: Goal) -> bool:
    return goal.spec is not None and goal.spec.is_custom


def check_fund(
    instrument_id: str, category: str, facts: Mapping[str, float | str], goal: Goal,
    s: Settings | None = None,
) -> list[Violation]:
    """Hard suitability for one fund/instrument; AI-notes requests waive the context rules."""
    out = _check_fund_all(instrument_id, category, facts, goal, s)
    if _notes_override(goal):
        return [v for v in out if v.rule not in OVERRIDABLE_RULES]
    return out


def overridden_violations(
    instrument_id: str, category: str, facts: Mapping[str, float | str], goal: Goal,
    s: Settings | None = None,
) -> list[Violation]:
    """Context rules this instrument breaks but the user's notes waived (shown as warnings)."""
    if not _notes_override(goal):
        return []
    return [
        v for v in _check_fund_all(instrument_id, category, facts, goal, s)
        if v.rule in OVERRIDABLE_RULES
    ]


def _check_fund_all(
    instrument_id: str, category: str, facts: Mapping[str, float | str], goal: Goal,
    s: Settings | None = None,
) -> list[Violation]:
    """Every suitability rule; missing required data is itself a violation."""
    s = s or get_settings()
    info = ALL_CATEGORIES.get(category)
    is_multi_asset = category in MULTI_ASSET_CATEGORIES
    out: list[Violation] = []

    def bad(rule: str, detail: str) -> None:
        out.append(Violation(instrument_id=instrument_id, rule=rule, detail=detail))

    if info is None:
        bad("unknown_category", category)
        return out
    excluded = [e.lower() for e in goal.excluded_categories]
    if any(e in category.lower() for e in excluded):
        bad("excluded_category", category)

    if category == "Direct Equity":
        out.extend(check_stock(instrument_id, category, facts, goal, s))
        return out

    if not is_multi_asset and s.allowed_categories and category not in s.allowed_categories:
        bad("allowed_category", f"{category} not allowed under rule set {s.fund_rule_set}")
    if s.require_direct_plan and facts.get("plan") != "direct":
        bad("plan", f"plan is {facts.get('plan')!r}; direct plan required")
    if getattr(s, "require_growth_option", True) and facts.get("option") and facts.get("option") != "growth":
        bad("option", f"option is {facts.get('option')!r}; growth option required")
    for key in ("expense_ratio", "aum_cr", "track_years"):
        if not isinstance(facts.get(key), int | float):
            bad("missing_data", key)
    er, aum, track = facts.get("expense_ratio"), facts.get("aum_cr"), facts.get("track_years")
    ceiling = s.expense_ceilings.get(category, s.default_expense_ceiling)
    if isinstance(er, int | float) and er > ceiling:
        bad("expense_ratio", f"{er}% exceeds {ceiling}% ceiling for {category}")
    floor = s.min_aum_cr_small if info.small_aum_ok else s.min_aum_cr
    if isinstance(aum, int | float) and aum < floor:
        bad("aum", f"{aum} Cr below {floor} Cr floor")
    min_track = s.min_track_years_debt if info.bucket == Bucket.DEBT else s.min_track_years_equity
    if info.bucket == Bucket.GOLD:
        min_track = s.min_track_years_debt
    if isinstance(track, int | float) and track < min_track:
        bad("track_record", f"{track}y below {min_track}y minimum")
    if s.min_rating is not None:
        rating = facts.get("rating")
        if isinstance(rating, int | float) and rating < s.min_rating:
            bad("rating", f"{rating} below {s.min_rating} floor")
    min_h = min_horizon(category, s)
    if goal.horizon_years < min_h:
        bad("horizon", f"{category} needs >= {min_h:g}y, goal is {goal.horizon_years:g}y")
    if s.require_positive_alpha and info.bucket == Bucket.EQUITY and category not in ("Index Fund", "Gold ETF"):
        alpha = facts.get("alpha")
        if isinstance(alpha, int | float) and alpha <= 0:
            bad("alpha", f"alpha {alpha} <= 0; positive alpha required for active equity")
    if s.match_maturity_to_horizon and info.bucket == Bucket.DEBT:
        avg_mat = facts.get("avg_maturity_years")
        if isinstance(avg_mat, int | float) and avg_mat > goal.horizon_years:
            bad("maturity", f"average maturity {avg_mat}y exceeds goal horizon {goal.horizon_years:g}y")

    # M13 asset-class specific suitability checks
    if category == "Bank FD":
        cr = str(facts.get("credit_rating") or "").upper()
        if cr and cr not in ("AAA", "SOV", "AA+", "AA"):
            bad("credit_rating", f"FD credit rating {cr} is below AA floor")
        ew = facts.get("early_withdrawal_penalty")
        if isinstance(ew, int | float) and float(ew) > 1.5:
            bad("early_withdrawal_penalty", f"early withdrawal penalty {ew}% exceeds 1.5% cap")
        dicgc = facts.get("dicgc_insured_limit_lakhs")
        if isinstance(dicgc, int | float) and float(dicgc) < 5.0:
            bad("dicgc_limit", f"DICGC insurance {dicgc}L below standard 5L protection")
    elif category == "Large Cap Equity":
        pledge = facts.get("promoter_pledge_pct")
        if isinstance(pledge, int | float) and float(pledge) > 25.0:
            bad("promoter_pledge", f"promoter pledging {pledge}% exceeds 25% hard limit")
        de = facts.get("debt_to_equity")
        if isinstance(de, int | float) and float(de) > 2.0:
            bad("debt_to_equity", f"debt/equity {de} exceeds 2.0x hard limit")
    elif category in ("REIT", "InvIT"):
        min_inv = facts.get("min_investment_inr")
        if isinstance(min_inv, int | float) and float(min_inv) >= 5_000_000:
            bad("min_investment", f"minimum ticket {min_inv} INR (PMS/AIF) excluded for retail")
        occ = facts.get("occupancy_pct")
        if isinstance(occ, int | float) and float(occ) < 75.0:
            bad("occupancy", f"occupancy {occ}% below 75% floor")
        lev = facts.get("leverage_pct")
        if isinstance(lev, int | float) and float(lev) > 49.0:
            bad("leverage", f"leverage {lev}% exceeds 49% SEBI REIT/InvIT ceiling")

    return out



class PlanLine(BaseModel):
    instrument_id: str
    bucket: Bucket
    category: str
    weight: float  # percent of portfolio


def verify_plan(
    lines: Sequence[PlanLine],
    facts_by_id: Mapping[str, Mapping[str, float | str]],
    goal: Goal,
    band: AllocationBand,
    s: Settings | None = None,
) -> list[Violation]:
    """Final gate. Returns every violation; caller blocks the output if any."""
    s = s or get_settings()
    out: list[Violation] = []
    total = sum(line.weight for line in lines)
    if total > 100 + 1e-6:
        out.append(Violation(instrument_id="*", rule="total_weight", detail=f"{total:.2f}% > 100%"))
    per_bucket: dict[Bucket, list[PlanLine]] = {}
    for line in lines:
        per_bucket.setdefault(line.bucket, []).append(line)
        if line.category == "Direct Equity":
            if line.weight > s.max_weight_per_stock + 1e-6:
                out.append(Violation(
                    instrument_id=line.instrument_id, rule="stock_concentration",
                    detail=f"{line.weight:.2f}% > {s.max_weight_per_stock}% per-stock cap"))
        elif line.weight > s.max_weight_per_instrument + 1e-6:
            out.append(Violation(
                instrument_id=line.instrument_id, rule="concentration",
                detail=f"{line.weight:.2f}% > {s.max_weight_per_instrument}% cap"))
        out.extend(check_fund(line.instrument_id, line.category,
                              facts_by_id.get(line.instrument_id, {}), goal, s))
    for bucket, items in per_bucket.items():
        non_stock_items = [i for i in items if i.category != "Direct Equity"]
        stock_items = [i for i in items if i.category == "Direct Equity"]
        if len(non_stock_items) > s.max_instruments_per_bucket:
            out.append(Violation(instrument_id="*", rule="bucket_count",
                                 detail=f"{len(non_stock_items)} instruments in {bucket.value}"))
        if len(stock_items) > s.max_stocks:
            out.append(Violation(instrument_id="*", rule="stock_bucket_count",
                                 detail=f"{len(stock_items)} direct stocks in {bucket.value}"))
        hi = band.ranges.get(bucket, (0.0, 0.0))[1]
        if bucket == Bucket.DEBT:  # debt is the remainder after the other buckets' minimums
            hi = max(hi, 100 - sum(lo for b, (lo, _) in band.ranges.items() if b != Bucket.DEBT))
        w = sum(i.weight for i in items)
        if w > hi + 1e-6:
            out.append(Violation(instrument_id="*", rule="band",
                                 detail=f"{bucket.value} {w:.2f}% exceeds band max {hi}%"))
    return out
