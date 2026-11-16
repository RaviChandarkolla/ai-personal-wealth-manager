"""M14: Derive financial metrics from parsed bank/CAS statements and create a new profile version.

Derives:
- monthly_income_inr
- monthly_expenses_inr
- savings_rate (fraction in [0, 1])
- income_stability (STABLE vs VARIABLE)
- emergency_months
- existing_sips_monthly_inr & active_sips list
- hhi (portfolio concentration from CAS holdings)

Feeds derived figures into deterministic `compute_risk_score` and creates a new
immutable `UserFinancialProfile` version (`v+1`) in `ProfileStore`.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from wealth_advisor.agents.profiling import ProfileAssumptions
from wealth_advisor.audit.log import AuditLog, ProfileStore
from wealth_advisor.config import get_settings
from wealth_advisor.domain.allocation import allocation_band
from wealth_advisor.domain.risk import RiskInputs, compute_risk_score
from wealth_advisor.ingestion.categorizer import CategorizedTransaction
from wealth_advisor.ingestion.parsers import (
    ActiveSIP,
    ParsedBankStatement,
    ParsedCASStatement,
)
from wealth_advisor.models.core import Goal, UserFinancialProfile
from wealth_advisor.models.enums import IncomeStability, RiskPreference


@dataclass(frozen=True)
class DerivedFinancialSummary:
    months_observed: int
    monthly_income_inr: float
    monthly_expenses_inr: float
    savings_rate: float
    income_stability: IncomeStability
    emergency_months: float
    existing_sips_monthly_inr: float
    existing_holdings_value_inr: float
    hhi: float | None
    active_sips: tuple[ActiveSIP, ...] = ()


_INCOME_CATEGORIES = frozenset({"salary", "other_income"})
_EXPENSE_CATEGORIES = frozenset({"emi", "rent", "utilities", "discretionary", "ambiguous"})
_LIQUID_CATEGORIES = frozenset({"liquid", "overnight", "ultra short", "money market", "bank fd", "arbitrage"})


def derive_financial_summary(
    bank_statement: ParsedBankStatement | None,
    categorized_txns: list[CategorizedTransaction] | tuple[CategorizedTransaction, ...],
    cas_statement: ParsedCASStatement | None = None,
) -> DerivedFinancialSummary:
    """Derive income, expenses, savings rate, stability, emergency months, and SIPs."""
    s = get_settings()

    months_seen: set[tuple[int, int]] = set()
    monthly_salary: dict[tuple[int, int], float] = defaultdict(float)
    total_income = 0.0
    total_expenses = 0.0
    total_bank_sip = 0.0

    for item in categorized_txns:
        ym = (item.txn.txn_date.year, item.txn.txn_date.month)
        months_seen.add(ym)
        amt = abs(item.txn.amount_inr)

        if item.category in _INCOME_CATEGORIES or item.txn.txn_type == "credit":
            if item.category != "transfer":
                total_income += amt
                if item.category == "salary":
                    monthly_salary[ym] += amt
        elif item.category == "sip":
            total_bank_sip += amt
        elif item.category in _EXPENSE_CATEGORIES:
            total_expenses += amt

    num_months = max(1, len(months_seen))
    monthly_income = round(total_income / num_months, 2)
    monthly_expenses = round(total_expenses / num_months, 2)

    if monthly_income > 0:
        raw_savings_rate = (monthly_income - monthly_expenses) / monthly_income
        savings_rate = round(max(0.0, min(1.0, raw_savings_rate)), 4)
    else:
        savings_rate = s.default_savings_rate

    # Determine income stability from salary credits across observed months
    salary_values = [monthly_salary[m] for m in sorted(months_seen) if monthly_salary[m] > 0]
    if not salary_values:
        income_stability = IncomeStability.VARIABLE
    elif len(salary_values) < max(1, len(months_seen) - 1):
        # Missing salary in more than 1 observed month
        income_stability = IncomeStability.VARIABLE
    elif len(salary_values) >= 2:
        mean_sal = statistics.mean(salary_values)
        stdev_sal = statistics.pstdev(salary_values)
        cov = (stdev_sal / mean_sal) if mean_sal > 0 else 1.0
        income_stability = IncomeStability.STABLE if cov <= 0.20 else IncomeStability.VARIABLE
    else:
        income_stability = IncomeStability.STABLE

    # CAS holdings & active SIPs
    holdings_total = 0.0
    liquid_holdings = 0.0
    hhi: float | None = None
    active_sips: tuple[ActiveSIP, ...] = ()

    if cas_statement is not None:
        holdings_total = sum(h.market_value_inr for h in cas_statement.holdings if h.market_value_inr > 0)
        liquid_holdings = sum(
            h.market_value_inr
            for h in cas_statement.holdings
            if h.market_value_inr > 0 and any(k in h.category.lower() for k in _LIQUID_CATEGORIES)
        )
        if holdings_total > 0:
            raw_hhi = sum((h.market_value_inr / holdings_total) ** 2 for h in cas_statement.holdings if h.market_value_inr > 0)
            hhi = round(min(1.0, max(0.0, raw_hhi)), 4)
        active_sips = tuple(sip for sip in cas_statement.active_sips if sip.active)

    if active_sips:
        existing_sips_monthly = round(
            sum(
                sip.monthly_amount_inr
                if sip.frequency == "monthly"
                else (sip.monthly_amount_inr / 3.0 if sip.frequency == "quarterly" else sip.monthly_amount_inr * 4.33)
                for sip in active_sips
            ),
            2,
        )
    else:
        existing_sips_monthly = round(total_bank_sip / num_months, 2)

    closing_bal = (
        bank_statement.closing_balance_inr
        if (bank_statement is not None and bank_statement.closing_balance_inr is not None)
        else 0.0
    )
    emergency_pool = max(0.0, closing_bal) + liquid_holdings
    if monthly_expenses > 0:
        emergency_months = round(emergency_pool / monthly_expenses, 2)
    elif closing_bal > 0:
        emergency_months = s.default_emergency_months
    else:
        emergency_months = s.default_emergency_months

    return DerivedFinancialSummary(
        months_observed=num_months,
        monthly_income_inr=monthly_income,
        monthly_expenses_inr=monthly_expenses,
        savings_rate=savings_rate,
        income_stability=income_stability,
        emergency_months=emergency_months,
        existing_sips_monthly_inr=existing_sips_monthly,
        existing_holdings_value_inr=round(holdings_total, 2),
        hhi=hhi,
        active_sips=active_sips,
    )


def update_profile_from_ingestion(
    *,
    summary: DerivedFinancialSummary,
    previous_profile: UserFinancialProfile | None = None,
    goal: Goal | None = None,
    profile_id: str | None = None,
    profile_store: ProfileStore | None = None,
    audit_log: AuditLog | None = None,
    run_id: str = "ingestion_run",
) -> tuple[UserFinancialProfile, ProfileAssumptions]:
    """Feed derived statement figures into `compute_risk_score` and save a new profile version."""
    if goal is None:
        if previous_profile is not None and "goal" in previous_profile.inputs:
            goal = Goal.model_validate(previous_profile.inputs["goal"])
        else:
            goal = Goal(
                amount=max(100_000.0, summary.monthly_income_inr * 6 or 100_000.0),
                horizon_years=5.0,
                risk_preference=RiskPreference.MODERATE,
                purpose="wealth creation",
            )

    resolved_profile_id = (
        profile_id
        or (previous_profile.profile_id if previous_profile is not None else None)
        or "ingested_user"
    )

    if previous_profile is None and profile_store is not None:
        previous_profile = profile_store.latest(resolved_profile_id)

    next_version = (previous_profile.version + 1) if previous_profile is not None else 1

    risk_inputs = RiskInputs(
        horizon_years=goal.horizon_years,
        risk_preference=goal.risk_preference,
        savings_rate=summary.savings_rate,
        income_stability=summary.income_stability,
        emergency_months=summary.emergency_months,
        hhi=summary.hhi,
    )
    risk_res = compute_risk_score(risk_inputs)
    band = allocation_band(risk_res.category, goal.horizon_years, summary.emergency_months)

    assumptions = ProfileAssumptions(
        savings_rate=summary.savings_rate,
        income_stability=summary.income_stability,
        emergency_months=summary.emergency_months,
        hhi=summary.hhi,
    )

    derived_payload: dict[str, Any] = {
        "months_observed": summary.months_observed,
        "monthly_income_inr": summary.monthly_income_inr,
        "monthly_expenses_inr": summary.monthly_expenses_inr,
        "savings_rate": summary.savings_rate,
        "income_stability": summary.income_stability.value,
        "emergency_months": summary.emergency_months,
        "existing_sips_monthly_inr": summary.existing_sips_monthly_inr,
        "existing_holdings_value_inr": summary.existing_holdings_value_inr,
        "hhi": summary.hhi,
        "active_sips": [
            {
                "sip_id": sip.sip_id,
                "instrument_id": sip.instrument_id,
                "scheme_name": sip.scheme_name,
                "monthly_amount_inr": sip.monthly_amount_inr,
                "frequency": sip.frequency,
            }
            for sip in summary.active_sips
        ],
    }

    new_profile = UserFinancialProfile(
        profile_id=resolved_profile_id,
        version=next_version,
        inputs={
            "goal": goal.model_dump(mode="json"),
            "risk_inputs": risk_inputs.model_dump(mode="json"),
            "assumed_defaults": [],
            "risk_factors": risk_res.factors,
            "derived_from_statements": derived_payload,
        },
        risk_score=risk_res.score,
        category=risk_res.category,
        allocation_band=band,
    )

    if profile_store is not None:
        profile_store.save(new_profile)

    if audit_log is not None:
        audit_log.append(
            run_id=run_id,
            agent="ingestion_profile_updater",
            step="profile_version_created",
            payload={
                "profile_id": new_profile.profile_id,
                "version": new_profile.version,
                "risk_score": new_profile.risk_score,
                "category": new_profile.category.value,
                "derived_from_statements": derived_payload,
            },
        )

    return new_profile, assumptions
