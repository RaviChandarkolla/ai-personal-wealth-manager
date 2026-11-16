"""M3 Profiling Agent: natural language -> validated Goal + UserFinancialProfile v1.

The LLM only extracts raw phrases and maps risk wording to the fixed taxonomy; amounts and
horizons are converted by code, and the risk score is computed by a deterministic tool.
"""

import re
import uuid

from pydantic import BaseModel

from ..config import get_settings
from ..domain.allocation import allocation_band
from ..domain.portfolio_spec import build_spec, describe_spec
from ..domain.risk import RiskInputs, compute_risk_score
from ..guardrails.input import GuardResult, check_input
from ..llm.base import LLMClient
from ..models.core import Goal, PortfolioSpec, UserFinancialProfile
from ..models.enums import IncomeStability, RiskCategory, RiskPreference

PROMPT_VERSION = "profiling.v1"

SYSTEM_PROMPT = """You extract an investment goal from the user's message. Do NOT do arithmetic.
- amount_text: the amount exactly as written (e.g. "50,000", "2 lakh", "1.5 crore"), else null.
- horizon_text: the time horizon exactly as written (e.g. "5 years", "3 saal", "18 months"), else null.
- risk_preference: map to exactly one of: capital_preservation, conservative, moderate, growth,
  aggressive. "low risk"/"safe" -> conservative; "no risk"/"can't lose money" ->
  capital_preservation; "medium"/"balanced" -> moderate; "high growth" -> growth;
  "high risk"/"max returns" -> aggressive. If the user gave no risk wording, use null.
- purpose: short free text (e.g. "house down payment"), else "".
- excluded_categories: fund categories the user wants to avoid (e.g. "mid cap"), else [].
- fund_requests: one entry per kind of mutual fund the user wants, with the number of funds as
  `count`. Copy the category wording the user used (e.g. "liquid", "debt", "flexi cap",
  "small cap"). "2 flexi and 2 small cap" -> two entries with count 2. Use [] when the user
  did not ask for specific funds.
- stock_count: how many direct stocks the user wants, else null.
- trendy_stock_count: how many of those stocks should be trending / in the news / momentum
  picks. If all of them should be, repeat stock_count. Else null.
- include_gold: true only if the user asks for gold, else null.
Never invent values the user did not state."""

_FIELD_QUESTIONS = {
    "amount": "How much do you want to invest (e.g. 50,000 or 2 lakh)?",
    "horizon": "For how long do you plan to stay invested (e.g. 5 years)?",
    "risk": "How much risk are you comfortable with (low / moderate / high)?",
}

_WORD_NUMBERS = {
    w: i
    for i, w in enumerate(
        ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]
    )
}
_AMOUNT_UNITS = {
    "crore": 1e7, "crores": 1e7, "cr": 1e7,
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "l": 1e5,
    "k": 1e3, "thousand": 1e3, "hazar": 1e3, "hazaar": 1e3,
    "million": 1e6, "m": 1e6,
}
_YEAR_UNITS = {"y", "yr", "yrs", "year", "years", "saal", "sal", "varsh"}
_MONTH_UNITS = {"m", "mo", "mos", "month", "months", "mahine", "mahina", "maah"}


class RawFundRequest(BaseModel):
    category: str
    count: int = 1


class ExtractedGoal(BaseModel):
    amount_text: str | None = None
    horizon_text: str | None = None
    risk_preference: RiskPreference | None = None
    purpose: str = ""
    excluded_categories: list[str] = []
    fund_requests: list[RawFundRequest] = []
    stock_count: int | None = None
    trendy_stock_count: int | None = None
    include_gold: bool | None = None


def spec_from_extracted(got: ExtractedGoal | None) -> PortfolioSpec | None:
    if got is None:
        return None
    return build_spec(
        [(r.category, r.count) for r in got.fund_requests],
        got.stock_count,
        got.trendy_stock_count,
        got.include_gold,
    )


class ProfilingResult(BaseModel):
    goal: Goal | None = None
    missing: list[str] = []
    rejected: GuardResult | None = None


class ProfileAssumptions(BaseModel):
    savings_rate: float
    income_stability: IncomeStability = IncomeStability.STABLE
    emergency_months: float
    hhi: float | None = None


def _to_number(token: str) -> float | None:
    token = token.replace(",", "").strip().lower()
    if token in _WORD_NUMBERS:
        return float(_WORD_NUMBERS[token])
    try:
        return float(token)
    except ValueError:
        return None


_NUM = r"(\d[\d,]*(?:\.\d+)?|\b(?:" + "|".join(_WORD_NUMBERS) + r")\b)"


def parse_amount(text: str | None) -> float | None:
    """'₹50,000' -> 50000; '2.5 lakh' -> 250000; '1 crore' -> 1e7."""
    if not text:
        return None
    m = re.search(_NUM + r"\s*([a-zA-Z]+)?", text.lower())
    if not m:
        return None
    value = _to_number(m.group(1))
    if value is None:
        return None
    unit = (m.group(2) or "").lower()
    if unit and unit not in _AMOUNT_UNITS:
        unit = ""  # e.g. "rupees", "inr"
    return value * _AMOUNT_UNITS.get(unit, 1.0)


def parse_horizon(text: str | None) -> float | None:
    """'5 years' -> 5; '3 saal' -> 3; '18 months' -> 1.5; 'five yrs' -> 5."""
    if not text:
        return None
    m = re.search(_NUM + r"\s*[- ]?\s*([a-zA-Z]+)", text.lower())
    if not m:
        return None
    value = _to_number(m.group(1))
    unit = m.group(2).lower()
    if value is None:
        return None
    if unit in _YEAR_UNITS:
        return value
    if unit in _MONTH_UNITS:
        return value / 12
    return None


def risk_score_tool(inputs: dict) -> dict:
    """Tool exposed to agents: the only way a risk score is produced."""
    return compute_risk_score(RiskInputs(**inputs)).model_dump(mode="json")


class ProfilingAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def extract(self, text: str) -> ProfilingResult:
        guard = check_input(text)
        if not guard.allowed:
            return ProfilingResult(rejected=guard)
        from ..llm.langchain_client import trace_context

        with trace_context(step="profiling", metadata={"prompt_version": PROMPT_VERSION}):
            got = self._llm.complete(
                [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
                ExtractedGoal,
            )
        amount = parse_amount(got.amount_text)
        horizon = parse_horizon(got.horizon_text)
        missing = [
            name
            for name, val in (("amount", amount), ("horizon", horizon), ("risk", got.risk_preference))
            if val is None or (isinstance(val, float) and val <= 0)
        ]
        if missing:
            return ProfilingResult(missing=missing)
        assert amount is not None and horizon is not None and got.risk_preference is not None
        goal = Goal(
            amount=amount,
            horizon_years=horizon,
            risk_preference=got.risk_preference,
            purpose=got.purpose,
            excluded_categories=got.excluded_categories,
            spec=spec_from_extracted(got),
        )
        return ProfilingResult(goal=goal)

    def resolve(
        self,
        query: str | None = None,
        *,
        amount: float | None = None,
        horizon_years: float | None = None,
        risk_preference: RiskPreference | None = None,
        purpose: str | None = None,
        excluded_categories: list[str] | None = None,
        spec: PortfolioSpec | None = None,
    ) -> ProfilingResult:
        """Resolve a Goal from structured inputs, a natural-language query, or a hybrid of both.

        - If all mandatory fields (amount, horizon_years, risk_preference) are supplied
          and `query` is empty, bypasses the LLM extraction call completely.
        - If `query` is supplied alongside structured fields, runs input guardrails and
          LLM extraction on `query` to capture `purpose`, `excluded_categories`, or any
          unfilled mandatory fields, with explicit structured values taking precedence.
        """
        clean_query = (query or "").strip()
        clean_purpose = (purpose or "").strip()

        # Run input guardrails on any free-text fields provided
        for text_input in (clean_query, clean_purpose):
            if text_input:
                guard = check_input(text_input)
                if not guard.allowed:
                    return ProfilingResult(rejected=guard)

        extracted: ExtractedGoal | None = None
        if clean_query:
            from ..llm.langchain_client import trace_context

            with trace_context(step="profiling", metadata={"prompt_version": PROMPT_VERSION, "mode": "hybrid"}):
                extracted = self._llm.complete(
                    [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": clean_query}],
                    ExtractedGoal,
                )

        final_amount = (
            amount
            if (amount is not None and amount > 0)
            else (parse_amount(extracted.amount_text) if extracted else None)
        )
        final_horizon = (
            horizon_years
            if (horizon_years is not None and horizon_years > 0)
            else (parse_horizon(extracted.horizon_text) if extracted else None)
        )
        final_risk = risk_preference or (extracted.risk_preference if extracted else None)
        final_purpose = clean_purpose or (extracted.purpose if extracted else "")

        merged_excluded: list[str] = []
        for cat in [*(excluded_categories or []), *(extracted.excluded_categories if extracted else [])]:
            c = cat.strip()
            if c and c not in merged_excluded:
                merged_excluded.append(c)

        missing = [
            name
            for name, val in (("amount", final_amount), ("horizon", final_horizon), ("risk", final_risk))
            if val is None or (isinstance(val, float) and val <= 0)
        ]
        if missing:
            return ProfilingResult(missing=missing)

        assert final_amount is not None and final_horizon is not None and final_risk is not None
        return ProfilingResult(
            goal=Goal(
                amount=final_amount,
                horizon_years=final_horizon,
                risk_preference=final_risk,
                purpose=final_purpose,
                excluded_categories=merged_excluded,
                spec=spec if spec is not None else spec_from_extracted(extracted),
            )
        )

    @staticmethod
    def follow_up_question(field: str) -> str:
        return _FIELD_QUESTIONS[field]

    @staticmethod
    def restate(goal: Goal) -> str:
        years = goal.horizon_years
        horizon = f"{years:g} years" if years >= 1 else f"{round(years * 12)} months"
        text = (
            f"Invest Rs {goal.amount:,.0f} for {horizon} with a "
            f"{goal.risk_preference.value.replace('_', ' ')} risk preference"
        )
        if goal.purpose:
            text += f" (purpose: {goal.purpose})"
        if goal.excluded_categories:
            text += f", excluding {', '.join(goal.excluded_categories)}"
        wanted = describe_spec(goal.spec)
        if wanted:
            text += f", holding {', '.join(wanted)}"
        return text + "."

    @staticmethod
    def build_profile(
        goal: Goal,
        assumptions: ProfileAssumptions | None = None,
        profile_id: str | None = None,
        version: int = 1,
    ) -> tuple[UserFinancialProfile, ProfileAssumptions]:
        s = get_settings()
        a = assumptions or ProfileAssumptions(
            savings_rate=s.default_savings_rate, emergency_months=s.default_emergency_months
        )
        risk_inputs = {
            "horizon_years": goal.horizon_years,
            "risk_preference": goal.risk_preference.value,
            "savings_rate": a.savings_rate,
            "income_stability": a.income_stability.value,
            "emergency_months": a.emergency_months,
            "hhi": a.hhi,
        }
        result = risk_score_tool(risk_inputs)
        category = RiskCategory(result["category"])
        band = allocation_band(category, goal.horizon_years, a.emergency_months)
        profile = UserFinancialProfile(
            profile_id=profile_id or uuid.uuid4().hex[:12],
            version=version,
            inputs={
                "goal": goal.model_dump(mode="json"),
                "risk_inputs": risk_inputs,
                "assumed_defaults": ["savings_rate", "income_stability", "emergency_months"],
                "risk_factors": result["factors"],
            },
            risk_score=result["score"],
            category=category,
            allocation_band=band,
        )
        return profile, a
