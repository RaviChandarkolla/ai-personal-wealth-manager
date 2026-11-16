"""AI & deterministic narrative synthesis for Stock Comparison and Single-Stock Analysis."""

from __future__ import annotations

from typing import Any

from ..domain.stock_compare import StockComparisonReport
from ..guardrails.compliance import scrub
from ..llm.base import LLMClient


class StockNarratorAgent:
    """Generates educational, compliance-checked narratives for stock comparison & deep-dive analysis."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def narrate_comparison(self, report: StockComparisonReport) -> str:
        """Synthesize a structured head-to-head comparison narrative."""
        fallback = self._deterministic_comparison(report)
        if self.llm is None:
            return fallback

        try:
            prompt = (
                "Summarize this head-to-head Indian equity fundamental comparison in 3 concise "
                "paragraphs covering: (1) Screening scorecard & overall leader, (2) Growth, ROE/ROCE, "
                "and balance sheet / cash-flow differences, and (3) Relative valuation vs sector medians. "
                "Remain strictly educational and avoid buy/sell imperatives or return guarantees.\n\n"
                f"Symbols: {', '.join(report.symbols)}\n"
                f"Active Rule Set: {report.rule_set}\n"
                f"Criterion Wins: {report.wins_by_symbol}\n"
                f"Overall Leader: {report.overall_winner}\n"
                f"Stocks Summary: {report.stocks}\n"
            )
            raw = self.llm.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a SEBI-compliant equity fundamental research analyst. "
                            "Provide objective, data-backed analysis without guaranteed-return claims."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            text = raw.strip() if isinstance(raw, str) else ""
            if not text:
                return fallback
            cleaned, _ = scrub(text)
            return cleaned
        except Exception:  # noqa: BLE001
            return fallback

    def narrate_single_stock(self, analysis: dict[str, Any]) -> str:
        """Synthesize an educational fundamental analyst memo for a single stock."""
        fallback = self._deterministic_single_stock(analysis)
        if self.llm is None:
            return fallback

        try:
            ov = analysis.get("overview", {})
            scr = analysis.get("screening", {}).get("active", {})
            val = analysis.get("valuation", {})
            ratios = analysis.get("ratios", {})
            prompt = (
                f"Write a concise 4-section fundamental analysis memo for {ov.get('name')} "
                f"({ov.get('symbol')}, Sector: {ov.get('sector')}).\n"
                f"Screen Result ({scr.get('rule_set')}): passed={scr.get('passed')} "
                f"({scr.get('passed_count')}/{scr.get('evaluated_count')}), "
                f"Composite Score={scr.get('composite_score')}, Red Flags={scr.get('red_flags')}.\n"
                f"Ratios: ROE={ratios.get('roe')}, ROCE={ratios.get('roce')}, "
                f"5Y Rev CAGR={ratios.get('revenue_cagr_5y')}, 5Y PAT CAGR={ratios.get('pat_cagr_5y')}, "
                f"D/E={ratios.get('debt_to_equity')}, CFO/PAT={ratios.get('cfo_to_pat')}.\n"
                f"Valuation: CMP={val.get('cmp')}, Fair Value Range=[{val.get('fair_value_low')}, "
                f"{val.get('fair_value_high')}], Verdict={val.get('verdict')}.\n"
                "Stay strictly educational and do not use guaranteed return language."
            )
            raw = self.llm.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a SEBI-compliant fundamental equity research analyst. "
                            "Write clear, objective analysis grounded in financial ratios."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            text = raw.strip() if isinstance(raw, str) else ""
            if not text:
                return fallback
            cleaned, _ = scrub(text)
            return cleaned
        except Exception:  # noqa: BLE001
            return fallback

    def _deterministic_comparison(self, report: StockComparisonReport) -> str:
        leader = report.overall_winner or report.symbols[0]
        wins_str = ", ".join(
            f"{sym}: {report.wins_by_symbol.get(sym, 0)} wins" for sym in report.symbols
        )
        rs_label = (
            "Set 2 (FA Course Notes)"
            if report.rule_set == "set2"
            else "Set 1 (Equity Heuristics)"
        )

        parts = [
            (
                f"Head-to-Head Summary ({rs_label}): Across {', '.join(report.symbols)}, "
                f"{leader} leads the comparison matrix ({wins_str})."
            )
        ]
        if report.cross_sector_warning:
            parts.append(report.cross_sector_warning)

        stock_notes: list[str] = []
        for st in report.stocks:
            sym = st["symbol"]
            status_lbl = "PASS" if st["passed"] else "FAIL"
            fv_low = st.get("fair_value_low")
            fv_high = st.get("fair_value_high")
            fv_str = (
                f"Fair Value ₹{fv_low:,.0f}–₹{fv_high:,.0f} ({st.get('verdict')})"
                if fv_low and fv_high
                else f"Valuation: {st.get('verdict')}"
            )
            stock_notes.append(
                f"{sym} [{status_lbl} {st['passed_count']}/{st['evaluated_count']}, "
                f"Score {st['composite_score']:.1f}, {fv_str}]"
            )
        parts.append("Screening & Valuation Breakdown: " + "; ".join(stock_notes) + ".")
        parts.append(
            "Note: Direct equity investments carry company-specific and market volatility risks; "
            "past financial ratios do not indicate future returns."
        )
        cleaned, _ = scrub(" ".join(parts))
        return cleaned

    def _deterministic_single_stock(self, analysis: dict[str, Any]) -> str:
        ov = analysis.get("overview", {})
        scr = analysis.get("screening", {}).get("active", {})
        val = analysis.get("valuation", {})
        ratios = analysis.get("ratios", {})

        sym = ov.get("symbol", "")
        name = ov.get("name", sym)
        sector = ov.get("sector", "Equity")
        is_bank = bool(ov.get("is_bank", False))

        passed = bool(scr.get("passed", False))
        p_cnt = scr.get("passed_count", 0)
        e_cnt = scr.get("evaluated_count", 0)
        score = scr.get("composite_score", 0.0)
        red_flags = scr.get("red_flags", [])

        status_txt = "passes" if passed else "does not pass"
        rs_label = (
            "Set 2 (FA Course Notes 6-Pointer Bank Screen)"
            if (scr.get("rule_set") == "set2" and is_bank)
            else (
                "Set 2 (FA Course Notes 10-Pointer Screen)"
                if scr.get("rule_set") == "set2"
                else "Set 1 (Equity Heuristics)"
            )
        )

        lines = [
            (
                f"1. Executive Summary: {name} ({sym}) operates in {sector} and {status_txt} the "
                f"{rs_label} with {p_cnt}/{e_cnt} criteria satisfied (Composite Score: {score:.1f}/100)."
            )
        ]

        if is_bank:
            nim = ratios.get("nim_pct")
            gnpa = ratios.get("gnpa_pct")
            nnpa = ratios.get("nnpa_pct")
            casa = ratios.get("casa_pct")
            car = ratios.get("car_pct")
            roe = (ratios.get("roe") or 0.0) * 100.0
            roa = (ratios.get("roa") or 0.0) * 100.0
            lines.append(
                f"2. Banking Franchise & Asset Quality: NIM stands at {nim if nim is not None else 'N/A'}%, "
                f"with GNPA at {gnpa if gnpa is not None else 'N/A'}% and NNPA at {nnpa if nnpa is not None else 'N/A'}%. "
                f"CASA ratio is {casa if casa is not None else 'N/A'}%, CAR is {car if car is not None else 'N/A'}%, "
                f"ROE is {roe:.1f}%, and ROA is {roa:.2f}%."
            )
        else:
            rev_cagr = (ratios.get("revenue_cagr_5y") or 0.0) * 100.0
            pat_cagr = (ratios.get("pat_cagr_5y") or 0.0) * 100.0
            roe = (ratios.get("roe") or 0.0) * 100.0
            roce = (ratios.get("roce") or 0.0) * 100.0
            de = ratios.get("debt_to_equity")
            cfo_pat = ratios.get("cfo_to_pat")
            lines.append(
                f"2. Growth, Profitability & Cash Conversion: 5Y Revenue CAGR is {rev_cagr:.1f}% and "
                f"5Y PAT CAGR is {pat_cagr:.1f}%. Return ratios are ROE {roe:.1f}% / ROCE {roce:.1f}%, "
                f"with Debt-to-Equity of {f'{de:.2f}x' if de is not None else 'N/A'} and 5Y cumulative "
                f"CFO/PAT of {f'{cfo_pat:.2f}x' if cfo_pat is not None else 'N/A'}."
            )

        cmp = val.get("cmp")
        fv_l = val.get("fair_value_low")
        fv_h = val.get("fair_value_high")
        verdict = val.get("verdict", "insufficient_data")
        if cmp and fv_l and fv_h:
            lines.append(
                f"3. Relative Valuation: At CMP ₹{cmp:,.2f}, the sector-relative weighted model indicates a "
                f"fair value band of ₹{fv_l:,.2f}–₹{fv_h:,.2f} (verdict: {verdict.replace('_', ' ')})."
            )
        else:
            lines.append(
                "3. Relative Valuation: Insufficient sector multiple coverage for a full fair-value band."
            )

        if red_flags:
            lines.append(f"4. Key Risks & Red Flags: {'; '.join(red_flags)}.")
        else:
            lines.append(
                "4. Governance & Risk Note: Zero hard governance/solvency red flags triggered; "
                "equity investments remain subject to market and sector volatility."
            )

        cleaned, _ = scrub("\n\n".join(lines))
        return cleaned
