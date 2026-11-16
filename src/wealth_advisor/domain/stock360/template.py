"""Section registry: Step 0 + S1-S17 with part, required items and source type (D/W/L).

Item source codes: D = derived in Python, W = web evidence, L = LLM judgement,
Y = Yahoo data, M = manual override.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

TEMPLATE_VERSION = "master-v2"
TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "prompts" / "stock360" / "master_v2.md"


@dataclass(frozen=True)
class SectionSpec:
    id: str
    number: int  # 0 for step 0
    title: str  # as in the template heading (text after the dash)
    part: int
    items: dict[str, str] = field(default_factory=dict)  # item key -> source code(s)


def _s(id_: str, n: int, title: str, part: int, items: dict[str, str]) -> SectionSpec:
    return SectionSpec(id_, n, title, part, items)


SECTIONS: tuple[SectionSpec, ...] = (
    _s("step0", 0, "MANDATORY LIVE DATA LOCK & FRESHNESS AUDIT", 1, {
        "freshness_table": "D", "corporate_action_check": "Y,D,W"}),
    _s("s1", 1, "LATEST MARKET DATA", 1, {
        "cmp_52w": "Y,D", "market_cap_ev": "Y", "sector_industry": "Y",
        "index_membership": "W", "shareholding": "W,M", "pledge": "W,M",
        "delivery_volume": "W", "returns_rs": "D"}),
    _s("s2", 2, "FUNDAMENTAL & FORENSIC ANALYSIS", 1, {
        "income_statement": "Y,D", "other_income_pct_pbt": "D", "balance_sheet": "Y,D",
        "goodwill_contingent": "W,M", "cash_flow": "Y,D", "cash_conversion": "D",
        "return_ratios": "D", "order_book_visibility": "W,M"}),
    _s("s3", 3, "VALUATION ANALYSIS", 2, {
        "multiples": "D", "vs_own_history": "D", "vs_sector_peers": "D",
        "cyclicality": "D", "overvaluation_verdict": "D"}),
    _s("s4", 4, "INTRINSIC VALUE CALCULATION", 2, {
        "reverse_dcf": "D", "dcf": "D", "lynch_peg": "D", "graham_number": "D",
        "composite_iv": "D"}),
    _s("s5", 5, "PRICE SURGE vs. FUNDAMENTALS AUDIT", 2, {
        "price_vs_fundamentals": "D", "return_decomposition": "D", "mean_reversion": "D"}),
    _s("s6", 6, "MOAT ANALYSIS", 2, {"moat_scores": "L", "moat_verdict": "L"}),
    _s("s7", 7, "MANAGEMENT QUALITY & GOVERNANCE ASSESSMENT", 2, {
        "management_rating": "L", "red_flags": "W,L"}),
    _s("s8", 8, "GROWTH POTENTIAL ANALYSIS", 3, {
        "tam_drivers": "W,L", "projections": "D"}),
    _s("s9", 9, "RISK ANALYSIS", 3, {"risk_matrix": "D,L", "overall_risk": "D,L"}),
    _s("s10", 10, "TECHNICAL ANALYSIS", 3, {
        "moving_averages": "D", "momentum_indicators": "D", "support_resistance": "D",
        "volume": "D", "weinstein_stage": "D", "chart_patterns": "W"}),
    _s("s11", 11, "SENTIMENT & FLOWS ANALYSIS", 3, {
        "news": "W", "analyst_consensus": "W", "fii_dii_flows": "W", "block_deals": "W"}),
    _s("s12", 12, "WARREN BUFFETT BUY ANALYSIS", 4, {
        "checklist": "D,L", "entry_price": "D", "verdict": "L"}),
    _s("s13", 13, "PETER LYNCH BUY ANALYSIS", 4, {
        "category": "D,L", "peg_entries": "D", "two_minute_drill": "L"}),
    _s("s14", 14, "TARGET PRICES & TRADE EXECUTION PLAN", 4, {
        "short_term_targets": "D", "long_term_targets": "D", "entry_plan": "D,L"}),
    _s("s15", 15, "INVESTMENT VERDICT", 4, {
        "stars": "D", "suitability": "L", "verdict_text": "L", "final_action": "D,L"}),
    _s("s16", 16, "INSTITUTIONAL MARKET CYCLE PHASE ANALYSIS", 3, {
        "phase": "D,W,L"}),
    _s("s17", 17, "SELL DECISION ANALYSIS: FAMOUS INVESTORS & TRADERS", 4, {
        "legend_triggers": "D,L,W", "consensus_score": "D", "phased_exit": "L", "tax": "D"}),
)

SECTION_BY_ID: dict[str, SectionSpec] = {s.id: s for s in SECTIONS}


def sections_for_part(part: int) -> list[SectionSpec]:
    return [s for s in SECTIONS if s.part == part]


def template_headings(text: str | None = None) -> list[tuple[int, str]]:
    """Parse (section number, title) for STEP 0 and SECTION n lines in the template file."""
    import re

    text = text if text is not None else TEMPLATE_PATH.read_text(encoding="utf-8")
    out: list[tuple[int, str]] = []
    for line in text.splitlines():
        m = re.match(r"^(?:STEP (0)|SECTION (\d+)) — (.+)$", line.strip())
        if m:
            num = int(m.group(1) or m.group(2))
            out.append((num, m.group(3).strip()))
    return out
