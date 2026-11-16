"""Head-to-head stock comparison engine (2-4 stocks, Non-Bank 18-metric & Bank 14-metric matrices)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from wealth_advisor.data.stocks.provider import (
    StockFinancialYear,
    StockMasterItem,
    StockPricePoint,
    StockSectorMedian,
    StockSnapshot,
)
from wealth_advisor.domain.stock_ratios import StockComputedRatios, compute_stock_ratios
from wealth_advisor.domain.stock_rulesets import (
    StockScreenEvaluation,
    evaluate_stock_set1,
    evaluate_stock_set2,
)
from wealth_advisor.domain.stock_valuation import (
    StockRelativeValuation,
    compute_relative_valuation,
)


@dataclass
class StockCompareBundle:
    """All input data needed to compare a single stock."""

    master: StockMasterItem
    snapshot: StockSnapshot
    financials: list[StockFinancialYear]
    prices: list[StockPricePoint]
    sector_median: StockSectorMedian | None


@dataclass
class StockCriterionComparison:
    """Single row in the head-to-head comparison matrix."""

    key: str
    label: str
    category: str
    direction: str  # "higher_is_better" | "lower_is_better" | "optimal_range"
    unit: str
    values: dict[str, float | None]
    formatted: dict[str, str]
    winner_symbol: str | None
    tied_symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StockComparisonReport:
    """Complete head-to-head comparison report for 2-4 stocks."""

    symbols: list[str]
    rule_set: str
    matrix_type: str  # "bank" | "non_bank" | "mixed"
    cross_sector_warning: str | None
    stocks: list[dict[str, Any]]
    criteria_matrix: list[StockCriterionComparison]
    wins_by_symbol: dict[str, int]
    overall_winner: str | None
    evaluations: dict[str, dict[str, Any]]
    evaluations_set1: dict[str, dict[str, Any]]
    evaluations_set2: dict[str, dict[str, Any]]
    valuations: dict[str, dict[str, Any]]
    normalized_price_series: list[dict[str, Any]]
    financial_growth_series: list[dict[str, Any]]
    narrative: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbols": list(self.symbols),
            "rule_set": self.rule_set,
            "matrix_type": self.matrix_type,
            "cross_sector_warning": self.cross_sector_warning,
            "stocks": self.stocks,
            "criteria_matrix": [c.to_dict() for c in self.criteria_matrix],
            "wins_by_symbol": dict(self.wins_by_symbol),
            "overall_winner": self.overall_winner,
            "evaluations": self.evaluations,
            "evaluations_set1": self.evaluations_set1,
            "evaluations_set2": self.evaluations_set2,
            "valuations": self.valuations,
            "normalized_price_series": self.normalized_price_series,
            "financial_growth_series": self.financial_growth_series,
            "narrative": self.narrative,
        }


def _fmt_val(val: float | None, unit: str) -> str:
    if val is None:
        return "N/A"
    if unit == "cr":
        return f"₹{val:,.0f} Cr"
    if unit == "pct_dec":
        return f"{val * 100.0:.1f}%"
    if unit == "pct":
        return f"{val:.2f}%"
    if unit == "x":
        return f"{val:.2f}x"
    return f"{val:.2f}"


def _pick_winner(
    values: dict[str, float | None],
    direction: str,
) -> tuple[str | None, list[str]]:
    valid = {sym: v for sym, v in values.items() if v is not None}
    if len(valid) < 2:
        return None, []

    if direction == "higher_is_better":
        best_val = max(valid.values())
        eps = max(1e-6, abs(best_val) * 1e-4)
        winners = [sym for sym, v in valid.items() if abs(v - best_val) <= eps]
    elif direction == "lower_is_better":
        best_val = min(valid.values())
        eps = max(1e-6, abs(best_val) * 1e-4)
        winners = [sym for sym, v in valid.items() if abs(v - best_val) <= eps]
    else:
        # optimal_range for Current Ratio: higher is better up to 3.0, gentle penalty above 3.0
        def _cr_score(v: float) -> float:
            if v <= 3.0:
                return v
            return max(0.0, 3.0 - (v - 3.0) * 0.5)

        scored = {sym: _cr_score(v) for sym, v in valid.items()}
        best_val = max(scored.values())
        eps = 1e-4
        winners = [sym for sym, s in scored.items() if abs(s - best_val) <= eps]

    if len(winners) == 1:
        return winners[0], []
    return None, winners


def _build_normalized_prices(bundles: list[StockCompareBundle]) -> list[dict[str, Any]]:
    """Build base-100 normalized price series across all compared stocks."""
    if not bundles:
        return []
    series_by_sym: dict[str, dict[str, float]] = {}
    all_dates: set[str] = set()
    for b in bundles:
        ordered = sorted(b.prices, key=lambda p: p.price_date)
        if not ordered:
            continue
        base = ordered[0].close
        if base <= 0:
            continue
        sym_map: dict[str, float] = {}
        for pt in ordered:
            sym_map[pt.price_date] = round((pt.close / base) * 100.0, 2)
            all_dates.add(pt.price_date)
        series_by_sym[b.master.symbol] = sym_map

    rows: list[dict[str, Any]] = []
    for dt in sorted(all_dates):
        row: dict[str, Any] = {"date": dt}
        for b in bundles:
            sym = b.master.symbol
            if sym in series_by_sym and dt in series_by_sym[sym]:
                row[sym] = series_by_sym[sym][dt]
        rows.append(row)
    return rows


def _build_financial_growth_series(bundles: list[StockCompareBundle]) -> list[dict[str, Any]]:
    """Build 5Y revenue and PAT series indexed by fiscal year for comparison charts."""
    years: set[int] = set()
    fy_map: dict[str, dict[int, StockFinancialYear]] = {}
    for b in bundles:
        sym = b.master.symbol
        fy_map[sym] = {}
        for fy in b.financials:
            years.add(fy.fiscal_year)
            fy_map[sym][fy.fiscal_year] = fy

    rows: list[dict[str, Any]] = []
    for yr in sorted(years):
        row: dict[str, Any] = {"fiscal_year": f"FY{str(yr)[-2:]}"}
        for b in bundles:
            sym = b.master.symbol
            fy_obj = fy_map.get(sym, {}).get(yr)
            row[f"{sym}_revenue_cr"] = fy_obj.revenue_cr if fy_obj else None
            row[f"{sym}_pat_cr"] = fy_obj.pat_cr if fy_obj else None
            row[f"{sym}_eps"] = fy_obj.eps if fy_obj else None
        rows.append(row)
    return rows


def _apply_compare_overrides(
    bundle: StockCompareBundle,
    overrides: dict[str, float] | None,
) -> StockCompareBundle:
    if not overrides:
        return bundle
    snap_updates: dict[str, Any] = {}
    if "fii_holding_pct" in overrides:
        snap_updates["fii_holding_pct"] = float(overrides["fii_holding_pct"])
    if "promoter_pledge_pct" in overrides:
        snap_updates["pledge_pct"] = float(overrides["promoter_pledge_pct"])
    if "pledge_pct" in overrides:
        snap_updates["pledge_pct"] = float(overrides["pledge_pct"])
    snap_out = bundle.snapshot.model_copy(update=snap_updates) if snap_updates else bundle.snapshot

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
    fins_out = list(bundle.financials)
    if fy_updates and fins_out:
        fins_out = sorted(fins_out, key=lambda f: f.fiscal_year)
        fins_out[-1] = fins_out[-1].model_copy(update=fy_updates)
    return StockCompareBundle(
        master=bundle.master,
        snapshot=snap_out,
        financials=fins_out,
        prices=bundle.prices,
        sector_median=bundle.sector_median,
    )


def compare_stocks(
    bundles: list[StockCompareBundle] | StockCompareBundle,
    bundle_b: StockCompareBundle | None = None,
    *,
    rule_set: str = "set2",
    min_market_cap_cr: float | None = None,
    min_traded_value_cr: float | None = None,
    overrides_a: dict[str, float] | None = None,
    overrides_b: dict[str, float] | None = None,
    overrides_by_symbol: dict[str, dict[str, float]] | None = None,
) -> StockComparisonReport:
    """Compare 2 to 4 stocks head-to-head with Set 1 & Set 2 checklists and relative valuation."""
    if isinstance(bundles, StockCompareBundle):
        if bundle_b is None:
            raise ValueError("Stock comparison requires between 2 and 4 stocks.")
        bundle_list = [bundles, bundle_b]
    else:
        bundle_list = list(bundles)

    if len(bundle_list) < 2 or len(bundle_list) > 4:
        raise ValueError("Stock comparison requires between 2 and 4 stocks.")

    ov_map: dict[str, dict[str, float]] = dict(overrides_by_symbol or {})
    if overrides_a and len(bundle_list) >= 1:
        ov_map[bundle_list[0].master.symbol] = overrides_a
    if overrides_b and len(bundle_list) >= 2:
        ov_map[bundle_list[1].master.symbol] = overrides_b

    bundles = [_apply_compare_overrides(b, ov_map.get(b.master.symbol)) for b in bundle_list]

    rs = rule_set if rule_set in ("set1", "set2") else "set2"
    symbols = [b.master.symbol for b in bundles]
    sectors = {b.master.sector for b in bundles}
    bank_flags = {b.master.is_bank for b in bundles}

    all_banks = bank_flags == {True}
    all_non_banks = bank_flags == {False}
    matrix_type = "bank" if all_banks else ("non_bank" if all_non_banks else "mixed")

    cross_sector_warning: str | None = None
    if len(bank_flags) > 1:
        cross_sector_warning = (
            "Cross-sector alert: Comparing Banking/NBFC stocks alongside Non-Banking stocks. "
            "Leverage, working capital, and EV/EBITDA multiples do not apply to banks."
        )
    elif len(sectors) > 1:
        cross_sector_warning = (
            f"Cross-sector comparison across {', '.join(sorted(sectors))}. "
            "Valuation multiples and margins are best interpreted relative to each stock's own sector median."
        )

    ratios_map: dict[str, StockComputedRatios] = {}
    eval_s1_map: dict[str, StockScreenEvaluation] = {}
    eval_s2_map: dict[str, StockScreenEvaluation] = {}
    val_map: dict[str, StockRelativeValuation] = {}
    stock_headers: list[dict[str, Any]] = []

    for b in bundles:
        sym = b.master.symbol
        is_b = b.master.is_bank
        ratios = compute_stock_ratios(b.snapshot, b.financials, is_bank=is_b)
        ratios_map[sym] = ratios

        ev1 = evaluate_stock_set1(
            b.snapshot,
            b.financials,
            b.sector_median,
            is_bank=is_b,
            min_market_cap_cr=min_market_cap_cr,
            min_traded_value_cr=min_traded_value_cr,
            overrides=ov_map.get(sym),
        )
        ev2 = evaluate_stock_set2(
            b.snapshot,
            b.financials,
            b.sector_median,
            is_bank=is_b,
            min_market_cap_cr=min_market_cap_cr,
            min_traded_value_cr=min_traded_value_cr,
            overrides=ov_map.get(sym),
        )
        eval_s1_map[sym] = ev1
        eval_s2_map[sym] = ev2

        latest_fy = max(b.financials, key=lambda f: f.fiscal_year) if b.financials else None
        val = compute_relative_valuation(
            b.snapshot, b.sector_median, latest_fy=latest_fy, is_bank=is_b
        )
        val_map[sym] = val

        active_ev = ev1 if rs == "set1" else ev2
        stock_headers.append(
            {
                "symbol": sym,
                "name": b.master.name,
                "sector": b.master.sector,
                "industry": b.master.industry,
                "is_bank": is_b,
                "price": b.snapshot.price,
                "market_cap_cr": b.snapshot.market_cap_cr,
                "passed": active_ev.passed,
                "passed_count": active_ev.passed_count,
                "evaluated_count": active_ev.evaluated_count,
                "composite_score": active_ev.composite_score,
                "verdict": val.verdict,
                "fair_value_low": val.fair_value_low,
                "fair_value_high": val.fair_value_high,
            }
        )

    # Define comparison criteria
    if all_banks:
        # 14-metric Banking Matrix
        specs: list[tuple[str, str, str, str, str, Any]] = [
            ("market_cap_cr", "Market Cap (₹ Cr)", "size", "higher_is_better", "cr", lambda r, b: r.market_cap_cr),
            ("nii_cagr_5y", "5Y NII / Revenue CAGR", "growth", "higher_is_better", "pct_dec", lambda r, b: r.nii_cagr_5y if r.nii_cagr_5y is not None else r.revenue_cagr_5y),
            ("pat_cagr_5y", "5Y PAT CAGR", "growth", "higher_is_better", "pct_dec", lambda r, b: r.pat_cagr_5y),
            ("nim_pct", "Net Interest Margin (NIM %)", "profitability", "higher_is_better", "pct", lambda r, b: r.nim_pct),
            ("gnpa_pct", "Gross NPA (%)", "asset_quality", "lower_is_better", "pct", lambda r, b: r.gnpa_pct),
            ("nnpa_pct", "Net NPA (%)", "asset_quality", "lower_is_better", "pct", lambda r, b: r.nnpa_pct),
            ("casa_pct", "CASA Ratio (%)", "capital_liability", "higher_is_better", "pct", lambda r, b: r.casa_pct),
            ("car_pct", "Capital Adequacy (CAR %)", "capital_liability", "higher_is_better", "pct", lambda r, b: r.car_pct),
            ("roe", "Return on Equity (ROE %)", "profitability", "higher_is_better", "pct_dec", lambda r, b: r.roe),
            ("roa", "Return on Assets (ROA %)", "profitability", "higher_is_better", "pct_dec", lambda r, b: r.roa),
            ("provision_coverage_pct", "Provision Coverage Ratio (%)", "asset_quality", "higher_is_better", "pct", lambda r, b: r.provision_coverage_pct),
            ("credit_cost_pct", "Credit Cost (%)", "asset_quality", "lower_is_better", "pct", lambda r, b: r.credit_cost_pct),
            ("pb", "Price to Book (P/B)", "valuation", "lower_is_better", "x", lambda r, b: r.pb),
            ("pe", "Price to Earnings (P/E)", "valuation", "lower_is_better", "x", lambda r, b: r.pe),
        ]
    else:
        # 18-metric Non-Banking Matrix
        specs = [
            ("market_cap_cr", "Market Cap (₹ Cr)", "size", "higher_is_better", "cr", lambda r, b: r.market_cap_cr),
            ("revenue_cagr_5y", "5Y Revenue CAGR", "growth", "higher_is_better", "pct_dec", lambda r, b: r.revenue_cagr_5y),
            ("pat_cagr_5y", "5Y PAT CAGR", "growth", "higher_is_better", "pct_dec", lambda r, b: r.pat_cagr_5y),
            ("eps_cagr_5y", "5Y EPS CAGR", "growth", "higher_is_better", "pct_dec", lambda r, b: r.eps_cagr_5y),
            ("roe", "Return on Equity (ROE %)", "profitability", "higher_is_better", "pct_dec", lambda r, b: r.roe),
            ("roce", "Return on Capital Employed (ROCE %)", "profitability", "higher_is_better", "pct_dec", lambda r, b: r.roce),
            ("ebitda_margin", "EBITDA Margin (%)", "profitability", "higher_is_better", "pct_dec", lambda r, b: r.ebitda_margin),
            ("pat_margin", "PAT Margin (%)", "profitability", "higher_is_better", "pct_dec", lambda r, b: r.pat_margin),
            ("debt_to_equity", "Debt-to-Equity", "leverage", "lower_is_better", "x", lambda r, b: None if r.is_bank else r.debt_to_equity),
            ("interest_coverage", "Interest Coverage", "leverage", "higher_is_better", "x", lambda r, b: None if r.is_bank else r.interest_coverage),
            ("current_ratio", "Current Ratio", "liquidity", "optimal_range", "x", lambda r, b: None if r.is_bank else r.current_ratio),
            ("cfo_to_pat", "CFO / PAT (5Y)", "efficiency", "higher_is_better", "x", lambda r, b: None if r.is_bank else r.cfo_to_pat),
            ("fcf_cr", "Free Cash Flow (₹ Cr)", "efficiency", "higher_is_better", "cr", lambda r, b: None if r.is_bank else r.fcf_cr),
            ("pe", "P/E Multiple", "valuation", "lower_is_better", "x", lambda r, b: r.pe),
            ("pb", "P/B Multiple", "valuation", "lower_is_better", "x", lambda r, b: r.pb),
            ("ev_ebitda", "EV / EBITDA Multiple", "valuation", "lower_is_better", "x", lambda r, b: None if r.is_bank else r.ev_ebitda),
            ("dividend_yield", "Dividend Yield (%)", "dividend", "higher_is_better", "pct_dec", lambda r, b: r.dividend_yield),
            ("pledge_pct", "Promoter Pledge (%)", "governance", "lower_is_better", "pct", lambda r, b: r.pledge_pct),
        ]

    criteria_matrix: list[StockCriterionComparison] = []
    wins_by_symbol: dict[str, int] = {sym: 0 for sym in symbols}

    for key, label, cat, direction, unit, getter in specs:
        vals: dict[str, float | None] = {}
        fmts: dict[str, str] = {}
        for b in bundles:
            sym = b.master.symbol
            r = ratios_map[sym]
            v = getter(r, b)
            vals[sym] = round(v, 4) if isinstance(v, float) else v
            # Add sector context for valuation multiples when available
            base_fmt = _fmt_val(v, unit)
            if key == "pe" and b.sector_median and b.sector_median.median_pe and v is not None:
                base_fmt = f"{v:.1f}x (Sec {b.sector_median.median_pe:.1f}x)"
            elif key == "pb" and b.sector_median and b.sector_median.median_pb and v is not None:
                base_fmt = f"{v:.1f}x (Sec {b.sector_median.median_pb:.1f}x)"
            elif key == "ev_ebitda" and b.sector_median and b.sector_median.median_ev_ebitda and v is not None:
                base_fmt = f"{v:.1f}x (Sec {b.sector_median.median_ev_ebitda:.1f}x)"
            elif key == "pledge_pct" and v is not None and r.promoter_holding_pct is not None:
                base_fmt = f"Pledge {v:.1f}% (Hold {r.promoter_holding_pct:.1f}%)"
            fmts[sym] = base_fmt

        winner, ties = _pick_winner(vals, direction)
        if winner is not None:
            wins_by_symbol[winner] = wins_by_symbol.get(winner, 0) + 1

        criteria_matrix.append(
            StockCriterionComparison(
                key=key,
                label=label,
                category=cat,
                direction=direction,
                unit=unit,
                values=vals,
                formatted=fmts,
                winner_symbol=winner,
                tied_symbols=ties,
            )
        )

    # Determine overall winner: prefer stocks that pass the active rule set, then criterion wins, then composite score
    active_eval_map = eval_s1_map if rs == "set1" else eval_s2_map
    ranked_syms = sorted(
        symbols,
        key=lambda s: (
            1 if active_eval_map[s].passed else 0,
            wins_by_symbol.get(s, 0),
            active_eval_map[s].composite_score,
        ),
        reverse=True,
    )
    overall_winner = ranked_syms[0] if ranked_syms else None

    return StockComparisonReport(
        symbols=symbols,
        rule_set=rs,
        matrix_type=matrix_type,
        cross_sector_warning=cross_sector_warning,
        stocks=stock_headers,
        criteria_matrix=criteria_matrix,
        wins_by_symbol=wins_by_symbol,
        overall_winner=overall_winner,
        evaluations={s: active_eval_map[s].to_dict() for s in symbols},
        evaluations_set1={s: eval_s1_map[s].to_dict() for s in symbols},
        evaluations_set2={s: eval_s2_map[s].to_dict() for s in symbols},
        valuations={s: val_map[s].to_dict() for s in symbols},
        normalized_price_series=_build_normalized_prices(bundles),
        financial_growth_series=_build_financial_growth_series(bundles),
        narrative="",
    )
