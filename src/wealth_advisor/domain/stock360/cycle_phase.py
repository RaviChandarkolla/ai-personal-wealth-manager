"""Section 16: institutional market-cycle phase (rule-based)."""

from __future__ import annotations


def pct_rank(value: float | None, history: list[float]) -> float | None:
    vals = [h for h in history if h is not None]
    if value is None or len(vals) < 2:
        return None
    return sum(1 for h in vals if h <= value) / len(vals) * 100


def cycle_phase(
    stage: str | None, pe_percentile: float | None, ret_from_3y_low_pct: float | None,
    inst_minus_public_delta: float | None = None,
) -> dict[str, str]:
    """Phase 1 Accumulation, 2 Markup, 3 Distribution, 4 Markdown.

    Inputs: Weinstein stage, P/E percentile vs own history, 1Y return from the low, and the
    change in (FII+DII) minus public holding (None when shareholding is unknown -> low confidence)."""
    if stage is None:
        return {"phase": "Not available", "sub_stage": "", "confidence": "low",
                "reason": "No price history for Weinstein stage"}
    rich = pe_percentile is not None and pe_percentile >= 80
    cheap = pe_percentile is not None and pe_percentile <= 40
    if stage == "Stage 4":
        phase, sub = "Phase 4: Markdown", "decline"
    elif stage == "Stage 3" or (stage == "Stage 2" and rich and inst_minus_public_delta is not None
                                and inst_minus_public_delta < 0):
        phase, sub = "Phase 3: Distribution", "late" if rich else "early"
    elif stage == "Stage 2":
        phase, sub = "Phase 2: Markup", "late" if rich else "early" if (
            ret_from_3y_low_pct is not None and ret_from_3y_low_pct < 40) else "mid"
    else:
        phase, sub = "Phase 1: Accumulation", "late" if (inst_minus_public_delta or 0) > 0 else "early"
    known = sum(x is not None for x in (pe_percentile, inst_minus_public_delta))
    conf = "low" if inst_minus_public_delta is None else "high" if known == 2 else "medium"
    reason = f"{stage}; P/E percentile {'n/a' if pe_percentile is None else round(pe_percentile)}"
    if inst_minus_public_delta is None:
        reason += "; shareholding unknown"
    if cheap and phase.startswith("Phase 3"):
        reason += "; note valuation is not rich"
    return {"phase": phase, "sub_stage": sub, "confidence": conf, "reason": reason}
