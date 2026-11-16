"""Deterministic risk scoring (1-10, higher = more risk capacity)."""

from pydantic import BaseModel, Field

from ..config import RiskWeights, Settings, get_settings
from ..models.enums import IncomeStability, RiskCategory, RiskPreference

_PREFERENCE_SCORE = {
    RiskPreference.CAPITAL_PRESERVATION: 2,
    RiskPreference.CONSERVATIVE: 4,
    RiskPreference.MODERATE: 6,
    RiskPreference.GROWTH: 8,
    RiskPreference.AGGRESSIVE: 10,
}


class RiskInputs(BaseModel):
    horizon_years: float = Field(gt=0)
    risk_preference: RiskPreference
    savings_rate: float = Field(ge=0, le=1)  # fraction of income
    income_stability: IncomeStability = IncomeStability.STABLE
    emergency_months: float = Field(ge=0)
    hhi: float | None = Field(default=None, ge=0, le=1)  # existing concentration; None = unknown


class RiskResult(BaseModel):
    score: float
    category: RiskCategory
    factors: dict[str, float]  # per-factor 1-10 score for explainability


def horizon_score(years: float) -> float:
    """<1y->1, 1-3->3, 3-5->5, 5-7->7, 7-10->8, >10->10. e.g. 5 -> 7."""
    if years < 1:
        return 1
    if years < 3:
        return 3
    if years < 5:
        return 5
    if years < 7:
        return 7
    if years <= 10:
        return 8
    return 10


def savings_score(rate: float, stability: IncomeStability) -> float:
    """Band score times stability multiplier (variable = 0.8), floor 1. e.g. 0.25 stable -> 8."""
    bands = [(0.05, 2), (0.10, 4), (0.20, 6), (0.30, 8)]
    base = next((s for limit, s in bands if rate < limit), 10)
    mult = 1.0 if stability == IncomeStability.STABLE else 0.8
    return max(1.0, base * mult)


def diversification_score(hhi: float | None) -> float:
    """Unknown -> neutral 5; else 1 + 9*(1-HHI). e.g. hhi 0.5 -> 5.5."""
    if hhi is None:
        return 5.0
    return 1 + 9 * (1 - hhi)


def liquidity_score(months: float) -> float:
    """<3 months->2, 3-6->5, 6-12->8, >=12->10. e.g. 4 -> 5."""
    if months < 3:
        return 2
    if months < 6:
        return 5
    if months < 12:
        return 8
    return 10


def category_for(score: float, s: Settings | None = None) -> RiskCategory:
    s = s or get_settings()
    if score < s.moderate_min:
        return RiskCategory.CONSERVATIVE
    if score < s.aggressive_min:
        return RiskCategory.MODERATE
    return RiskCategory.AGGRESSIVE


def compute_risk_score(inputs: RiskInputs, weights: RiskWeights | None = None) -> RiskResult:
    """Weighted sum of factor scores.

    Example: horizon 5y(7), moderate(6), savings 25% stable(8), unknown hhi(5), 6 months(8)
    -> 7*.30 + 6*.25 + 8*.20 + 5*.15 + 8*.10 = 6.75 -> Moderate.
    """
    w = weights or get_settings().risk_weights
    factors = {
        "horizon": horizon_score(inputs.horizon_years),
        "preference": _PREFERENCE_SCORE[inputs.risk_preference],
        "savings": savings_score(inputs.savings_rate, inputs.income_stability),
        "diversification": diversification_score(inputs.hhi),
        "liquidity": liquidity_score(inputs.emergency_months),
    }
    score = sum(factors[k] * getattr(w, k) for k in factors)
    score = round(min(10.0, max(1.0, score)), 4)
    return RiskResult(score=score, category=category_for(score), factors=factors)
