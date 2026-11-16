"""Rule catalog definitions, live example generators, and rule-set comparison.

Provides:
- Catalog of all system rules across 7 groups with metadata, descriptions, kinds, and source citations.
- Live dynamic example generation based on the active Settings.
- `describe_rule_set(name)`: Grouped rules with live values, examples, and provenance.
- `compare_rule_sets()`: Tabular side-by-side comparison across Set 1, Set 2, and Combined.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from .rulesets import (
    AVAILABLE_RULE_SETS,
    COMBINED_PROVENANCE,
    SET1_RULES,
    SET2_RULES,
)


@dataclass
class RuleCatalogEntry:
    id: str
    group: str  # Allocation, Capacity caps, Fund filters, Scoring, Categories & horizons, Warnings, Disclosures
    title: str
    description: str
    kind: str  # hard filter, score, allocation, disclosure
    setting_keys: list[str]
    source_ref: str
    example_fn: Callable[[Settings], str]
    value_fn: Callable[[Settings], Any] = field(default=lambda s: None)


def _fmt_bands(bands: dict[str, dict[str, tuple[float, float]]]) -> str:
    parts = []
    for profile in ("Conservative", "Moderate", "Aggressive"):
        b = bands.get(profile, {})
        eq = b.get("equity", (0, 0))
        debt = b.get("debt", (0, 0))
        gold = b.get("gold", (0, 0))
        parts.append(f"{profile}: Eq {eq[0]:g}-{eq[1]:g}%, Debt {debt[0]:g}-{debt[1]:g}%, Gold {gold[0]:g}-{gold[1]:g}%")
    return " | ".join(parts)


def _ex_allocation_bands(s: Settings) -> str:
    b = s.allocation_bands.get("Moderate", {})
    eq = b.get("equity", (40.0, 60.0))
    debt = b.get("debt", (30.0, 45.0))
    gold = b.get("gold", (5.0, 10.0))
    mid_eq = (eq[0] + eq[1]) / 2
    mid_debt = (debt[0] + debt[1]) / 2
    mid_gold = (gold[0] + gold[1]) / 2
    return (
        f"A Moderate profile gets {eq[0]:g}-{eq[1]:g}% equity, {debt[0]:g}-{debt[1]:g}% debt, "
        f"{gold[0]:g}-{gold[1]:g}% gold. Rs 10L at midpoints is Rs {mid_eq * 10000:,.0f} equity, "
        f"Rs {mid_debt * 10000:,.0f} debt, Rs {mid_gold * 10000:,.0f} gold."
    )


def _ex_cap_1y(s: Settings) -> str:
    return f"A goal with an 8-month horizon gets at most {s.equity_cap_horizon_lt_1y:g}% equity."


def _ex_cap_3y(s: Settings) -> str:
    return f"A 2-year goal gets at most {s.equity_cap_horizon_lt_3y:g}% equity."


def _ex_cap_5y(s: Settings) -> str:
    if s.equity_cap_horizon_lt_5y is not None:
        return f"A 4-year goal gets at most {s.equity_cap_horizon_lt_5y:g}% equity (capped for < 5y horizon)."
    return "No 3-5y cap in this rule set; standard profile allocation bands apply."


def _ex_cap_emergency(s: Settings) -> str:
    return (
        f"An aggressive investor with only 2 months emergency savings is capped at "
        f"{s.equity_cap_low_emergency:g}% equity (below {s.low_emergency_months:g}m threshold)."
    )


def _ex_direct_plan(s: Settings) -> str:
    return "A regular plan with 1.2% expense ratio is rejected in favor of the direct plan at 0.4%."


def _ex_aum_equity(s: Settings) -> str:
    return f"Large Cap fund with AUM of Rs 350 Cr is rejected (requires >= Rs {s.min_aum_cr:g} Cr)."


def _ex_aum_small(s: Settings) -> str:
    return f"Liquid or Gold ETF fund with AUM of Rs 60 Cr is rejected (floor is Rs {s.min_aum_cr_small:g} Cr)."


def _ex_track_years(s: Settings) -> str:
    return (
        f"An equity fund launched 3 years ago is rejected (requires >= {s.min_track_years_equity:g}y); "
        f"debt funds require >= {s.min_track_years_debt:g}y."
    )


def _ex_rating(s: Settings) -> str:
    if s.min_rating is not None:
        return f"A fund with 3.0 stars is rejected (requires at least {s.min_rating:g} stars)."
    return "Star ratings inform scoring but no hard rejection floor is enforced."


def _ex_alpha(s: Settings) -> str:
    if s.require_positive_alpha:
        return "Active equity fund with alpha -0.8% is rejected because it trails its benchmark."
    return "Active funds with negative alpha are scored down but not automatically rejected."


def _ex_maturity_match(s: Settings) -> str:
    if s.match_maturity_to_horizon:
        return "A Corporate Bond fund with 3.8y average maturity is rejected for a 2-year horizon goal."
    return "Debt categories are selected by category horizon rather than exact maturity matching."


def _get_analyst_weights_dict(s: Settings) -> dict[str, float]:
    w = s.analyst_weights
    if hasattr(w, "model_dump"):
        return w.model_dump()
    return dict(w)


def _ex_analyst_weights(s: Settings) -> str:
    w = _get_analyst_weights_dict(s)
    return (
        f"Analyst scoring weights: Fundamental {w.get('fundamental', 0):.1%}, Fit {w.get('fit', 0):.1%}, "
        f"News {w.get('news', 0):.1%}, Sentiment {w.get('sentiment', 0):.1%}, Cost {w.get('liquidity_cost', 0):.1%}."
    )


def _ex_fundamental_weights(s: Settings) -> str:
    fw = s.fundamental_weights
    return (
        f"Fundamental metrics: 3y Roll {fw.get('pct_roll_3y', 0):.0%}, 5y CAGR {fw.get('pct_cagr_5y', 0):.0%}, "
        f"Sharpe {fw.get('sharpe', 0):.0%}, Drawdown {fw.get('max_drawdown', 0):.0%}, "
        f"Alpha {fw.get('alpha', 0):.0%}, Low-Vol {fw.get('pct_low_vol', 0):.0%}."
    )


def _ex_max_beta(s: Settings) -> str:
    if s.max_beta is not None:
        return f"An equity fund with beta 1.15 exceeds max beta {s.max_beta:g} and receives a fit penalty."
    return "No beta ceiling applied; volatility caps govern fit scoring."


def _ex_trusted_amcs(s: Settings) -> str:
    if s.trusted_amc_bonus > 0 and s.trusted_amcs:
        return f"Funds from {', '.join(s.trusted_amcs)} receive +{s.trusted_amc_bonus:g} points on the liquidity/cost score."
    return "No AMC brand bonus applied in this rule set."


def _ex_buy_min_score(s: Settings) -> str:
    return (
        f"A fund scoring 48/100 is rejected: minimum BUY threshold is {s.buy_min_score:g}/100 "
        f"with confidence >= {s.min_confidence:.0%}."
    )


def _ex_vol_caps(s: Settings) -> str:
    vc = s.vol_caps
    return (
        f"Annualized volatility caps: Conservative {vc.get('Conservative', 0):.1%}, "
        f"Moderate {vc.get('Moderate', 0):.1%}, Aggressive {vc.get('Aggressive', 0):.1%}."
    )


def _ex_max_weight(s: Settings) -> str:
    return f"A Rs 10L portfolio cannot allocate more than Rs {10 * s.max_weight_per_instrument / 100:.1f}L ({s.max_weight_per_instrument:g}%) to any single fund."


def _ex_allowed_categories(s: Settings) -> str:
    n = len(s.allowed_categories)
    first_few = ", ".join(s.allowed_categories[:4])
    return f"{n} categories allowed for selection: {first_few}, and {n - 4} others."


def _ex_category_min_horizons(s: Settings) -> str:
    h = s.category_min_horizons
    return (
        f"Minimum holding horizons: Liquid {h.get('Liquid', 0):g}y, Short Duration {h.get('Short Duration', 0):g}y, "
        f"Mid Cap {h.get('Mid Cap', 0):g}y, Small Cap {h.get('Small Cap', 0):g}y."
    )


def _ex_expense_ceilings(s: Settings) -> str:
    mid = s.expense_ceilings.get("Mid Cap", 1.0)
    return f"Mid Cap fund with expense ratio {mid + 0.2:.2f}% is scored down (category ceiling is {mid:.2f}%)."


def _ex_benchmark_proxies(s: Settings) -> str:
    return f"Benchmark proxies: Large Cap -> UTI Nifty 50 ({s.benchmark_proxy_codes.get('Large Cap', '120716')}), Mid Cap -> {s.benchmark_proxy_codes.get('Mid Cap', '147622')}."


def _ex_manager_tenure(s: Settings) -> str:
    if s.min_manager_tenure_years is not None:
        return f"Manager tenure of 1.5y triggers a warning because it is below the {s.min_manager_tenure_years:g}y threshold."
    return "No manager tenure warning threshold configured."


def _ex_ytm_outlier(s: Settings) -> str:
    if s.ytm_outlier_pp is not None:
        return f"Debt fund with YTM 9.5% vs category median 7.2% is flagged (+2.3pp exceeds {s.ytm_outlier_pp:g}pp credit risk limit)."
    return "No YTM outlier warning threshold configured."


def _ex_elss_lockin(s: Settings) -> str:
    return "ELSS investments are subject to a statutory lock-in period of 3 years from the date of allotment."


def _ex_capital_gains(s: Settings) -> str:
    return "Equity funds (>65% equity) are taxed at 20% STCG / 12.5% LTCG (>Rs 1.25L). Debt funds are taxed at income slab rates."


RULE_CATALOG: list[RuleCatalogEntry] = [
    # ---- 1. Allocation ----
    RuleCatalogEntry(
        id="alloc.bands",
        group="Allocation",
        title="Asset Allocation Bands",
        description="Target equity, debt, gold, and alternatives percentage ranges by client risk profile.",
        kind="allocation",
        setting_keys=["allocation_bands"],
        source_ref="Strategic Asset Allocation Policy",
        example_fn=_ex_allocation_bands,
        value_fn=lambda s: _fmt_bands(s.allocation_bands),
    ),
    RuleCatalogEntry(
        id="alloc.max_weight_per_instrument",
        group="Allocation",
        title="Maximum Weight per Instrument",
        description="Caps allocation to any single fund as a percentage of the total portfolio.",
        kind="hard filter",
        setting_keys=["max_weight_per_instrument"],
        source_ref="Single-Scheme Concentration Limit",
        example_fn=_ex_max_weight,
        value_fn=lambda s: f"{s.max_weight_per_instrument:g}%",
    ),

    # ---- 2. Capacity caps ----
    RuleCatalogEntry(
        id="caps.horizon_lt_1y",
        group="Capacity caps",
        title="Horizon < 1 Year Equity Cap",
        description="Maximum equity exposure allowed for ultra short-term investment goals under 1 year.",
        kind="hard filter",
        setting_keys=["equity_cap_horizon_lt_1y"],
        source_ref="Ultra-Short Horizon Guardrail",
        example_fn=_ex_cap_1y,
        value_fn=lambda s: f"{s.equity_cap_horizon_lt_1y:g}%",
    ),
    RuleCatalogEntry(
        id="caps.horizon_lt_3y",
        group="Capacity caps",
        title="Horizon < 3 Years Equity Cap",
        description="Maximum equity exposure allowed for short-term investment goals under 3 years.",
        kind="hard filter",
        setting_keys=["equity_cap_horizon_lt_3y"],
        source_ref="Short-Term Horizon Guardrail",
        example_fn=_ex_cap_3y,
        value_fn=lambda s: f"{s.equity_cap_horizon_lt_3y:g}%",
    ),
    RuleCatalogEntry(
        id="caps.horizon_lt_5y",
        group="Capacity caps",
        title="Horizon 3-5 Years Equity Cap",
        description="Maximum equity exposure allowed for medium-term goals between 3 and 5 years.",
        kind="hard filter",
        setting_keys=["equity_cap_horizon_lt_5y"],
        source_ref="Medium-Term Horizon Guardrail",
        example_fn=_ex_cap_5y,
        value_fn=lambda s: f"{s.equity_cap_horizon_lt_5y:g}%" if s.equity_cap_horizon_lt_5y is not None else "None",
    ),
    RuleCatalogEntry(
        id="caps.low_emergency",
        group="Capacity caps",
        title="Low Emergency Fund Equity Cap",
        description="Maximum equity exposure when liquid emergency savings are below the required threshold.",
        kind="hard filter",
        setting_keys=["equity_cap_low_emergency", "low_emergency_months"],
        source_ref="Emergency Buffer Risk Capacity",
        example_fn=_ex_cap_emergency,
        value_fn=lambda s: f"{s.equity_cap_low_emergency:g}% (< {s.low_emergency_months:g}m)",
    ),

    # ---- 3. Fund filters ----
    RuleCatalogEntry(
        id="filter.direct_plan",
        group="Fund filters",
        title="Direct Plan Requirement",
        description="Enforces selection of direct growth plans to eliminate distributor commissions.",
        kind="hard filter",
        setting_keys=["require_direct_plan"],
        source_ref="Plan Structure & Costs",
        example_fn=_ex_direct_plan,
        value_fn=lambda s: "Enforced" if s.require_direct_plan else "Optional",
    ),
    RuleCatalogEntry(
        id="filter.min_aum",
        group="Fund filters",
        title="Minimum Fund AUM",
        description="Requires adequate fund size to ensure liquidity and protect against redemption pressure.",
        kind="hard filter",
        setting_keys=["min_aum_cr", "min_aum_cr_small"],
        source_ref="Fund Size & Liquidity",
        example_fn=_ex_aum_equity,
        value_fn=lambda s: f"Rs {s.min_aum_cr:g} Cr (Rs {s.min_aum_cr_small:g} Cr for small)",
    ),
    RuleCatalogEntry(
        id="filter.track_record",
        group="Fund filters",
        title="Minimum Track Record",
        description="Requires historical performance track record across market cycles.",
        kind="hard filter",
        setting_keys=["min_track_years_equity", "min_track_years_debt"],
        source_ref="Fund Longevity & Track Record",
        example_fn=_ex_track_years,
        value_fn=lambda s: f"Equity {s.min_track_years_equity:g}y, Debt {s.min_track_years_debt:g}y",
    ),
    RuleCatalogEntry(
        id="filter.min_rating",
        group="Fund filters",
        title="Minimum Star Rating",
        description="Quality filter requiring a minimum independent rating for candidate mutual funds.",
        kind="hard filter",
        setting_keys=["min_rating"],
        source_ref="Independent Quality Rating Filter",
        example_fn=_ex_rating,
        value_fn=lambda s: f"{s.min_rating:g} Stars" if s.min_rating is not None else "None",
    ),
    RuleCatalogEntry(
        id="filter.positive_alpha",
        group="Fund filters",
        title="Positive Alpha on Active Equity",
        description="Requires active equity funds to generate positive Jensen's alpha against their category benchmark.",
        kind="hard filter",
        setting_keys=["require_positive_alpha"],
        source_ref="Benchmark Outperformance (Alpha)",
        example_fn=_ex_alpha,
        value_fn=lambda s: "Required (> 0.0%)" if s.require_positive_alpha else "Not Required",
    ),
    RuleCatalogEntry(
        id="filter.match_maturity",
        group="Fund filters",
        title="Debt Maturity Matching",
        description="Ensures debt scheme average maturity does not exceed the investor's goal horizon.",
        kind="hard filter",
        setting_keys=["match_maturity_to_horizon"],
        source_ref="Interest Rate & Duration Matching",
        example_fn=_ex_maturity_match,
        value_fn=lambda s: "Enforced" if s.match_maturity_to_horizon else "Not Enforced",
    ),

    # ---- 4. Scoring ----
    RuleCatalogEntry(
        id="scoring.analyst_weights",
        group="Scoring",
        title="Analyst Dimension Weights",
        description="Weights applied across the five fundamental and qualitative analyst scoring dimensions.",
        kind="score",
        setting_keys=["analyst_weights"],
        source_ref="Multi-Factor Scoring Model",
        example_fn=_ex_analyst_weights,
        value_fn=lambda s: {k: round(v, 3) for k, v in _get_analyst_weights_dict(s).items()},
    ),
    RuleCatalogEntry(
        id="scoring.fundamental_weights",
        group="Scoring",
        title="Fundamental Sub-Score Weights",
        description="Weights assigned to quantitative metrics (rolling returns, CAGR, Sharpe, drawdown, alpha, low-vol).",
        kind="score",
        setting_keys=["fundamental_weights"],
        source_ref="Quantitative Return & Risk Metrics",
        example_fn=_ex_fundamental_weights,
        value_fn=lambda s: {k: round(v, 3) for k, v in s.fundamental_weights.items()},
    ),
    RuleCatalogEntry(
        id="scoring.max_beta",
        group="Scoring",
        title="Beta Ceiling for Fit Score",
        description="Threshold above which higher market sensitivity beta triggers a score penalty.",
        kind="score",
        setting_keys=["max_beta"],
        source_ref="Market Sensitivity (Beta)",
        example_fn=_ex_max_beta,
        value_fn=lambda s: f"{s.max_beta:g}" if s.max_beta is not None else "None",
    ),
    RuleCatalogEntry(
        id="scoring.trusted_amcs",
        group="Scoring",
        title="Trusted AMC Score Bonus",
        description="Score bonus awarded to top-tier institutional AMCs for liquidity and operational resilience.",
        kind="score",
        setting_keys=["trusted_amcs", "trusted_amc_bonus"],
        source_ref="AMC Institutional Quality",
        example_fn=_ex_trusted_amcs,
        value_fn=lambda s: f"+{s.trusted_amc_bonus:g} pts ({', '.join(s.trusted_amcs)})" if s.trusted_amc_bonus > 0 else "None",
    ),
    RuleCatalogEntry(
        id="scoring.decision_thresholds",
        group="Scoring",
        title="BUY Decision Thresholds",
        description="Minimum score and confidence required for an instrument to be recommended.",
        kind="score",
        setting_keys=["buy_min_score", "min_confidence"],
        source_ref="Recommendation Quality Guardrail",
        example_fn=_ex_buy_min_score,
        value_fn=lambda s: f"Score >= {s.buy_min_score:g}, Confidence >= {s.min_confidence:.0%}",
    ),
    RuleCatalogEntry(
        id="scoring.vol_caps",
        group="Scoring",
        title="Volatility Ceilings by Risk Profile",
        description="Maximum annual volatility threshold before a fund incurs an allocation fit penalty.",
        kind="score",
        setting_keys=["vol_caps"],
        source_ref="Volatility & Risk Control",
        example_fn=_ex_vol_caps,
        value_fn=lambda s: {k: f"{v:.1%}" for k, v in s.vol_caps.items()},
    ),

    # ---- 5. Categories & horizons ----
    RuleCatalogEntry(
        id="cat.allowed",
        group="Categories & horizons",
        title="Allowed Categories Universe",
        description="SEBI mutual fund categories eligible for research and portfolio recommendations.",
        kind="hard filter",
        setting_keys=["allowed_categories"],
        source_ref="SEBI Scheme Categorization",
        example_fn=_ex_allowed_categories,
        value_fn=lambda s: f"{len(s.allowed_categories)} categories",
    ),
    RuleCatalogEntry(
        id="cat.min_horizons",
        group="Categories & horizons",
        title="Category Minimum Holding Horizons",
        description="Recommended minimum investment holding period per mutual fund category.",
        kind="hard filter",
        setting_keys=["category_min_horizons"],
        source_ref="Category Holding Horizon",
        example_fn=_ex_category_min_horizons,
        value_fn=lambda s: {k: f"{v:g}y" for k, v in list(s.category_min_horizons.items())[:6]},
    ),
    RuleCatalogEntry(
        id="cat.expense_ceilings",
        group="Categories & horizons",
        title="Expense Ratio Ceilings",
        description="Maximum expected direct TER per category; funds exceeding ceiling are scored down.",
        kind="score",
        setting_keys=["expense_ceilings"],
        source_ref="Expense & Fee Discipline",
        example_fn=_ex_expense_ceilings,
        value_fn=lambda s: {k: f"{v:.2f}%" for k, v in list(s.expense_ceilings.items())[:6]},
    ),
    RuleCatalogEntry(
        id="cat.benchmark_proxies",
        group="Categories & horizons",
        title="Benchmark Index Proxies",
        description="Index funds used as market return proxies to compute beta and Jensen's alpha.",
        kind="score",
        setting_keys=["benchmark_proxy_codes"],
        source_ref="Category Benchmark Proxies",
        example_fn=_ex_benchmark_proxies,
        value_fn=lambda s: s.benchmark_proxy_codes,
    ),

    # ---- 6. Warnings ----
    RuleCatalogEntry(
        id="warn.manager_tenure",
        group="Warnings",
        title="Fund Manager Tenure Floor",
        description="Generates an advisory conflict warning if the current manager has managed the scheme for less than the threshold.",
        kind="score",
        setting_keys=["min_manager_tenure_years"],
        source_ref="Fund Manager Stability",
        example_fn=_ex_manager_tenure,
        value_fn=lambda s: f"{s.min_manager_tenure_years:g}y" if s.min_manager_tenure_years is not None else "None",
    ),
    RuleCatalogEntry(
        id="warn.ytm_outlier",
        group="Warnings",
        title="YTM Outlier Credit Risk Flag",
        description="Warns when debt scheme yield-to-maturity exceeds category median by more than the threshold.",
        kind="score",
        setting_keys=["ytm_outlier_pp"],
        source_ref="Debt Credit Risk Guardrail",
        example_fn=_ex_ytm_outlier,
        value_fn=lambda s: f"+{s.ytm_outlier_pp:g} pp" if s.ytm_outlier_pp is not None else "None",
    ),

    # ---- 7. Disclosures ----
    RuleCatalogEntry(
        id="disc.elss_lockin",
        group="Disclosures",
        title="ELSS Statutory 3-Year Lock-In",
        description="Mandatory disclosure explaining the statutory lock-in period for tax saving mutual funds.",
        kind="disclosure",
        setting_keys=[],
        source_ref="Section 80C, Income Tax Act",
        example_fn=_ex_elss_lockin,
        value_fn=lambda s: "3-Year Statutory Lock-In",
    ),
    RuleCatalogEntry(
        id="disc.taxation",
        group="Disclosures",
        title="Mutual Fund Taxation & Exit Load",
        description="Standard regulatory disclosures regarding capital gains tax treatment and scheme exit loads.",
        kind="disclosure",
        setting_keys=[],
        source_ref="SEBI MF Regulations / Finance Act 2024",
        example_fn=_ex_capital_gains,
        value_fn=lambda s: "Equity vs Debt Taxation Rules",
    ),
]


def describe_rule_set(name: str = "set1") -> dict[str, Any]:
    """Return catalog rules grouped with live values and examples for the given rule set."""
    s = get_settings(name)
    meta = next((m for m in AVAILABLE_RULE_SETS if m["id"] == name), AVAILABLE_RULE_SETS[0])

    groups_map: dict[str, list[dict[str, Any]]] = {}
    for entry in RULE_CATALOG:
        val = entry.value_fn(s)
        used = True
        if val in ("None", "Not Required", "Not Enforced", None):
            used = False

        rule_dict: dict[str, Any] = {
            "id": entry.id,
            "title": entry.title,
            "description": entry.description,
            "kind": entry.kind,
            "group": entry.group,
            "setting_keys": entry.setting_keys,
            "source_ref": entry.source_ref,
            "value": val,
            "example": entry.example_fn(s),
            "used": used,
        }

        if name == "combined":
            prov = [p for p in COMBINED_PROVENANCE if any(k in entry.setting_keys for k in [p["setting"]])]
            if prov:
                rule_dict["provenance"] = prov[0]

        groups_map.setdefault(entry.group, []).append(rule_dict)

    groups = [{"name": g, "rules": rules} for g, rules in groups_map.items()]

    out: dict[str, Any] = {
        "rule_set": name,
        "label": meta["label"],
        "description": meta["description"],
        "groups": groups,
    }
    if name == "combined":
        out["provenance"] = COMBINED_PROVENANCE
        out["provenance_summary"] = COMBINED_PROVENANCE
    return out


def compare_rule_sets() -> list[dict[str, Any]]:
    """Compare all catalog rules side-by-side across Set 1, Set 2, and Combined."""
    s1 = get_settings("set1")
    s2 = get_settings("set2")
    sc = get_settings("combined")

    rows: list[dict[str, Any]] = []
    for entry in RULE_CATALOG:
        v1 = entry.value_fn(s1)
        v2 = entry.value_fn(s2)
        vc = entry.value_fn(sc)

        has_diff = v1 != v2
        strat = "Identical"
        winner = "Both"
        rationale = "Rules are identical across both sets."

        prov = [p for p in COMBINED_PROVENANCE if any(k in entry.setting_keys for k in [p["setting"]])]
        if prov:
            p = prov[0]
            strat = p["strategy"]
            winner = p["winner"]
            rationale = p["reason"]
        elif has_diff:
            strat = "Custom"
            winner = "Combined"
            rationale = "Merged across multiple parameters."

        rows.append({
            "id": entry.id,
            "rule_id": entry.id,
            "group": entry.group,
            "title": entry.title,
            "kind": entry.kind,
            "setting_keys": entry.setting_keys,
            "source_ref": entry.source_ref,
            "set1": v1,
            "set2": v2,
            "combined": vc,
            "set1_value": v1,
            "set2_value": v2,
            "combined_value": vc,
            "has_difference": has_diff,
            "strategy": strat,
            "winner": winner,
            "rationale": rationale,
        })
    return rows

