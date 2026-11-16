"""Quant-only verdict: 0-100 score, stars, action band, legend sell signals.

This is the only thing Advise runs for the optional veto (no LLM, no web)."""

from __future__ import annotations

from dataclasses import dataclass

from .checklists import (
    TriggerContext,
    consensus_action,
    evaluate_triggers,
    legend_verdicts,
    sell_signal_count,
)
from .models import MechanicalVerdict

ACTIONS = ["SELL", "AVOID", "REDUCE", "HOLD", "ACCUMULATE", "BUY", "STRONG BUY"]
WEIGHTS = {"quality": 30, "valuation": 25, "growth": 20, "balance": 15, "technical": 10}


@dataclass
class MechInputs:
    roe_pct: float | None = None
    roce_pct: float | None = None
    roe_years_above_15: bool | None = None
    cash_conversion: float | None = None
    other_income_pct_pbt: float | None = None
    premium_to_iv_pct: float | None = None  # CMP vs composite IV
    peg: float | None = None
    rev_cagr_pct: float | None = None
    eps_cagr_pct: float | None = None
    debt_to_equity: float | None = None
    net_debt_to_ebitda: float | None = None
    interest_coverage: float | None = None
    weinstein_stage: str | None = None
    above_200dma: bool | None = None
    rsi: float | None = None
    is_bank: bool = False


def _frac(items: list[float | None]) -> float | None:
    vals = [v for v in items if v is not None]
    return sum(vals) / len(vals) if vals else None


def _b(cond: bool | None) -> float | None:
    return None if cond is None else 1.0 if cond else 0.0


def _lin(v: float | None, lo: float, hi: float) -> float | None:
    """0 at lo, 1 at hi (clamped)."""
    return None if v is None else min(max((v - lo) / (hi - lo), 0.0), 1.0)


def pillar_scores(i: MechInputs) -> dict[str, float | None]:
    return {
        "quality": _frac([_lin(i.roe_pct, 5, 25), _lin(i.roce_pct, 5, 25), _b(i.roe_years_above_15),
                          _lin(i.cash_conversion, 0.3, 1.0),
                          None if i.other_income_pct_pbt is None else 1 - _lin(i.other_income_pct_pbt, 10, 50)]),  # type: ignore[operator]
        "valuation": _frac([None if i.premium_to_iv_pct is None else 1 - _lin(i.premium_to_iv_pct, -30, 60),  # type: ignore[operator]
                            None if i.peg is None else 1 - _lin(i.peg, 0.8, 2.5)]),  # type: ignore[operator]
        "growth": _frac([_lin(i.rev_cagr_pct, 0, 20), _lin(i.eps_cagr_pct, 0, 25)]),
        "balance": _frac([
            None if i.is_bank or i.debt_to_equity is None else 1 - _lin(i.debt_to_equity, 0.2, 1.5),  # type: ignore[operator]
            None if i.is_bank or i.net_debt_to_ebitda is None else 1 - _lin(i.net_debt_to_ebitda, 0, 4),  # type: ignore[operator]
            _lin(i.interest_coverage, 1.5, 8)]),
        "technical": _frac([
            None if i.weinstein_stage is None else {"Stage 2": 1.0, "Stage 1": 0.5, "Stage 3": 0.3, "Stage 4": 0.0}[i.weinstein_stage],
            _b(i.above_200dma),
            None if i.rsi is None else 1.0 if 40 <= i.rsi <= 70 else 0.4]),
    }


def action_for(score: float, sell_signals: int) -> str:
    if sell_signals >= 5:
        return "SELL"
    return ("STRONG BUY" if score >= 80 else "BUY" if score >= 65 else "ACCUMULATE" if score >= 50
            else "HOLD" if score >= 40 else "REDUCE" if score >= 30 else "AVOID")


def build_mechanical(inputs: MechInputs, ctx: TriggerContext | None = None) -> MechanicalVerdict:
    """Missing pillars are excluded and the score re-normalised over the pillars that exist."""
    ps = pillar_scores(inputs)
    avail = {k: v for k, v in ps.items() if v is not None}
    total_w = sum(WEIGHTS[k] for k in avail)
    score = sum(v * WEIGHTS[k] for k, v in avail.items()) / total_w * 100 if total_w else 0.0
    triggers = evaluate_triggers(ctx) if ctx else []
    verdicts = legend_verdicts(triggers) if ctx else {}
    signals, evaluable = sell_signal_count(verdicts) if ctx else (0, 0)
    action = action_for(score, signals) if total_w else "HOLD"
    return MechanicalVerdict(
        score=round(score, 1), stars=round(score / 20, 1), action=action,
        sell_signals=signals, evaluable_legends=evaluable, legend_verdicts=verdicts, triggers=triggers,
    )


def should_veto(v: MechanicalVerdict, min_signals: int = 5) -> tuple[bool, str]:
    if v.action in ("AVOID", "SELL"):
        return True, f"mechanical verdict {v.action}"
    if v.sell_signals >= min_signals:
        return True, f"{v.sell_signals}/{v.evaluable_legends} legend sell signals"
    return False, ""


def clamp_action(llm_action: str, mechanical_action: str) -> tuple[str, str]:
    """LLM action may differ from the mechanical one by at most one band."""
    try:
        a, m = ACTIONS.index(llm_action.upper()), ACTIONS.index(mechanical_action)
    except ValueError:
        return mechanical_action, f"unrecognised action '{llm_action}'; mechanical action kept"
    if abs(a - m) <= 1:
        return ACTIONS[a], ""
    c = m + (1 if a > m else -1)
    return ACTIONS[c], f"LLM action {llm_action} clamped to {ACTIONS[c]} (mechanical {mechanical_action})"


__all__ = [
    "MechInputs",
    "TriggerContext",
    "action_for",
    "build_mechanical",
    "clamp_action",
    "consensus_action",
    "should_veto",
]
