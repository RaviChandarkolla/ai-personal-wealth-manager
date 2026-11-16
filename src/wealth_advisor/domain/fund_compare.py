"""Deterministic head-to-head mutual fund comparison engine (Course Notes S3–S19)."""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from ..config import get_settings
from ..data.on_demand import FundProfile
from ..models.enums import Bucket
from .rulesets import get_rule_set_overrides


class CriterionSpec(BaseModel):
    id: str
    session_ref: str
    label: str
    direction: Literal["higher", "lower", "sweet_spot", "direct_wins"]
    default_weight: float
    category_neutral: bool
    applies_to_buckets: list[str] | None = None
    applies_to_categories: list[str] | None = None
    unit: str = ""


class CriterionRow(BaseModel):
    id: str
    session_ref: str
    label: str
    direction: str
    category_neutral: bool
    unit: str = ""
    raw_weight: float
    effective_weight: float = 0.0
    value_a: float | str | None = None
    value_b: float | str | None = None
    points_a: float = 0.0
    points_b: float = 0.0
    winner: Literal["A", "B", "tie"] | None = None
    status: Literal["scored", "no_data", "not_applicable"]
    fact_id_a: str | None = None
    fact_id_b: str | None = None


class ChecklistItem(BaseModel):
    id: str
    session_ref: str
    label: str
    status: Literal["pass", "flag", "info", "no_data"]
    detail: str


class NotEvaluatedItem(BaseModel):
    id: str
    session_ref: str
    label: str
    status: str = "not evaluated"
    reason: str


class ComparisonResult(BaseModel):
    fund_a: FundProfile
    fund_b: FundProfile
    same_category: bool
    warnings: list[str] = []
    criteria: list[CriterionRow]
    checklist: dict[str, list[ChecklistItem]]
    scores: dict[str, float]
    winner: Literal["A", "B", "tie"]
    winner_code: str | None = None
    winner_name: str | None = None
    ranking: list[str]
    not_evaluated: list[NotEvaluatedItem]
    as_of: date


CRITERIA: list[CriterionSpec] = [
    CriterionSpec(
        id="plan_type",
        session_ref="Plan Structure & Costs",
        label="Direct vs Regular",
        direction="direct_wins",
        default_weight=5.0,
        category_neutral=True,
    ),
    CriterionSpec(
        id="expense_ratio",
        session_ref="Expense & Fee Discipline",
        label="Total Expense Ratio",
        direction="lower",
        default_weight=10.0,
        category_neutral=False,
        unit="%",
    ),
    CriterionSpec(
        id="roll_3y_median",
        session_ref="Rolling Return Consistency",
        label="3Y median rolling return",
        direction="higher",
        default_weight=12.0,
        category_neutral=False,
        unit="%",
    ),
    CriterionSpec(
        id="rolling_consistency_3y",
        session_ref="Rolling Return Consistency",
        label="% of 3Y rolling windows > rf",
        direction="higher",
        default_weight=8.0,
        category_neutral=True,
        unit="%",
    ),
    CriterionSpec(
        id="cagr_5y",
        session_ref="Historical Return Track Record",
        label="5Y CAGR",
        direction="higher",
        default_weight=8.0,
        category_neutral=False,
        unit="%",
    ),
    CriterionSpec(
        id="cagr_3y",
        session_ref="Historical Return Track Record",
        label="3Y CAGR",
        direction="higher",
        default_weight=5.0,
        category_neutral=False,
        unit="%",
    ),
    CriterionSpec(
        id="sharpe",
        session_ref="Risk-Adjusted Return Ratios",
        label="Sharpe ratio",
        direction="higher",
        default_weight=10.0,
        category_neutral=True,
    ),
    CriterionSpec(
        id="sortino",
        session_ref="Risk-Adjusted Return Ratios",
        label="Sortino ratio",
        direction="higher",
        default_weight=8.0,
        category_neutral=True,
    ),
    CriterionSpec(
        id="alpha",
        session_ref="Benchmark Outperformance (Alpha)",
        label="Jensen's alpha vs category proxy",
        direction="higher",
        default_weight=8.0,
        category_neutral=True,
        unit="pp",
    ),
    CriterionSpec(
        id="beta",
        session_ref="Market Sensitivity (Beta)",
        label="Beta (lower volatility sensitivity)",
        direction="lower",
        default_weight=4.0,
        category_neutral=False,
    ),
    CriterionSpec(
        id="volatility",
        session_ref="Volatility & Risk Control",
        label="Annualised volatility",
        direction="lower",
        default_weight=6.0,
        category_neutral=False,
        unit="%",
    ),
    CriterionSpec(
        id="max_drawdown",
        session_ref="Downside & Drawdown Protection",
        label="Max drawdown",
        direction="lower",
        default_weight=7.0,
        category_neutral=True,
        unit="%",
    ),
    CriterionSpec(
        id="fund_age_years",
        session_ref="Fund Longevity & Track Record",
        label="Track record length",
        direction="higher",
        default_weight=4.0,
        category_neutral=True,
        unit="yrs",
    ),
    CriterionSpec(
        id="aum_band",
        session_ref="Fund Size & Liquidity",
        label="AUM sweet spot (500–50,000 Cr)",
        direction="sweet_spot",
        default_weight=4.0,
        category_neutral=True,
        unit="Cr",
    ),
    CriterionSpec(
        id="manager_tenure_years",
        session_ref="Fund Manager Stability",
        label="Fund manager tenure",
        direction="higher",
        default_weight=6.0,
        category_neutral=True,
        unit="yrs",
    ),
    CriterionSpec(
        id="tracking_error",
        session_ref="Passive Index Tracking",
        label="Tracking error vs benchmark",
        direction="lower",
        default_weight=15.0,
        category_neutral=False,
        applies_to_categories=["Index", "Index Fund", "Gold ETF / FoF", "Gold ETF"],
        unit="%",
    ),
    CriterionSpec(
        id="ytm",
        session_ref="Debt Yield & Credit Profile",
        label="Yield to Maturity",
        direction="higher",
        default_weight=6.0,
        category_neutral=False,
        applies_to_buckets=["debt"],
        unit="%",
    ),
    CriterionSpec(
        id="modified_duration",
        session_ref="Interest Rate Sensitivity",
        label="Modified duration (rate risk)",
        direction="lower",
        default_weight=5.0,
        category_neutral=False,
        applies_to_buckets=["debt"],
        unit="yrs",
    ),
]


NOT_EVALUATED: list[NotEvaluatedItem] = [
    NotEvaluatedItem(
        id="exit_load",
        session_ref="Expense & Fee Discipline",
        label="Exit load",
        reason="Not evaluated: scheme-level exit load schedules are not published in AMFI feeds.",
    ),
    NotEvaluatedItem(
        id="portfolio_turnover",
        session_ref="Portfolio Churn & Costs",
        label="Portfolio turnover",
        reason="Not evaluated: monthly portfolio turnover ratios are not included in AMFI feeds.",
    ),
    NotEvaluatedItem(
        id="top_holdings_sectors",
        session_ref="Portfolio Concentration",
        label="Top 3 holdings and sectors",
        reason="Not evaluated: underlying stock and sector weights require monthly AMC factsheets.",
    ),
    NotEvaluatedItem(
        id="riskometer",
        session_ref="SEBI Riskometer Classification",
        label="SEBI Riskometer",
        reason="Not evaluated: riskometer labels are not included in AMFI NAVAll.txt.",
    ),
    NotEvaluatedItem(
        id="credit_rating_mix",
        session_ref="Debt Credit Quality",
        label="Credit rating mix",
        reason="Not evaluated: debt rating breakdown (AAA/AA/sov) is not in AMFI feeds.",
    ),
]


def _extract_value(profile: FundProfile, crit_id: str) -> tuple[float | str | None, str | None]:
    if crit_id == "plan_type":
        return profile.plan, f"{profile.code}:plan_type"
    if crit_id == "aum_band":
        return profile.aum_cr, (f"{profile.code}:aum_cr" if profile.aum_cr is not None else None)
    val = getattr(profile, crit_id, None)
    fact_id = f"{profile.code}:{crit_id}" if val is not None else None
    return val, fact_id


def _aum_fitness(aum_cr: float, min_aum: float = 500.0, max_aum: float = 50_000.0) -> float:
    if min_aum <= aum_cr <= max_aum:
        return 1.0
    if aum_cr < min_aum:
        return max(0.0, aum_cr / min_aum)
    return max(0.0, 1.0 - (aum_cr - max_aum) / 100_000.0)


def _score_pair(
    direction: str,
    val_a: float | str,
    val_b: float | str,
) -> tuple[float, float, Literal["A", "B", "tie"]]:
    """Return (share_a, share_b, winner) where share_a + share_b == 1.0 and symmetry holds."""
    if direction == "direct_wins":
        da = 1.0 if str(val_a).lower() == "direct" else 0.0
        db = 1.0 if str(val_b).lower() == "direct" else 0.0
        if da == db:
            return 0.5, 0.5, "tie"
        return (1.0, 0.0, "A") if da > db else (0.0, 1.0, "B")

    fa = float(val_a)
    fb = float(val_b)

    if direction == "sweet_spot":
        fit_a = _aum_fitness(fa)
        fit_b = _aum_fitness(fb)
        diff = fit_a - fit_b
        if abs(diff) < 0.01:
            return 0.5, 0.5, "tie"
        sa = 0.5 + 0.5 * max(-1.0, min(1.0, diff))
        sb = 1.0 - sa
        return sa, sb, ("A" if sa > sb else "B")

    denom = max(abs(fa), abs(fb), 1.0)
    raw_diff = (fa - fb) if direction == "higher" else (fb - fa)
    rel_diff = raw_diff / denom
    if abs(rel_diff) < 0.01 or abs(fa - fb) < 1e-6:
        return 0.5, 0.5, "tie"

    adv = max(-1.0, min(1.0, rel_diff / 0.25))
    sa = 0.5 + 0.5 * adv
    sb = 1.0 - sa
    return sa, sb, ("A" if sa > sb else "B")


def _criterion_applies(crit: CriterionSpec, a: FundProfile, b: FundProfile, same_category: bool) -> bool:
    if not same_category and not crit.category_neutral:
        return False
    if crit.applies_to_categories is not None:
        cats_lower = {c.lower() for c in crit.applies_to_categories}
        if a.category.lower() not in cats_lower or b.category.lower() not in cats_lower:
            return False
    if crit.applies_to_buckets is not None:
        ba = a.bucket.value if a.bucket else ""
        bb = b.bucket.value if b.bucket else ""
        if ba not in crit.applies_to_buckets or bb not in crit.applies_to_buckets:
            return False
    return True


def build_checklist(
    profile: FundProfile,
    *,
    peer_median_ytm: float | None = None,
) -> list[ChecklistItem]:
    """Build the Set 2 red-flag and informational checklist for a single fund."""
    set2 = get_rule_set_overrides("set2")
    items: list[ChecklistItem] = []

    # 1. Direct vs Regular plan
    is_direct = profile.plan.lower() == "direct"
    items.append(
        ChecklistItem(
            id="regular_plan",
            session_ref="Plan Structure & Costs",
            label="Plan type (Direct required)",
            status="pass" if is_direct else "flag",
            detail="Direct plan" if is_direct else "Regular plan embeds distributor commission",
        )
    )

    # 2. TER vs Set 2 category ceiling
    ceilings: dict[str, float] = set2.get("expense_ceilings", {})
    default_ceil: float = float(set2.get("default_expense_ceiling", 0.85))
    cat_key = profile.category
    if cat_key == "Index":
        cat_key = "Index Fund"
    elif cat_key == "Gold ETF / FoF":
        cat_key = "Gold ETF"
    ceiling = ceilings.get(cat_key, ceilings.get(profile.category, default_ceil))
    if profile.expense_ratio is None:
        items.append(
            ChecklistItem(
                id="ter_ceiling",
                session_ref="Expense & Fee Discipline",
                label=f"TER ceiling (≤ {ceiling:.2f}%)",
                status="no_data",
                detail="TER not available",
            )
        )
    elif profile.expense_ratio > ceiling:
        items.append(
            ChecklistItem(
                id="ter_ceiling",
                session_ref="Expense & Fee Discipline",
                label=f"TER ceiling (≤ {ceiling:.2f}%)",
                status="flag",
                detail=f"TER {profile.expense_ratio:.2f}% exceeds category ceiling of {ceiling:.2f}%",
            )
        )
    else:
        items.append(
            ChecklistItem(
                id="ter_ceiling",
                session_ref="Expense & Fee Discipline",
                label=f"TER ceiling (≤ {ceiling:.2f}%)",
                status="pass",
                detail=f"TER {profile.expense_ratio:.2f}% is within {ceiling:.2f}% ceiling",
            )
        )

    # 3. Minimum track record
    is_debt_or_gold = profile.bucket in (Bucket.DEBT, Bucket.GOLD)
    min_years = float(
        set2.get("min_track_years_debt" if is_debt_or_gold else "min_track_years_equity", 5.0)
    )
    if profile.fund_age_years is None:
        items.append(
            ChecklistItem(
                id="track_record",
                session_ref="Fund Longevity & Track Record",
                label=f"Track record (≥ {min_years:.0f} yrs)",
                status="no_data",
                detail="Fund age not available",
            )
        )
    elif profile.fund_age_years < min_years:
        items.append(
            ChecklistItem(
                id="track_record",
                session_ref="Fund Longevity & Track Record",
                label=f"Track record (≥ {min_years:.0f} yrs)",
                status="flag",
                detail=f"Track record {profile.fund_age_years:.1f} yrs is below {min_years:.0f} yrs minimum",
            )
        )
    else:
        items.append(
            ChecklistItem(
                id="track_record",
                session_ref="Fund Longevity & Track Record",
                label=f"Track record (≥ {min_years:.0f} yrs)",
                status="pass",
                detail=f"Track record {profile.fund_age_years:.1f} yrs meets {min_years:.0f} yrs minimum",
            )
        )

    # 4. AUM sweet spot (500–50,000 Cr)
    min_aum = 500.0
    max_aum = 50_000.0
    if profile.aum_cr is None:
        items.append(
            ChecklistItem(
                id="aum_sweet_spot",
                session_ref="Fund Size & Liquidity",
                label="AUM sweet spot (500–50,000 Cr)",
                status="no_data",
                detail="AUM not available",
            )
        )
    elif profile.aum_cr < min_aum or profile.aum_cr > max_aum:
        items.append(
            ChecklistItem(
                id="aum_sweet_spot",
                session_ref="Fund Size & Liquidity",
                label="AUM sweet spot (500–50,000 Cr)",
                status="flag",
                detail=f"AUM ₹{profile.aum_cr:,.0f} Cr is outside the 500–50,000 Cr sweet spot",
            )
        )
    else:
        items.append(
            ChecklistItem(
                id="aum_sweet_spot",
                session_ref="Fund Size & Liquidity",
                label="AUM sweet spot (500–50,000 Cr)",
                status="pass",
                detail=f"AUM ₹{profile.aum_cr:,.0f} Cr is within the 500–50,000 Cr sweet spot",
            )
        )

    # 5. Manager tenure >= 3 years
    min_tenure = float(set2.get("min_manager_tenure_years", 3.0))
    if profile.manager_tenure_years is None:
        items.append(
            ChecklistItem(
                id="manager_tenure",
                session_ref="Fund Manager Stability",
                label=f"Manager tenure (≥ {min_tenure:.0f} yrs)",
                status="no_data",
                detail="Manager tenure not available (curated funds only)",
            )
        )
    elif profile.manager_tenure_years < min_tenure:
        items.append(
            ChecklistItem(
                id="manager_tenure",
                session_ref="Fund Manager Stability",
                label=f"Manager tenure (≥ {min_tenure:.0f} yrs)",
                status="flag",
                detail=f"Manager tenure {profile.manager_tenure_years:.1f} yrs is below {min_tenure:.0f} yrs",
            )
        )
    else:
        items.append(
            ChecklistItem(
                id="manager_tenure",
                session_ref="Fund Manager Stability",
                label=f"Manager tenure (≥ {min_tenure:.0f} yrs)",
                status="pass",
                detail=f"Manager tenure {profile.manager_tenure_years:.1f} yrs (≥ {min_tenure:.0f} yrs)",
            )
        )

    # 6. Alpha <= 0
    if profile.alpha is None:
        items.append(
            ChecklistItem(
                id="alpha_positive",
                session_ref="Benchmark Outperformance (Alpha)",
                label="Positive Jensen's alpha (> 0)",
                status="no_data",
                detail="Alpha not available",
            )
        )
    elif profile.alpha <= 0:
        items.append(
            ChecklistItem(
                id="alpha_positive",
                session_ref="Benchmark Outperformance (Alpha)",
                label="Positive Jensen's alpha (> 0)",
                status="flag",
                detail=f"Alpha is {profile.alpha:.2f} pp (≤ 0 vs benchmark proxy)",
            )
        )
    else:
        items.append(
            ChecklistItem(
                id="alpha_positive",
                session_ref="Benchmark Outperformance (Alpha)",
                label="Positive Jensen's alpha (> 0)",
                status="pass",
                detail=f"Alpha is +{profile.alpha:.2f} pp vs benchmark proxy",
            )
        )

    # 7. Debt YTM credit-risk outlier check
    ytm_outlier_pp = float(set2.get("ytm_outlier_pp", 1.5))
    if profile.bucket == Bucket.DEBT:
        if profile.ytm is None:
            items.append(
                ChecklistItem(
                    id="ytm_credit_risk",
                    session_ref="Debt Yield & Credit Profile",
                    label="YTM credit-risk check",
                    status="no_data",
                    detail="YTM not available",
                )
            )
        elif peer_median_ytm is not None and profile.ytm > peer_median_ytm + ytm_outlier_pp:
            items.append(
                ChecklistItem(
                    id="ytm_credit_risk",
                    session_ref="Debt Yield & Credit Profile",
                    label="YTM credit-risk check",
                    status="flag",
                    detail=f"YTM {profile.ytm:.2f}% is > {ytm_outlier_pp:.1f} pp above peer median ({peer_median_ytm:.2f}%) — check credit risk",
                )
            )
        else:
            items.append(
                ChecklistItem(
                    id="ytm_credit_risk",
                    session_ref="Debt Yield & Credit Profile",
                    label="YTM credit-risk check",
                    status="pass",
                    detail=f"YTM {profile.ytm:.2f}% is within normal range",
                )
            )

    # 8. Minimum holding period info for Mid Cap and Small Cap
    horizons: dict[str, float] = set2.get("category_min_horizons", {})
    if profile.category in ("Small Cap", "Mid Cap"):
        min_h = horizons.get(profile.category, 10.0 if profile.category == "Small Cap" else 7.0)
        items.append(
            ChecklistItem(
                id="min_holding_period",
                session_ref="Category Holding Horizon",
                label=f"{profile.category} minimum holding horizon",
                status="info",
                detail=f"{profile.category} funds require at least a {min_h:.0f}-year investment horizon.",
            )
        )

    # 9. ELSS lock-in & Dynamic Asset Allocation / Balanced Advantage notes
    if profile.category == "ELSS":
        items.append(
            ChecklistItem(
                id="elss_lock_in",
                session_ref="Statutory Lock-In Rules",
                label="ELSS 3-year statutory lock-in",
                status="info",
                detail="Every ELSS lump-sum or SIP instalment is locked in for 3 years.",
            )
        )
    if profile.category in ("Balanced Advantage", "Dynamic Asset Allocation"):
        items.append(
            ChecklistItem(
                id="daa_low_risk_warning",
                session_ref="Hybrid Equity Risk",
                label="Dynamic Asset Allocation equity risk",
                status="info",
                detail="Dynamic Asset Allocation funds still carry equity volatility and are unsuitable for strictly low-risk investors.",
            )
        )

    return items


def compare_funds(
    a: FundProfile,
    b: FundProfile,
    *,
    peer_median_ytm: float | None = None,
) -> ComparisonResult:
    """Deterministically compare two FundProfiles and return a ComparisonResult."""
    s = get_settings()
    weights_cfg = s.compare_weights
    same_category = a.category.strip().lower() == b.category.strip().lower()

    warnings: list[str] = []
    if not same_category:
        warnings.append(
            f"Notes S7: compare within the same category ({a.category} vs {b.category}) — "
            "category-dependent return and TER criteria are skipped; ranking uses only "
            "category-neutral risk-adjusted and structural criteria."
        )

    draft_rows: list[tuple[CriterionSpec, CriterionRow, float, float]] = []
    for crit in CRITERIA:
        raw_w = float(weights_cfg.get(crit.id, crit.default_weight))
        val_a, fid_a = _extract_value(a, crit.id)
        val_b, fid_b = _extract_value(b, crit.id)

        if not _criterion_applies(crit, a, b, same_category):
            row = CriterionRow(
                id=crit.id,
                session_ref=crit.session_ref,
                label=crit.label,
                direction=crit.direction,
                category_neutral=crit.category_neutral,
                unit=crit.unit,
                raw_weight=raw_w,
                value_a=val_a,
                value_b=val_b,
                status="not_applicable",
                fact_id_a=fid_a,
                fact_id_b=fid_b,
            )
            draft_rows.append((crit, row, 0.0, 0.0))
            continue

        if val_a is None or val_b is None or val_a == "" or val_b == "":
            row = CriterionRow(
                id=crit.id,
                session_ref=crit.session_ref,
                label=crit.label,
                direction=crit.direction,
                category_neutral=crit.category_neutral,
                unit=crit.unit,
                raw_weight=raw_w,
                value_a=val_a,
                value_b=val_b,
                status="no_data",
                fact_id_a=fid_a,
                fact_id_b=fid_b,
            )
            draft_rows.append((crit, row, 0.0, 0.0))
            continue

        sa, sb, win = _score_pair(crit.direction, val_a, val_b)
        row = CriterionRow(
            id=crit.id,
            session_ref=crit.session_ref,
            label=crit.label,
            direction=crit.direction,
            category_neutral=crit.category_neutral,
            unit=crit.unit,
            raw_weight=raw_w,
            value_a=val_a,
            value_b=val_b,
            winner=win,
            status="scored",
            fact_id_a=fid_a,
            fact_id_b=fid_b,
        )
        draft_rows.append((crit, row, sa, sb))

    total_scored_weight = sum(r.raw_weight for _, r, _, _ in draft_rows if r.status == "scored")
    total_a = 0.0
    total_b = 0.0
    final_rows: list[CriterionRow] = []

    for _, row, sa, sb in draft_rows:
        if row.status == "scored" and total_scored_weight > 0:
            eff_w = (row.raw_weight / total_scored_weight) * 100.0
            pa = eff_w * sa
            pb = eff_w * sb
            row.effective_weight = round(eff_w, 2)
            row.points_a = round(pa, 2)
            row.points_b = round(pb, 2)
            total_a += pa
            total_b += pb
        final_rows.append(row)

    if total_scored_weight == 0:
        score_a = 50.0
        score_b = 50.0
    else:
        score_a = round(total_a, 2)
        score_b = round(100.0 - score_a, 2)

    if abs(score_a - score_b) < 0.5 or a.code == b.code:
        score_a = 50.0 if a.code == b.code else score_a
        score_b = 50.0 if a.code == b.code else score_b
        winner: Literal["A", "B", "tie"] = "tie"
        winner_code = None
        winner_name = None
        ranking = sorted([a.code, b.code])
    elif score_a > score_b:
        winner = "A"
        winner_code = a.code
        winner_name = a.name
        ranking = [a.code, b.code]
    else:
        winner = "B"
        winner_code = b.code
        winner_name = b.name
        ranking = [b.code, a.code]

    # Determine peer median YTM if both are debt and peer_median_ytm wasn't passed
    eff_median_ytm = peer_median_ytm
    if eff_median_ytm is None and a.ytm is not None and b.ytm is not None:
        eff_median_ytm = min(a.ytm, b.ytm)

    checklist = {
        a.code: build_checklist(a, peer_median_ytm=eff_median_ytm),
        b.code: build_checklist(b, peer_median_ytm=eff_median_ytm),
    }

    as_of = max(a.as_of, b.as_of)
    return ComparisonResult(
        fund_a=a,
        fund_b=b,
        same_category=same_category,
        warnings=warnings,
        criteria=final_rows,
        checklist=checklist,
        scores={a.code: score_a, b.code: score_b},
        winner=winner,
        winner_code=winner_code,
        winner_name=winner_name,
        ranking=ranking,
        not_evaluated=list(NOT_EVALUATED),
        as_of=as_of,
    )
