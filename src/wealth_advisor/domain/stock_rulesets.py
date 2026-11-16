"""Stock screening rule sets: Set 1 (Existing Heuristics) & Set 2 (FA Course Notes)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from wealth_advisor.data.stocks.provider import (
    StockFinancialYear,
    StockSectorMedian,
    StockSnapshot,
)
from wealth_advisor.domain.stock_ratios import StockComputedRatios, compute_stock_ratios
from wealth_advisor.domain.stock_valuation import (
    StockRelativeValuation,
    compute_relative_valuation,
)

STOCK_RULE_SET_META: dict[str, dict[str, str]] = {
    "set1": {
        "name": "set1",
        "label": "Set 1 — Existing Equity Heuristics",
        "source": "Original M13 Equity Heuristics (P/E, YoY Growth, D/E, Pledge, Insider)",
        "description": (
            "Screens direct equities using 5 core heuristic checks: P/E <= Sector P/E, "
            "YoY Earnings Growth > 0%, Debt/Equity <= 2.0, Promoter Pledge <= 25%, and "
            "Insider Net Buying signal."
        ),
    },
    "set2": {
        "name": "set2",
        "label": "Set 2 — FA Course Notes (CA Rachana Ranade)",
        "source": "Fundamental Analysis Course Notes (10-Pointer Non-Bank / 6-Pointer Bank)",
        "description": (
            "Screens non-banking stocks on the 10-pointer checklist (5Y Revenue/PAT/EPS CAGR, "
            "ROE/ROCE >= 15%, D/E <= 1.0, Interest Coverage >= 3.0, Current Ratio >= 1.5, "
            "CFO/PAT >= 0.80, Increasing Dividends, Optional Market Cap) and banks on the "
            "6-pointer checklist (NII CAGR, NIM >= 3%, GNPA <= 3% & NNPA <= 1%, CASA >= 40% & "
            "CAR >= 12%, ROE >= 12% & ROA >= 1%, Optional Market Cap), plus governance red flags."
        ),
    },
}


@dataclass
class StockCriterionResult:
    """Evaluation of a single screening criterion for a stock."""

    rule_id: str
    name: str
    category: str
    threshold_desc: str
    actual_value: float | bool | None
    actual_formatted: str
    status: str  # "passed" | "failed" | "missing" | "not_applied"
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StockScreenEvaluation:
    """Complete evaluation of a stock under Set 1 or Set 2."""

    symbol: str
    rule_set: str  # "set1" | "set2"
    is_bank: bool
    passed: bool
    passed_count: int
    evaluated_count: int
    total_criteria: int
    pass_ratio: float
    min_required_evaluated: int
    criteria: list[StockCriterionResult] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    sub_scores: dict[str, float] = field(default_factory=dict)
    composite_score: float = 0.0
    valuation_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "rule_set": self.rule_set,
            "is_bank": self.is_bank,
            "passed": self.passed,
            "passed_count": self.passed_count,
            "evaluated_count": self.evaluated_count,
            "total_criteria": self.total_criteria,
            "pass_ratio": round(self.pass_ratio, 4),
            "min_required_evaluated": self.min_required_evaluated,
            "criteria": [c.to_dict() for c in self.criteria],
            "red_flags": list(self.red_flags),
            "sub_scores": {k: round(v, 2) for k, v in self.sub_scores.items()},
            "composite_score": round(self.composite_score, 2),
            "valuation_summary": dict(self.valuation_summary),
        }


def _fmt_pct(val: float | None, *, is_decimal: bool = True) -> str:
    if val is None:
        return "N/A"
    pct = val * 100.0 if is_decimal else val
    return f"{pct:.1f}%"


def _fmt_num(val: float | None, decimals: int = 2, suffix: str = "") -> str:
    if val is None:
        return "N/A"
    return f"{val:.{decimals}f}{suffix}"


def compute_stock_sub_scores(
    ratios: StockComputedRatios,
    valuation: StockRelativeValuation,
    *,
    pass_ratio: float,
    red_flags: list[str],
    risk_profile: str = "Moderate",
    horizon_years: float = 5.0,
) -> tuple[dict[str, float], float]:
    """Compute the 5 sub-scores (0-100) and weighted composite score for a stock."""
    # 1. Fundamental score (30%): screen pass ratio (40%), ROE/ROCE (25%), 5Y PAT CAGR (20%), CFO/PAT (15%)
    screen_part = max(0.0, min(100.0, pass_ratio * 100.0))

    prof_metric = max(ratios.roe or 0.0, ratios.roce or 0.0)
    if ratios.is_bank and ratios.roe is not None:
        prof_part = max(0.0, min(100.0, (ratios.roe / 0.18) * 85.0))
    else:
        prof_part = max(0.0, min(100.0, (prof_metric / 0.25) * 85.0))

    growth_val = ratios.pat_cagr_5y if ratios.pat_cagr_5y is not None else (ratios.earnings_growth_yoy or 0.0)
    growth_part = max(0.0, min(100.0, (growth_val / 0.20) * 85.0))

    if ratios.is_bank:
        # For banks, use asset quality / NIM instead of CFO/PAT
        nnpa = ratios.nnpa_pct if ratios.nnpa_pct is not None else 1.0
        cfo_part = max(0.0, min(100.0, 100.0 - (nnpa * 35.0)))
    else:
        cfo_val = ratios.cfo_to_pat if ratios.cfo_to_pat is not None else 0.8
        cfo_part = max(0.0, min(100.0, (cfo_val / 1.10) * 85.0))

    fundamental = (
        0.40 * screen_part
        + 0.25 * prof_part
        + 0.20 * growth_part
        + 0.15 * cfo_part
    )
    if red_flags:
        fundamental = max(0.0, fundamental - 20.0 * len(red_flags))

    # 2. Fit score (25%): horizon compatibility (min 5Y) & volatility/drawdown alignment
    horizon_score = 90.0 if horizon_years >= 5.0 else max(20.0, (horizon_years / 5.0) * 70.0)
    vol = ratios.volatility_1y if ratios.volatility_1y is not None else 0.20
    dd = abs(ratios.max_drawdown_1y) if ratios.max_drawdown_1y is not None else 0.12

    target_vol = {"Conservative": 0.15, "Moderate": 0.22, "Aggressive": 0.30}.get(
        risk_profile, 0.22
    )
    vol_score = max(20.0, min(100.0, 85.0 - max(0.0, (vol - target_vol) * 250.0) - dd * 40.0))
    fit = 0.55 * horizon_score + 0.45 * vol_score

    # 3. News / Governance hygiene score (15%): baseline 75 adjusted for auditor flags & pledge
    news = 78.0
    if ratios.auditor_flags > 0:
        news -= 40.0 * ratios.auditor_flags
    pledge = ratios.pledge_pct or 0.0
    if pledge > 0:
        news -= min(35.0, pledge * 1.2)
    news = max(0.0, min(100.0, news))

    # 4. Sentiment & Valuation score (15%): relative valuation upside + insider buying + promoter holding
    val_score = 60.0
    if valuation.expected_return_low_pct is not None:
        # +10% upside -> ~75, 0% -> 60, -20% -> 35
        val_score = max(15.0, min(95.0, 60.0 + valuation.expected_return_low_pct * 1.2))
    insider_bonus = 0.0
    if ratios.insider_net_buy_lakhs > 0:
        insider_bonus = min(12.0, 4.0 + ratios.insider_net_buy_lakhs / 30.0)
    elif ratios.insider_net_buy_lakhs < 0:
        insider_bonus = max(-12.0, ratios.insider_net_buy_lakhs / 25.0)
    prom_bonus = 5.0 if (ratios.promoter_holding_pct or 0.0) >= 40.0 else 0.0
    sentiment = max(0.0, min(100.0, val_score + insider_bonus + prom_bonus))

    # 5. Liquidity & Cost score (15%): market cap + avg daily traded value
    mcap = ratios.market_cap_cr or 0.0
    adtv = ratios.avg_daily_traded_value_cr or 0.0
    mcap_score = 50.0
    if mcap >= 100_000:
        mcap_score = 92.0
    elif mcap >= 25_000:
        mcap_score = 82.0
    elif mcap >= 5_000:
        mcap_score = 70.0
    elif mcap > 0:
        mcap_score = 55.0

    adtv_score = 50.0
    if adtv >= 500:
        adtv_score = 92.0
    elif adtv >= 100:
        adtv_score = 82.0
    elif adtv >= 25:
        adtv_score = 70.0
    elif adtv > 0:
        adtv_score = 55.0
    liquidity_cost = 0.50 * mcap_score + 0.50 * adtv_score

    sub_scores = {
        "fundamental": round(fundamental, 2),
        "fit": round(fit, 2),
        "news": round(news, 2),
        "sentiment": round(sentiment, 2),
        "liquidity_cost": round(liquidity_cost, 2),
    }
    composite = (
        0.30 * fundamental
        + 0.25 * fit
        + 0.15 * news
        + 0.15 * sentiment
        + 0.15 * liquidity_cost
    )
    return sub_scores, round(composite, 2)


def _apply_overrides(
    snapshot: StockSnapshot,
    financials: list[StockFinancialYear],
    overrides: dict[str, float] | None,
) -> tuple[StockSnapshot, list[StockFinancialYear]]:
    if not overrides:
        return snapshot, financials
    snap_updates: dict[str, Any] = {}
    if "fii_holding_pct" in overrides and overrides["fii_holding_pct"] is not None:
        snap_updates["fii_holding_pct"] = float(overrides["fii_holding_pct"])
    if "promoter_pledge_pct" in overrides and overrides["promoter_pledge_pct"] is not None:
        snap_updates["pledge_pct"] = float(overrides["promoter_pledge_pct"])
    if "pledge_pct" in overrides and overrides["pledge_pct"] is not None:
        snap_updates["pledge_pct"] = float(overrides["pledge_pct"])
    snap_out = snapshot.model_copy(update=snap_updates) if snap_updates else snapshot

    bank_keys = (
        "nim_pct",
        "gnpa_pct",
        "nnpa_pct",
        "casa_pct",
        "car_pct",
        "credit_cost_pct",
        "provision_coverage_pct",
    )
    fy_updates = {k: float(overrides[k]) for k in bank_keys if k in overrides and overrides[k] is not None}
    fins_out = list(financials)
    if fy_updates and fins_out:
        fins_out = sorted(fins_out, key=lambda f: f.fiscal_year)
        fins_out[-1] = fins_out[-1].model_copy(update=fy_updates)
    return snap_out, fins_out


def evaluate_stock_set1(
    snapshot: StockSnapshot,
    financials: list[StockFinancialYear],
    sector_median: StockSectorMedian | None,
    *,
    is_bank: bool = False,
    min_market_cap_cr: float | None = None,
    min_traded_value_cr: float | None = None,
    risk_profile: str = "Moderate",
    horizon_years: float = 5.0,
    overrides: dict[str, float] | None = None,
) -> StockScreenEvaluation:
    """Evaluate a stock under Set 1 (Existing Equity Heuristics)."""
    snapshot, financials = _apply_overrides(snapshot, financials, overrides)
    ratios = compute_stock_ratios(snapshot, financials, is_bank=is_bank)
    latest_fy = max(financials, key=lambda f: f.fiscal_year) if financials else None
    valuation = compute_relative_valuation(
        snapshot, sector_median, latest_fy=latest_fy, is_bank=is_bank
    )

    criteria: list[StockCriterionResult] = []
    red_flags: list[str] = []

    # 1. P/E <= Sector P/E
    sec_pe = sector_median.median_pe if sector_median else None
    if ratios.pe is None or sec_pe is None or sec_pe <= 0:
        criteria.append(
            StockCriterionResult(
                rule_id="EQ_S1_PE",
                name="P/E <= Sector Median P/E",
                category="valuation",
                threshold_desc=f"<= {_fmt_num(sec_pe, 1, 'x')}" if sec_pe else "<= Sector P/E",
                actual_value=ratios.pe,
                actual_formatted=_fmt_num(ratios.pe, 1, "x"),
                status="missing",
                reason="Missing stock or sector P/E multiple.",
            )
        )
    else:
        ok_pe = ratios.pe <= sec_pe
        criteria.append(
            StockCriterionResult(
                rule_id="EQ_S1_PE",
                name="P/E <= Sector Median P/E",
                category="valuation",
                threshold_desc=f"<= {sec_pe:.1f}x",
                actual_value=ratios.pe,
                actual_formatted=f"{ratios.pe:.1f}x",
                status="passed" if ok_pe else "failed",
                reason=(
                    f"P/E {ratios.pe:.1f}x is at/below sector median {sec_pe:.1f}x."
                    if ok_pe
                    else f"P/E {ratios.pe:.1f}x exceeds sector median {sec_pe:.1f}x."
                ),
            )
        )

    # 2. YoY Earnings Growth > 0
    if ratios.earnings_growth_yoy is None:
        criteria.append(
            StockCriterionResult(
                rule_id="EQ_S1_EARNINGS",
                name="Positive YoY Earnings Growth",
                category="growth",
                threshold_desc="> 0.0%",
                actual_value=None,
                actual_formatted="N/A",
                status="missing",
                reason="Missing YoY earnings growth data.",
            )
        )
    else:
        ok_eg = ratios.earnings_growth_yoy > 0.0
        criteria.append(
            StockCriterionResult(
                rule_id="EQ_S1_EARNINGS",
                name="Positive YoY Earnings Growth",
                category="growth",
                threshold_desc="> 0.0%",
                actual_value=ratios.earnings_growth_yoy,
                actual_formatted=_fmt_pct(ratios.earnings_growth_yoy),
                status="passed" if ok_eg else "failed",
                reason=(
                    f"YoY earnings growth is {_fmt_pct(ratios.earnings_growth_yoy)} (> 0%)."
                    if ok_eg
                    else f"YoY earnings growth is {_fmt_pct(ratios.earnings_growth_yoy)} (<= 0%)."
                ),
            )
        )

    # 3. Debt-to-Equity <= 2.0 (Not applicable for banks where deposits/borrowings are operating raw material)
    if is_bank:
        criteria.append(
            StockCriterionResult(
                rule_id="EQ_S1_DEBT",
                name="Debt-to-Equity <= 2.0",
                category="leverage",
                threshold_desc="<= 2.0x (N/A for Banks)",
                actual_value=ratios.debt_to_equity,
                actual_formatted="N/A (Bank)",
                status="not_applied",
                reason="Standard Debt/Equity is not applicable to banking institutions.",
            )
        )
    elif ratios.debt_to_equity is None:
        criteria.append(
            StockCriterionResult(
                rule_id="EQ_S1_DEBT",
                name="Debt-to-Equity <= 2.0",
                category="leverage",
                threshold_desc="<= 2.00x",
                actual_value=None,
                actual_formatted="N/A",
                status="missing",
                reason="Missing Debt-to-Equity data.",
            )
        )
    else:
        ok_de = ratios.debt_to_equity <= 2.0
        criteria.append(
            StockCriterionResult(
                rule_id="EQ_S1_DEBT",
                name="Debt-to-Equity <= 2.0",
                category="leverage",
                threshold_desc="<= 2.00x",
                actual_value=ratios.debt_to_equity,
                actual_formatted=_fmt_num(ratios.debt_to_equity, 2, "x"),
                status="passed" if ok_de else "failed",
                reason=(
                    f"Debt/Equity is {ratios.debt_to_equity:.2f}x (<= 2.00x)."
                    if ok_de
                    else f"Debt/Equity is {ratios.debt_to_equity:.2f}x (> 2.00x)."
                ),
            )
        )

    # 4. Promoter Pledge <= 25%
    if ratios.pledge_pct is None:
        criteria.append(
            StockCriterionResult(
                rule_id="EQ_S1_PLEDGE",
                name="Promoter Pledge <= 25%",
                category="governance",
                threshold_desc="<= 25.0%",
                actual_value=None,
                actual_formatted="N/A",
                status="missing",
                reason="Missing promoter pledge data.",
            )
        )
    else:
        ok_pl = ratios.pledge_pct <= 25.0
        if not ok_pl:
            red_flags.append(f"Promoter pledge {ratios.pledge_pct:.1f}% exceeds 25.0% cap")
        criteria.append(
            StockCriterionResult(
                rule_id="EQ_S1_PLEDGE",
                name="Promoter Pledge <= 25%",
                category="governance",
                threshold_desc="<= 25.0%",
                actual_value=ratios.pledge_pct,
                actual_formatted=_fmt_pct(ratios.pledge_pct, is_decimal=False),
                status="passed" if ok_pl else "failed",
                reason=(
                    f"Promoter pledge is {ratios.pledge_pct:.1f}% (<= 25.0%)."
                    if ok_pl
                    else f"Promoter pledge is {ratios.pledge_pct:.1f}% (> 25.0%)."
                ),
            )
        )

    # 5. Insider Net Buying Signal (>= 0 Lakhs passes; > 0 is positive signal)
    ok_ins = ratios.insider_net_buy_lakhs >= 0.0
    criteria.append(
        StockCriterionResult(
            rule_id="EQ_S1_INSIDER",
            name="Insider Net Buying Signal",
            category="sentiment",
            threshold_desc=">= ₹0 Lakhs net buying",
            actual_value=ratios.insider_net_buy_lakhs,
            actual_formatted=f"₹{ratios.insider_net_buy_lakhs:.1f}L",
            status="passed" if ok_ins else "failed",
            reason=(
                f"Positive insider net buying of ₹{ratios.insider_net_buy_lakhs:.1f}L."
                if ratios.insider_net_buy_lakhs > 0
                else (
                    "Neutral insider activity (₹0.0L net)."
                    if ok_ins
                    else f"Net insider selling of ₹{abs(ratios.insider_net_buy_lakhs):.1f}L."
                )
            ),
        )
    )

    # Optional Market Cap filter if user supplied min_market_cap_cr
    if min_market_cap_cr is not None and min_market_cap_cr > 0:
        mcap = ratios.market_cap_cr
        ok_mc = mcap is not None and mcap >= min_market_cap_cr
        criteria.append(
            StockCriterionResult(
                rule_id="EQ_S1_MCAP_OPT",
                name=f"Market Cap >= ₹{min_market_cap_cr:,.0f} Cr",
                category="size",
                threshold_desc=f">= ₹{min_market_cap_cr:,.0f} Cr",
                actual_value=mcap,
                actual_formatted=f"₹{mcap:,.0f} Cr" if mcap is not None else "N/A",
                status="passed" if ok_mc else ("missing" if mcap is None else "failed"),
                reason=(
                    f"Market cap ₹{mcap:,.0f} Cr meets user threshold ₹{min_market_cap_cr:,.0f} Cr."
                    if ok_mc and mcap is not None
                    else f"Market cap below user threshold ₹{min_market_cap_cr:,.0f} Cr."
                ),
            )
        )

    if min_traded_value_cr is not None and min_traded_value_cr > 0:
        adtv = ratios.avg_daily_traded_value_cr
        if adtv is None or adtv < min_traded_value_cr:
            red_flags.append(
                f"Average daily traded value ({_fmt_num(adtv, 1, ' Cr')}) below ₹{min_traded_value_cr:.1f} Cr threshold"
            )

    evaluated = [c for c in criteria if c.status in ("passed", "failed")]
    passed_list = [c for c in evaluated if c.status == "passed"]
    evaluated_count = len(evaluated)
    passed_count = len(passed_list)
    total_criteria = len([c for c in criteria if c.status != "not_applied"])
    pass_ratio = (passed_count / evaluated_count) if evaluated_count > 0 else 0.0

    # In Set 1, all hard criteria (PE, Earnings, Debt if non-bank, Pledge, plus optional mcap) must pass
    hard_ids = {"EQ_S1_PE", "EQ_S1_EARNINGS", "EQ_S1_PLEDGE", "EQ_S1_MCAP_OPT"}
    if not is_bank:
        hard_ids.add("EQ_S1_DEBT")
    hard_failed = any(
        c.rule_id in hard_ids and c.status != "passed" for c in criteria if c.status != "not_applied"
    )
    overall_passed = (evaluated_count >= 3) and (not hard_failed) and (len(red_flags) == 0)

    sub_scores, composite = compute_stock_sub_scores(
        ratios,
        valuation,
        pass_ratio=pass_ratio,
        red_flags=red_flags,
        risk_profile=risk_profile,
        horizon_years=horizon_years,
    )

    return StockScreenEvaluation(
        symbol=snapshot.symbol,
        rule_set="set1",
        is_bank=is_bank,
        passed=overall_passed,
        passed_count=passed_count,
        evaluated_count=evaluated_count,
        total_criteria=total_criteria,
        pass_ratio=pass_ratio,
        min_required_evaluated=3,
        criteria=criteria,
        red_flags=red_flags,
        sub_scores=sub_scores,
        composite_score=composite,
        valuation_summary=valuation.to_dict(),
    )


def evaluate_stock_set2(
    snapshot: StockSnapshot,
    financials: list[StockFinancialYear],
    sector_median: StockSectorMedian | None,
    *,
    is_bank: bool = False,
    min_market_cap_cr: float | None = None,
    min_traded_value_cr: float | None = None,
    risk_profile: str = "Moderate",
    horizon_years: float = 5.0,
    overrides: dict[str, float] | None = None,
) -> StockScreenEvaluation:
    """Evaluate a stock under Set 2 (FA Course Notes: 10-Pointer Non-Bank / 6-Pointer Bank)."""
    snapshot, financials = _apply_overrides(snapshot, financials, overrides)
    ratios = compute_stock_ratios(snapshot, financials, is_bank=is_bank)
    ordered_fy = sorted(financials, key=lambda f: f.fiscal_year)
    latest_fy = ordered_fy[-1] if ordered_fy else None
    valuation = compute_relative_valuation(
        snapshot, sector_median, latest_fy=latest_fy, is_bank=is_bank
    )

    criteria: list[StockCriterionResult] = []
    red_flags: list[str] = []

    # Check hard governance & solvency red flags first
    if ratios.pledge_pct is not None and ratios.pledge_pct > 20.0:
        red_flags.append(f"Promoter pledge {ratios.pledge_pct:.1f}% > 20.0% hard limit")
    if ratios.auditor_flags > 0:
        red_flags.append(f"Auditor qualification / adverse remark ({ratios.auditor_flags} flag(s))")
    if latest_fy is not None and latest_fy.net_worth_cr is not None and latest_fy.net_worth_cr <= 0:
        red_flags.append(f"Negative or zero net worth (₹{latest_fy.net_worth_cr:,.0f} Cr)")
    if not is_bank and ratios.interest_coverage is not None and ratios.interest_coverage < 1.5:
        red_flags.append(f"Interest coverage {ratios.interest_coverage:.2f}x < 1.50x hard floor")
    if is_bank and ratios.nnpa_pct is not None and ratios.nnpa_pct > 2.0:
        red_flags.append(f"Bank Net NPA {ratios.nnpa_pct:.2f}% > 2.00% hard limit")
    if min_traded_value_cr is not None and min_traded_value_cr > 0:
        adtv = ratios.avg_daily_traded_value_cr
        if adtv is None or adtv < min_traded_value_cr:
            red_flags.append(
                f"Average daily traded value ({_fmt_num(adtv, 1, ' Cr')}) < ₹{min_traded_value_cr:.1f} Cr threshold"
            )

    mcap_filter_active = min_market_cap_cr is not None and min_market_cap_cr > 0

    if not is_bank:
        # --- 10-Pointer Screen for Non-Banking Stocks ---
        # 1. 5Y Revenue CAGR >= 10%
        rev_cagr = ratios.revenue_cagr_5y
        criteria.append(
            StockCriterionResult(
                rule_id="S2_NB_01_REV_CAGR",
                name="5Y Revenue CAGR >= 10%",
                category="growth",
                threshold_desc=">= 10.0%",
                actual_value=rev_cagr,
                actual_formatted=_fmt_pct(rev_cagr),
                status=(
                    "missing"
                    if rev_cagr is None
                    else ("passed" if rev_cagr >= 0.10 else "failed")
                ),
                reason=(
                    "Missing 5Y revenue series."
                    if rev_cagr is None
                    else f"5Y Revenue CAGR is {_fmt_pct(rev_cagr)} (target >= 10.0%)."
                ),
            )
        )

        # 2. 5Y PAT CAGR >= 12%
        pat_cagr = ratios.pat_cagr_5y
        criteria.append(
            StockCriterionResult(
                rule_id="S2_NB_02_PAT_CAGR",
                name="5Y PAT CAGR >= 12%",
                category="growth",
                threshold_desc=">= 12.0%",
                actual_value=pat_cagr,
                actual_formatted=_fmt_pct(pat_cagr),
                status=(
                    "missing"
                    if pat_cagr is None
                    else ("passed" if pat_cagr >= 0.12 else "failed")
                ),
                reason=(
                    "Missing 5Y PAT series."
                    if pat_cagr is None
                    else f"5Y PAT CAGR is {_fmt_pct(pat_cagr)} (target >= 12.0%)."
                ),
            )
        )

        # 3. 5Y EPS CAGR >= 12%
        eps_cagr = ratios.eps_cagr_5y
        criteria.append(
            StockCriterionResult(
                rule_id="S2_NB_03_EPS_CAGR",
                name="5Y EPS CAGR >= 12%",
                category="growth",
                threshold_desc=">= 12.0%",
                actual_value=eps_cagr,
                actual_formatted=_fmt_pct(eps_cagr),
                status=(
                    "missing"
                    if eps_cagr is None
                    else ("passed" if eps_cagr >= 0.12 else "failed")
                ),
                reason=(
                    "Missing 5Y EPS series."
                    if eps_cagr is None
                    else f"5Y EPS CAGR is {_fmt_pct(eps_cagr)} (target >= 12.0%)."
                ),
            )
        )

        # 4. ROE >= 15% (or ROCE >= 15%)
        roe_val = ratios.roe
        roce_val = ratios.roce
        best_prof = max(roe_val or -999.0, roce_val or -999.0)
        if roe_val is None and roce_val is None:
            criteria.append(
                StockCriterionResult(
                    rule_id="S2_NB_04_ROE_ROCE",
                    name="ROE or ROCE >= 15%",
                    category="profitability",
                    threshold_desc=">= 15.0%",
                    actual_value=None,
                    actual_formatted="N/A",
                    status="missing",
                    reason="Missing both ROE and ROCE.",
                )
            )
        else:
            ok_prof = (roe_val is not None and roe_val >= 0.15) or (
                roce_val is not None and roce_val >= 0.15
            )
            criteria.append(
                StockCriterionResult(
                    rule_id="S2_NB_04_ROE_ROCE",
                    name="ROE or ROCE >= 15%",
                    category="profitability",
                    threshold_desc=">= 15.0%",
                    actual_value=best_prof,
                    actual_formatted=f"ROE {_fmt_pct(roe_val)} / ROCE {_fmt_pct(roce_val)}",
                    status="passed" if ok_prof else "failed",
                    reason=f"ROE is {_fmt_pct(roe_val)} and ROCE is {_fmt_pct(roce_val)} (target >= 15.0%).",
                )
            )

        # 5. Debt-to-Equity <= 1.0
        de_val = ratios.debt_to_equity
        criteria.append(
            StockCriterionResult(
                rule_id="S2_NB_05_DEBT_EQUITY",
                name="Debt-to-Equity <= 1.0",
                category="leverage",
                threshold_desc="<= 1.00x",
                actual_value=de_val,
                actual_formatted=_fmt_num(de_val, 2, "x"),
                status=(
                    "missing"
                    if de_val is None
                    else ("passed" if de_val <= 1.0 else "failed")
                ),
                reason=(
                    "Missing Debt-to-Equity data."
                    if de_val is None
                    else f"Debt-to-Equity is {de_val:.2f}x (target <= 1.00x)."
                ),
            )
        )

        # 6. Interest Coverage >= 3.0
        ic_val = ratios.interest_coverage
        criteria.append(
            StockCriterionResult(
                rule_id="S2_NB_06_INT_COV",
                name="Interest Coverage >= 3.0x",
                category="leverage",
                threshold_desc=">= 3.00x",
                actual_value=ic_val,
                actual_formatted=_fmt_num(ic_val, 1, "x"),
                status=(
                    "missing"
                    if ic_val is None
                    else ("passed" if ic_val >= 3.0 else "failed")
                ),
                reason=(
                    "Missing Interest Coverage data."
                    if ic_val is None
                    else f"Interest Coverage is {ic_val:.1f}x (target >= 3.0x)."
                ),
            )
        )

        # 7. Current Ratio >= 1.5
        cr_val = ratios.current_ratio
        criteria.append(
            StockCriterionResult(
                rule_id="S2_NB_07_CURR_RATIO",
                name="Current Ratio >= 1.5x",
                category="liquidity",
                threshold_desc=">= 1.50x",
                actual_value=cr_val,
                actual_formatted=_fmt_num(cr_val, 2, "x"),
                status=(
                    "missing"
                    if cr_val is None
                    else ("passed" if cr_val >= 1.5 else "failed")
                ),
                reason=(
                    "Missing Current Ratio data."
                    if cr_val is None
                    else f"Current Ratio is {cr_val:.2f}x (target >= 1.50x)."
                ),
            )
        )

        # 8. CFO / PAT >= 0.80
        cfo_pat = ratios.cfo_to_pat
        criteria.append(
            StockCriterionResult(
                rule_id="S2_NB_08_CFO_PAT",
                name="CFO / PAT >= 0.80",
                category="efficiency",
                threshold_desc=">= 0.80 (80%)",
                actual_value=cfo_pat,
                actual_formatted=_fmt_num(cfo_pat, 2, "x"),
                status=(
                    "missing"
                    if cfo_pat is None
                    else ("passed" if cfo_pat >= 0.80 else "failed")
                ),
                reason=(
                    "Missing CFO/PAT cash conversion data."
                    if cfo_pat is None
                    else f"Cumulative CFO/PAT ratio is {cfo_pat:.2f}x (target >= 0.80x)."
                ),
            )
        )

        # 9. Increasing Dividends (or healthy dividend track record)
        div_inc = ratios.dividends_increasing
        criteria.append(
            StockCriterionResult(
                rule_id="S2_NB_09_DIVIDEND",
                name="Consistent / Increasing Dividends",
                category="dividend",
                threshold_desc="Increasing 5Y DPS",
                actual_value=div_inc,
                actual_formatted=(
                    "Yes" if div_inc is True else ("No" if div_inc is False else "N/A")
                ),
                status=(
                    "missing"
                    if div_inc is None
                    else ("passed" if div_inc else "failed")
                ),
                reason=(
                    "Missing dividend history."
                    if div_inc is None
                    else (
                        f"Consistent/increasing dividend track record (5Y DPS CAGR {_fmt_pct(ratios.dps_cagr_5y)})."
                        if div_inc
                        else "Dividend per share has stagnated or declined."
                    )
                ),
            )
        )

        # 10. Optional Market Cap >= min_market_cap_cr
        mcap = ratios.market_cap_cr
        if not mcap_filter_active:
            criteria.append(
                StockCriterionResult(
                    rule_id="S2_NB_10_MCAP",
                    name="Market Cap Threshold (Optional)",
                    category="size",
                    threshold_desc="No limit by default",
                    actual_value=mcap,
                    actual_formatted=f"₹{mcap:,.0f} Cr" if mcap is not None else "N/A",
                    status="not_applied",
                    reason="No market-cap limit applied by default (9-pointer active).",
                )
            )
        else:
            assert min_market_cap_cr is not None
            ok_mc = mcap is not None and mcap >= min_market_cap_cr
            criteria.append(
                StockCriterionResult(
                    rule_id="S2_NB_10_MCAP",
                    name=f"Market Cap >= ₹{min_market_cap_cr:,.0f} Cr",
                    category="size",
                    threshold_desc=f">= ₹{min_market_cap_cr:,.0f} Cr",
                    actual_value=mcap,
                    actual_formatted=f"₹{mcap:,.0f} Cr" if mcap is not None else "N/A",
                    status="missing" if mcap is None else ("passed" if ok_mc else "failed"),
                    reason=(
                        f"Market cap ₹{mcap:,.0f} Cr >= ₹{min_market_cap_cr:,.0f} Cr."
                        if ok_mc and mcap is not None
                        else f"Market cap below ₹{min_market_cap_cr:,.0f} Cr threshold."
                    ),
                )
            )

        min_required_evaluated = 7 if mcap_filter_active else 6
    else:
        # --- 6-Pointer Screen for Banking / NBFC Stocks ---
        # 1. 5Y NII / Revenue CAGR >= 10%
        nii_cagr = ratios.nii_cagr_5y if ratios.nii_cagr_5y is not None else ratios.revenue_cagr_5y
        criteria.append(
            StockCriterionResult(
                rule_id="S2_BK_01_NII_CAGR",
                name="5Y NII / Revenue CAGR >= 10%",
                category="growth",
                threshold_desc=">= 10.0%",
                actual_value=nii_cagr,
                actual_formatted=_fmt_pct(nii_cagr),
                status=(
                    "missing"
                    if nii_cagr is None
                    else ("passed" if nii_cagr >= 0.10 else "failed")
                ),
                reason=(
                    "Missing 5Y NII/Revenue series."
                    if nii_cagr is None
                    else f"5Y NII/Revenue CAGR is {_fmt_pct(nii_cagr)} (target >= 10.0%)."
                ),
            )
        )

        # 2. Net Interest Margin (NIM) >= 3.0%
        nim = ratios.nim_pct
        criteria.append(
            StockCriterionResult(
                rule_id="S2_BK_02_NIM",
                name="Net Interest Margin (NIM) >= 3.0%",
                category="profitability",
                threshold_desc=">= 3.0%",
                actual_value=nim,
                actual_formatted=_fmt_pct(nim, is_decimal=False),
                status=(
                    "missing"
                    if nim is None
                    else ("passed" if nim >= 3.0 else "failed")
                ),
                reason=(
                    "Missing NIM data."
                    if nim is None
                    else f"NIM is {nim:.2f}% (target >= 3.0%)."
                ),
            )
        )

        # 3. Asset Quality: GNPA <= 3.0% AND NNPA <= 1.0%
        gnpa = ratios.gnpa_pct
        nnpa = ratios.nnpa_pct
        if gnpa is None and nnpa is None:
            criteria.append(
                StockCriterionResult(
                    rule_id="S2_BK_03_ASSET_QUALITY",
                    name="GNPA <= 3.0% & NNPA <= 1.0%",
                    category="asset_quality",
                    threshold_desc="GNPA <= 3.0%, NNPA <= 1.0%",
                    actual_value=None,
                    actual_formatted="N/A",
                    status="missing",
                    reason="Missing GNPA/NNPA data.",
                )
            )
        else:
            ok_aq = (gnpa is None or gnpa <= 3.0) and (nnpa is None or nnpa <= 1.0)
            criteria.append(
                StockCriterionResult(
                    rule_id="S2_BK_03_ASSET_QUALITY",
                    name="GNPA <= 3.0% & NNPA <= 1.0%",
                    category="asset_quality",
                    threshold_desc="GNPA <= 3.0%, NNPA <= 1.0%",
                    actual_value=nnpa if nnpa is not None else gnpa,
                    actual_formatted=f"GNPA {_fmt_pct(gnpa, is_decimal=False)} / NNPA {_fmt_pct(nnpa, is_decimal=False)}",
                    status="passed" if ok_aq else "failed",
                    reason=f"GNPA is {_fmt_pct(gnpa, is_decimal=False)} and NNPA is {_fmt_pct(nnpa, is_decimal=False)}.",
                )
            )

        # 4. Liability & Capital Adequacy: CASA >= 40% AND CAR >= 12%
        casa = ratios.casa_pct
        car = ratios.car_pct
        if casa is None and car is None:
            criteria.append(
                StockCriterionResult(
                    rule_id="S2_BK_04_CASA_CAR",
                    name="CASA >= 40% & CAR >= 12%",
                    category="capital_liability",
                    threshold_desc="CASA >= 40.0%, CAR >= 12.0%",
                    actual_value=None,
                    actual_formatted="N/A",
                    status="missing",
                    reason="Missing CASA/CAR data.",
                )
            )
        else:
            ok_cc = (casa is not None and casa >= 40.0) and (car is not None and car >= 12.0)
            criteria.append(
                StockCriterionResult(
                    rule_id="S2_BK_04_CASA_CAR",
                    name="CASA >= 40% & CAR >= 12%",
                    category="capital_liability",
                    threshold_desc="CASA >= 40.0%, CAR >= 12.0%",
                    actual_value=casa,
                    actual_formatted=f"CASA {_fmt_pct(casa, is_decimal=False)} / CAR {_fmt_pct(car, is_decimal=False)}",
                    status="passed" if ok_cc else "failed",
                    reason=f"CASA is {_fmt_pct(casa, is_decimal=False)} and CAR is {_fmt_pct(car, is_decimal=False)}.",
                )
            )

        # 5. Bank Profitability: ROE >= 12% AND ROA >= 1.0%
        roe_b = ratios.roe
        roa_b = ratios.roa
        if roe_b is None and roa_b is None:
            criteria.append(
                StockCriterionResult(
                    rule_id="S2_BK_05_ROE_ROA",
                    name="ROE >= 12% & ROA >= 1.0%",
                    category="profitability",
                    threshold_desc="ROE >= 12.0%, ROA >= 1.0%",
                    actual_value=None,
                    actual_formatted="N/A",
                    status="missing",
                    reason="Missing ROE/ROA data.",
                )
            )
        else:
            ok_rr = (roe_b is not None and roe_b >= 0.12) and (
                roa_b is not None and roa_b >= 0.01
            )
            criteria.append(
                StockCriterionResult(
                    rule_id="S2_BK_05_ROE_ROA",
                    name="ROE >= 12% & ROA >= 1.0%",
                    category="profitability",
                    threshold_desc="ROE >= 12.0%, ROA >= 1.0%",
                    actual_value=roe_b,
                    actual_formatted=f"ROE {_fmt_pct(roe_b)} / ROA {_fmt_pct(roa_b)}",
                    status="passed" if ok_rr else "failed",
                    reason=f"ROE is {_fmt_pct(roe_b)} and ROA is {_fmt_pct(roa_b)}.",
                )
            )

        # 6. Optional Market Cap >= min_market_cap_cr
        mcap = ratios.market_cap_cr
        if not mcap_filter_active:
            criteria.append(
                StockCriterionResult(
                    rule_id="S2_BK_06_MCAP",
                    name="Market Cap Threshold (Optional)",
                    category="size",
                    threshold_desc="No limit by default",
                    actual_value=mcap,
                    actual_formatted=f"₹{mcap:,.0f} Cr" if mcap is not None else "N/A",
                    status="not_applied",
                    reason="No market-cap limit applied by default (5-pointer active).",
                )
            )
        else:
            assert min_market_cap_cr is not None
            ok_mc = mcap is not None and mcap >= min_market_cap_cr
            criteria.append(
                StockCriterionResult(
                    rule_id="S2_BK_06_MCAP",
                    name=f"Market Cap >= ₹{min_market_cap_cr:,.0f} Cr",
                    category="size",
                    threshold_desc=f">= ₹{min_market_cap_cr:,.0f} Cr",
                    actual_value=mcap,
                    actual_formatted=f"₹{mcap:,.0f} Cr" if mcap is not None else "N/A",
                    status="missing" if mcap is None else ("passed" if ok_mc else "failed"),
                    reason=(
                        f"Market cap ₹{mcap:,.0f} Cr >= ₹{min_market_cap_cr:,.0f} Cr."
                        if ok_mc and mcap is not None
                        else f"Market cap below ₹{min_market_cap_cr:,.0f} Cr threshold."
                    ),
                )
            )

        min_required_evaluated = 4

    active_criteria = [c for c in criteria if c.status != "not_applied"]
    evaluated = [c for c in active_criteria if c.status in ("passed", "failed")]
    passed_list = [c for c in evaluated if c.status == "passed"]
    evaluated_count = len(evaluated)
    passed_count = len(passed_list)
    total_criteria = len(active_criteria)
    pass_ratio = (passed_count / evaluated_count) if evaluated_count > 0 else 0.0

    # When min_market_cap_cr is explicitly supplied, the market cap check itself must also pass
    mcap_criterion_ok = True
    if mcap_filter_active:
        mcap_crit = next(
            (c for c in criteria if c.rule_id in ("S2_NB_10_MCAP", "S2_BK_06_MCAP")),
            None,
        )
        if mcap_crit is None or mcap_crit.status != "passed":
            mcap_criterion_ok = False

    overall_passed = (
        evaluated_count >= min_required_evaluated
        and pass_ratio >= 0.80
        and len(red_flags) == 0
        and mcap_criterion_ok
    )

    sub_scores, composite = compute_stock_sub_scores(
        ratios,
        valuation,
        pass_ratio=pass_ratio,
        red_flags=red_flags,
        risk_profile=risk_profile,
        horizon_years=horizon_years,
    )

    return StockScreenEvaluation(
        symbol=snapshot.symbol,
        rule_set="set2",
        is_bank=is_bank,
        passed=overall_passed,
        passed_count=passed_count,
        evaluated_count=evaluated_count,
        total_criteria=total_criteria,
        pass_ratio=pass_ratio,
        min_required_evaluated=min_required_evaluated,
        criteria=criteria,
        red_flags=red_flags,
        sub_scores=sub_scores,
        composite_score=composite,
        valuation_summary=valuation.to_dict(),
    )


def evaluate_stock(
    snapshot: StockSnapshot,
    financials: list[StockFinancialYear],
    sector_median: StockSectorMedian | None,
    *,
    rule_set: str = "set2",
    is_bank: bool = False,
    min_market_cap_cr: float | None = None,
    min_traded_value_cr: float | None = None,
    risk_profile: str = "Moderate",
    horizon_years: float = 5.0,
    overrides: dict[str, float] | None = None,
) -> StockScreenEvaluation:
    """Evaluate a stock under the requested stock rule set ('set1' or 'set2')."""
    if rule_set == "set1":
        return evaluate_stock_set1(
            snapshot,
            financials,
            sector_median,
            is_bank=is_bank,
            min_market_cap_cr=min_market_cap_cr,
            min_traded_value_cr=min_traded_value_cr,
            risk_profile=risk_profile,
            horizon_years=horizon_years,
            overrides=overrides,
        )
    return evaluate_stock_set2(
        snapshot,
        financials,
        sector_median,
        is_bank=is_bank,
        min_market_cap_cr=min_market_cap_cr,
        min_traded_value_cr=min_traded_value_cr,
        risk_profile=risk_profile,
        horizon_years=horizon_years,
        overrides=overrides,
    )


def get_stock_rules_catalog(rule_set: str = "set2") -> dict[str, Any]:
    """Return structured rule definitions for `/api/stocks/rules` and `RulesViewer.tsx`."""
    rs = rule_set if rule_set in ("set1", "set2") else "set2"
    meta = STOCK_RULE_SET_META[rs]

    if rs == "set1":
        rules = [
            {
                "rule_id": "EQ_S1_PE",
                "name": "P/E <= Sector Median P/E",
                "scope": "All Stocks",
                "category": "valuation",
                "threshold": "pe <= sector_pe",
                "rationale": "Avoids overpaying relative to sector peers (Value at Reasonable Price).",
            },
            {
                "rule_id": "EQ_S1_EARNINGS",
                "name": "Positive YoY Earnings Growth",
                "scope": "All Stocks",
                "category": "growth",
                "threshold": "earnings_growth_yoy > 0%",
                "rationale": "Confirms positive near-term earnings momentum.",
            },
            {
                "rule_id": "EQ_S1_DEBT",
                "name": "Manageable Leverage",
                "scope": "Non-Banking Stocks",
                "category": "leverage",
                "threshold": "debt_to_equity <= 2.0x",
                "rationale": "Screens out over-leveraged balance sheets (not applied to banks).",
            },
            {
                "rule_id": "EQ_S1_PLEDGE",
                "name": "Promoter Pledge Cap",
                "scope": "All Stocks",
                "category": "governance",
                "threshold": "pledge_pct <= 25.0%",
                "rationale": "Protects against forced promoter share sales.",
            },
            {
                "rule_id": "EQ_S1_INSIDER",
                "name": "Insider Net Buying Signal",
                "scope": "All Stocks",
                "category": "sentiment",
                "threshold": "insider_net_buy_lakhs >= 0",
                "rationale": "Tracks insider confidence and awards bonus sentiment score for net buying.",
            },
        ]
    else:
        rules = [
            {
                "rule_id": "S2_NB_01_REV_CAGR",
                "name": "5Y Revenue CAGR >= 10%",
                "scope": "Non-Bank (10-Pointer)",
                "category": "growth",
                "threshold": "revenue_cagr_5y >= 10%",
                "rationale": "Demonstrates sustained top-line demand across an economic cycle.",
            },
            {
                "rule_id": "S2_NB_02_PAT_CAGR",
                "name": "5Y PAT CAGR >= 12%",
                "scope": "Non-Bank (10-Pointer)",
                "category": "growth",
                "threshold": "pat_cagr_5y >= 12%",
                "rationale": "Ensures bottom-line profit grows faster than revenue via operating leverage.",
            },
            {
                "rule_id": "S2_NB_03_EPS_CAGR",
                "name": "5Y EPS CAGR >= 12%",
                "scope": "Non-Bank (10-Pointer)",
                "category": "growth",
                "threshold": "eps_cagr_5y >= 12%",
                "rationale": "Confirms per-share compounding without excessive equity dilution.",
            },
            {
                "rule_id": "S2_NB_04_ROE_ROCE",
                "name": "ROE or ROCE >= 15%",
                "scope": "Non-Bank (10-Pointer)",
                "category": "profitability",
                "threshold": "roe >= 15% or roce >= 15%",
                "rationale": "Verifies capital efficiency above cost of capital.",
            },
            {
                "rule_id": "S2_NB_05_DEBT_EQUITY",
                "name": "Debt-to-Equity <= 1.0x",
                "scope": "Non-Bank (10-Pointer)",
                "category": "leverage",
                "threshold": "debt_to_equity <= 1.0x",
                "rationale": "Conservative solvency threshold from FA Course Notes.",
            },
            {
                "rule_id": "S2_NB_06_INT_COV",
                "name": "Interest Coverage >= 3.0x",
                "scope": "Non-Bank (10-Pointer)",
                "category": "leverage",
                "threshold": "interest_coverage >= 3.0x (Hard floor >= 1.5x)",
                "rationale": "Ensures operating profit comfortably covers debt servicing.",
            },
            {
                "rule_id": "S2_NB_07_CURR_RATIO",
                "name": "Current Ratio >= 1.5x",
                "scope": "Non-Bank (10-Pointer)",
                "category": "liquidity",
                "threshold": "current_ratio >= 1.5x",
                "rationale": "Confirms short-term working capital cushion.",
            },
            {
                "rule_id": "S2_NB_08_CFO_PAT",
                "name": "5Y Cumulative CFO / PAT >= 0.80",
                "scope": "Non-Bank (10-Pointer)",
                "category": "efficiency",
                "threshold": "cfo_to_pat >= 0.80",
                "rationale": "Validates that accounting profits convert into real operating cash flow.",
            },
            {
                "rule_id": "S2_NB_09_DIVIDEND",
                "name": "Consistent / Increasing Dividends",
                "scope": "Non-Bank (10-Pointer)",
                "category": "dividend",
                "threshold": "Increasing 5Y DPS",
                "rationale": "Signals shareholder-friendly capital allocation and real cash generation.",
            },
            {
                "rule_id": "S2_NB_10_MCAP",
                "name": "Market Cap >= User Threshold (Optional)",
                "scope": "Non-Bank (10-Pointer)",
                "category": "size",
                "threshold": "No limit by default (9-pointer); optional min_market_cap_cr",
                "rationale": "Allows full NSE universe screening by default or user-chosen size filter.",
            },
            {
                "rule_id": "S2_BK_01_NII_CAGR",
                "name": "5Y NII / Revenue CAGR >= 10%",
                "scope": "Bank / NBFC (6-Pointer)",
                "category": "growth",
                "threshold": "nii_cagr_5y >= 10%",
                "rationale": "Measures core lending income growth for banks.",
            },
            {
                "rule_id": "S2_BK_02_NIM",
                "name": "Net Interest Margin (NIM) >= 3.0%",
                "scope": "Bank / NBFC (6-Pointer)",
                "category": "profitability",
                "threshold": "nim_pct >= 3.0%",
                "rationale": "Measures spread earned between lending yield and cost of funds.",
            },
            {
                "rule_id": "S2_BK_03_ASSET_QUALITY",
                "name": "GNPA <= 3.0% & NNPA <= 1.0%",
                "scope": "Bank / NBFC (6-Pointer)",
                "category": "asset_quality",
                "threshold": "gnpa_pct <= 3.0% and nnpa_pct <= 1.0% (Hard cap NNPA <= 2.0%)",
                "rationale": "Screens for disciplined underwriting and low bad-loan stress.",
            },
            {
                "rule_id": "S2_BK_04_CASA_CAR",
                "name": "CASA >= 40% & CAR >= 12%",
                "scope": "Bank / NBFC (6-Pointer)",
                "category": "capital_liability",
                "threshold": "casa_pct >= 40% and car_pct >= 12%",
                "rationale": "Low-cost deposit franchise (CASA) + strong regulatory capital buffer (CAR).",
            },
            {
                "rule_id": "S2_BK_05_ROE_ROA",
                "name": "Bank ROE >= 12% & ROA >= 1.0%",
                "scope": "Bank / NBFC (6-Pointer)",
                "category": "profitability",
                "threshold": "roe >= 12% and roa >= 1.0%",
                "rationale": "Gold-standard bank return thresholds from FA Course Notes.",
            },
            {
                "rule_id": "S2_BK_06_MCAP",
                "name": "Market Cap >= User Threshold (Optional)",
                "scope": "Bank / NBFC (6-Pointer)",
                "category": "size",
                "threshold": "No limit by default (5-pointer); optional min_market_cap_cr",
                "rationale": "Optional size filter when requested by user.",
            },
        ]

    return {
        "rule_set": rs,
        "meta": meta,
        "rules": rules,
        "pass_policy": (
            "All evaluated hard checks must pass with zero governance red flags."
            if rs == "set1"
            else (
                "Non-banks: >= 80% pass across evaluated criteria (min 6/9 by default, or 7/10 when "
                "min_market_cap_cr is set). Banks: >= 80% pass (min 4/5 by default, or 4/6 when "
                "min_market_cap_cr is set). Zero hard governance/solvency red flags."
            )
        ),
    }


def compare_stock_rule_sets() -> dict[str, Any]:
    """Return side-by-side comparison of Stock Set 1 and Stock Set 2 for `/api/stocks/rules/compare`."""
    return {
        "rule_sets": {
            "set1": get_stock_rules_catalog("set1"),
            "set2": get_stock_rules_catalog("set2"),
        },
        "dimensions": [
            {
                "dimension": "Growth Horizon",
                "set1": "1-Year YoY Earnings Growth > 0%",
                "set2": "5-Year Revenue CAGR >= 10%, PAT CAGR >= 12%, EPS CAGR >= 12% (or NII CAGR >= 10% for Banks)",
            },
            {
                "dimension": "Profitability & Return Ratios",
                "set1": "Not explicitly gated",
                "set2": "ROE or ROCE >= 15% (Non-Banks); NIM >= 3%, ROE >= 12% & ROA >= 1% (Banks)",
            },
            {
                "dimension": "Leverage & Solvency",
                "set1": "Debt-to-Equity <= 2.0x",
                "set2": "Debt-to-Equity <= 1.0x, Interest Coverage >= 3.0x (Hard floor 1.5x), Current Ratio >= 1.5x",
            },
            {
                "dimension": "Cash Flow & Earnings Quality",
                "set1": "Not explicitly gated",
                "set2": "5Y Cumulative CFO / PAT >= 0.80 & Increasing Dividends",
            },
            {
                "dimension": "Banking / NBFC Specialization",
                "set1": "Generic equity screen (D/E skipped)",
                "set2": "Dedicated 6-Pointer Bank Screen (NII CAGR, NIM, GNPA/NNPA, CASA, CAR, ROE/ROA)",
            },
            {
                "dimension": "Valuation Model",
                "set1": "Single check: P/E <= Sector Median P/E",
                "set2": "Weighted Relative Valuation (30% EV/EBITDA, 30% P/S, 30% P/E, 10% P/B; 60% P/B + 40% P/E for Banks) with [1.0x, 1.2x] Fair Value Range",
            },
            {
                "dimension": "Governance Red Flags",
                "set1": "Promoter Pledge <= 25%, Insider Buying signal",
                "set2": "Hard red flags: Pledge > 20%, Auditor flags > 0, Net Worth <= 0, Interest Coverage < 1.5x, Bank NNPA > 2%",
            },
            {
                "dimension": "Market Cap & Liquidity Filter",
                "set1": "No limit by default (optional min_market_cap_cr & min_traded_value_cr)",
                "set2": "No limit by default (9-pointer / 5-pointer); optional min_market_cap_cr activates 10th / 6th pointer",
            },
        ],
    }
