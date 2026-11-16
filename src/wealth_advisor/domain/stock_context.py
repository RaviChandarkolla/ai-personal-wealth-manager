"""EIC (Economy-Industry-Company) context, Porter's 5 Forces, 5C framework & single-stock deep dive."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from wealth_advisor.data.stocks.provider import (
    StockFinancialYear,
    StockMasterItem,
    StockPricePoint,
    StockSectorMedian,
    StockSnapshot,
)
from wealth_advisor.domain.stock_ratios import (
    compute_stock_ratios,
    compute_year_ratios,
)
from wealth_advisor.domain.stock_rulesets import (
    evaluate_stock_set1,
    evaluate_stock_set2,
)
from wealth_advisor.domain.stock_valuation import compute_relative_valuation

_SECTOR_PORTER_TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "Information Technology": {
        "threat_of_new_entrants": {
            "rating": "Low to Moderate",
            "note": "Enterprise scale, global delivery track record, and multi-year client relationships create high entry barriers.",
        },
        "bargaining_power_of_buyers": {
            "rating": "Moderate to High",
            "note": "Fortune 500 clients consolidate vendors and negotiate pricing on large cloud/AI transformation deals.",
        },
        "bargaining_power_of_suppliers": {
            "rating": "Moderate",
            "note": "Key input is skilled engineering talent; wage inflation and attrition cycles influence operating margins.",
        },
        "threat_of_substitutes": {
            "rating": "Moderate",
            "note": "GenAI automation and SaaS platforms shift billing from headcount to outcome-based models.",
        },
        "competitive_rivalry": {
            "rating": "High",
            "note": "Intense competition among Tier-1 Indian IT majors and global consultancies for large deals.",
        },
    },
    "Financial Services": {
        "threat_of_new_entrants": {
            "rating": "Low",
            "note": "Strict RBI licensing, capital adequacy norms, and nationwide branch/CASA networks protect incumbents.",
        },
        "bargaining_power_of_buyers": {
            "rating": "Moderate",
            "note": "Prime corporate and retail borrowers shop across banks for lower lending rates; sticky CASA customers switch less.",
        },
        "bargaining_power_of_suppliers": {
            "rating": "Moderate",
            "note": "Depositors and wholesale debt markets supply funds; banks with high CASA (>40%) enjoy lower cost of funds.",
        },
        "threat_of_substitutes": {
            "rating": "Moderate",
            "note": "Fintechs, mutual funds (financialization of savings), and corporate bond markets compete for deposits and credit.",
        },
        "competitive_rivalry": {
            "rating": "High",
            "note": "Strong rivalry among top private and PSU banks for retail deposits and high-quality credit growth.",
        },
    },
    "Fast Moving Consumer Goods": {
        "threat_of_new_entrants": {
            "rating": "Moderate",
            "note": "D2C brands can enter online niches, but general-trade kirana distribution across India remains a moat.",
        },
        "bargaining_power_of_buyers": {
            "rating": "Moderate",
            "note": "Low ticket sizes and brand loyalty support pricing power, though quick-commerce platforms have rising leverage.",
        },
        "bargaining_power_of_suppliers": {
            "rating": "Low to Moderate",
            "note": "Large FMCG players source agricultural/chemical inputs at scale, though commodity spikes impact gross margins.",
        },
        "threat_of_substitutes": {
            "rating": "Moderate",
            "note": "Private labels and regional value brands compete during rural demand slowdowns.",
        },
        "competitive_rivalry": {
            "rating": "High",
            "note": "Intense share-of-shelf competition across personal care, foods, and home care segments.",
        },
    },
    "Automobile and Auto Components": {
        "threat_of_new_entrants": {
            "rating": "Low",
            "note": "High capex, R&D, dealer service networks, and safety/emission regulations deter new OEMs.",
        },
        "bargaining_power_of_buyers": {
            "rating": "Moderate",
            "note": "Consumers compare feature sets, SUV styling, and total cost of ownership across OEMs.",
        },
        "bargaining_power_of_suppliers": {
            "rating": "Low to Moderate",
            "note": "Large OEMs command strong terms over tier-1/tier-2 component suppliers, except in specialized EV electronics.",
        },
        "threat_of_substitutes": {
            "rating": "Moderate",
            "note": "Shared mobility, metro expansion, and EV powertrain transitions reshape demand.",
        },
        "competitive_rivalry": {
            "rating": "High",
            "note": "Aggressive model launches in SUVs, EVs, and premium two-wheelers.",
        },
    },
    "Healthcare": {
        "threat_of_new_entrants": {
            "rating": "Low to Moderate",
            "note": "USFDA/regulatory approvals, ANDA pipelines, and complex manufacturing facilities create strong barriers.",
        },
        "bargaining_power_of_buyers": {
            "rating": "Moderate to High",
            "note": "US pharmacy benefit managers (PBMs) exert pricing pressure on generics; domestic branded formulations have sticky doctor prescriptions.",
        },
        "bargaining_power_of_suppliers": {
            "rating": "Moderate",
            "note": "API/KSM sourcing concentration is mitigated by backward integration and PLI schemes.",
        },
        "threat_of_substitutes": {
            "rating": "Low",
            "note": "Essential chronic and acute therapies have limited non-medical substitutes.",
        },
        "competitive_rivalry": {
            "rating": "Moderate to High",
            "note": "High in plain-vanilla generics; lower in specialty pharma, inhalers, and biosimilars.",
        },
    },
}

_DEFAULT_PORTER: dict[str, dict[str, str]] = {
    "threat_of_new_entrants": {
        "rating": "Low to Moderate",
        "note": "Capital intensity, regulatory clearances, and operating scale favour established sector leaders.",
    },
    "bargaining_power_of_buyers": {
        "rating": "Moderate",
        "note": "Institutional and retail customers evaluate quality, reliability, and total cost of ownership.",
    },
    "bargaining_power_of_suppliers": {
        "rating": "Moderate",
        "note": "Input cost pass-through depends on contract structures and brand/infrastructure moat.",
    },
    "threat_of_substitutes": {
        "rating": "Low to Moderate",
        "note": "Core infrastructure, energy, and telecom services face gradual technological evolution.",
    },
    "competitive_rivalry": {
        "rating": "Moderate",
        "note": "Consolidated industry structure with disciplined pricing among top players.",
    },
}


def load_macro_snapshot() -> dict[str, Any]:
    """Load the curated Indian macro snapshot JSON."""
    try:
        ref = resources.files("wealth_advisor.data").joinpath("macro_snapshot.json")
        text = ref.read_text(encoding="utf-8")
        return dict(json.loads(text))
    except (OSError, ValueError, TypeError):
        return {
            "as_of_date": "2026-03-31",
            "country": "India",
            "currency": "INR",
            "indicators": {},
        }


def build_eic_context(
    master: StockMasterItem,
    snapshot: StockSnapshot,
    sector_median: StockSectorMedian | None,
) -> dict[str, Any]:
    """Build Economy-Industry-Company (EIC) qualitative framework card (FA Course Notes Sec 2)."""
    macro = load_macro_snapshot()
    porter = _SECTOR_PORTER_TEMPLATES.get(master.sector, _DEFAULT_PORTER)

    prom_pct = snapshot.promoter_holding_pct or 0.0
    pledge_pct = snapshot.pledge_pct or 0.0
    roe_pct = (snapshot.roe or 0.0) * 100.0
    de_val = snapshot.debt_to_equity or 0.0

    five_c = {
        "character": (
            f"Promoter holding at {prom_pct:.1f}% with {pledge_pct:.1f}% pledge and "
            f"{snapshot.auditor_flags} auditor flag(s)."
        ),
        "capacity": (
            f"Generates ROE of {roe_pct:.1f}% with market cap of ₹{(snapshot.market_cap_cr or 0.0):,.0f} Cr "
            f"in {master.industry}."
        ),
        "capital": (
            "Regulated banking capital structure evaluated via CAR & CASA."
            if master.is_bank
            else f"Debt-to-Equity of {de_val:.2f}x and Interest Coverage of {(snapshot.interest_coverage or 0.0):.1f}x."
        ),
        "collateral": (
            "Strong balance sheet asset backing and liquid cash reserves."
            if pledge_pct <= 5.0
            else f"Elevated promoter share pledge ({pledge_pct:.1f}%) warrants monitoring."
        ),
        "conditions": (
            f"Operating in {master.sector} ({master.industry}) with sector median P/E of "
            f"{(sector_median.median_pe if sector_median and sector_median.median_pe else 0.0):.1f}x."
        ),
    }

    return {
        "economy": macro,
        "industry": {
            "sector": master.sector,
            "industry": master.industry,
            "is_bank": master.is_bank,
            "peer_count": sector_median.peer_count if sector_median else 0,
            "sector_medians": sector_median.to_dict() if sector_median else None,
            "porters_five_forces": porter,
        },
        "company_5c": five_c,
    }


def build_financial_statements_explorer(
    financials: list[StockFinancialYear],
) -> list[dict[str, Any]]:
    """Build 5-year P&L, Balance Sheet, Cash Flow, and YoY growth table for `Analyse Stock`."""
    ordered = sorted(financials, key=lambda f: f.fiscal_year)
    rows: list[dict[str, Any]] = []
    for idx, fy in enumerate(ordered):
        prev = ordered[idx - 1] if idx > 0 else None
        yr_ratios = compute_year_ratios(fy)

        rev_yoy: float | None = None
        if (
            prev is not None
            and fy.revenue_cr is not None
            and prev.revenue_cr is not None
            and prev.revenue_cr > 0
        ):
            rev_yoy = (fy.revenue_cr - prev.revenue_cr) / prev.revenue_cr

        pat_yoy: float | None = None
        if (
            prev is not None
            and fy.pat_cr is not None
            and prev.pat_cr is not None
            and prev.pat_cr > 0
        ):
            pat_yoy = (fy.pat_cr - prev.pat_cr) / prev.pat_cr

        eps_yoy: float | None = None
        if (
            prev is not None
            and fy.eps is not None
            and prev.eps is not None
            and prev.eps > 0
        ):
            eps_yoy = (fy.eps - prev.eps) / prev.eps

        rows.append(
            {
                "fiscal_year": fy.fiscal_year,
                "label": f"FY{str(fy.fiscal_year)[-2:]}",
                "revenue_cr": fy.revenue_cr,
                "revenue_yoy": rev_yoy,
                "ebitda_cr": fy.ebitda_cr,
                "ebit_cr": fy.ebit_cr,
                "interest_cr": fy.interest_cr,
                "pbt_cr": fy.pbt_cr,
                "tax_cr": fy.tax_cr,
                "pat_cr": fy.pat_cr,
                "pat_yoy": pat_yoy,
                "eps": fy.eps,
                "eps_yoy": eps_yoy,
                "dps": fy.dps,
                "bvps": fy.bvps,
                "total_assets_cr": fy.total_assets_cr,
                "net_worth_cr": fy.net_worth_cr,
                "total_debt_cr": fy.total_debt_cr,
                "current_assets_cr": fy.current_assets_cr,
                "current_liabilities_cr": fy.current_liabilities_cr,
                "working_capital_cr": yr_ratios.get("working_capital_cr"),
                "inventory_cr": fy.inventory_cr,
                "receivables_cr": fy.receivables_cr,
                "payables_cr": fy.payables_cr,
                "cash_and_equivalents_cr": fy.cash_and_equivalents_cr,
                "cfo_cr": fy.cfo_cr,
                "capex_cr": fy.capex_cr,
                "fcf_cr": yr_ratios.get("fcf_cr"),
                "dividends_paid_cr": fy.dividends_paid_cr,
                "nii_cr": fy.nii_cr,
                "nim_pct": fy.nim_pct,
                "gnpa_pct": fy.gnpa_pct,
                "nnpa_pct": fy.nnpa_pct,
                "casa_pct": fy.casa_pct,
                "car_pct": fy.car_pct,
                "credit_cost_pct": fy.credit_cost_pct,
                "provision_coverage_pct": fy.provision_coverage_pct,
                "deposits_cr": fy.deposits_cr,
                "advances_cr": fy.advances_cr,
                "current_ratio": yr_ratios.get("current_ratio"),
                "cfo_to_pat": yr_ratios.get("cfo_to_pat"),
            }
        )
    return rows


def _apply_bundle_overrides(
    snapshot: StockSnapshot,
    financials: list[StockFinancialYear],
    overrides: dict[str, float] | None,
) -> tuple[StockSnapshot, list[StockFinancialYear]]:
    if not overrides:
        return snapshot, financials
    snap_updates: dict[str, Any] = {}
    if "fii_holding_pct" in overrides:
        snap_updates["fii_holding_pct"] = float(overrides["fii_holding_pct"])
    if "promoter_pledge_pct" in overrides:
        snap_updates["pledge_pct"] = float(overrides["promoter_pledge_pct"])
    if "pledge_pct" in overrides:
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
    if fy_updates and financials:
        ordered = sorted(financials, key=lambda f: f.fiscal_year)
        ordered[-1] = ordered[-1].model_copy(update=fy_updates)
        return snap_out, ordered
    return snap_out, financials


def build_single_stock_analysis(
    *,
    master: StockMasterItem,
    snapshot: StockSnapshot,
    financials: list[StockFinancialYear],
    prices: list[StockPricePoint],
    sector_median: StockSectorMedian | None,
    peers: list[dict[str, Any]] | None = None,
    sector_peers: list[dict[str, Any]] | None = None,
    rule_set: str = "set2",
    min_market_cap_cr: float | None = None,
    min_traded_value_cr: float | None = None,
    valuation_weights: dict[str, float] | None = None,
    stale_after_days: int = 7,
    overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Assemble the complete 8-section deep-dive payload for `/api/stocks/analyze/{symbol}`."""
    is_bank = master.is_bank
    snapshot, financials = _apply_bundle_overrides(snapshot, financials, overrides)
    peer_list = peers if peers is not None else (sector_peers or [])
    ratios = compute_stock_ratios(snapshot, financials, is_bank=is_bank)
    ordered_fy = sorted(financials, key=lambda f: f.fiscal_year)
    latest_fy = ordered_fy[-1] if ordered_fy else None

    eval_s1 = evaluate_stock_set1(
        snapshot,
        financials,
        sector_median,
        is_bank=is_bank,
        min_market_cap_cr=min_market_cap_cr,
        min_traded_value_cr=min_traded_value_cr,
        overrides=overrides,
    )
    eval_s2 = evaluate_stock_set2(
        snapshot,
        financials,
        sector_median,
        is_bank=is_bank,
        min_market_cap_cr=min_market_cap_cr,
        min_traded_value_cr=min_traded_value_cr,
        overrides=overrides,
    )
    active_eval = eval_s1 if rule_set == "set1" else eval_s2

    valuation = compute_relative_valuation(
        snapshot,
        sector_median,
        latest_fy=latest_fy,
        is_bank=is_bank,
        weights=valuation_weights,
    )

    statements = build_financial_statements_explorer(financials)
    eic = build_eic_context(master, snapshot, sector_median)

    overview_dict = {
        "symbol": master.symbol,
        "isin": master.isin,
        "name": master.name,
        "exchange": master.exchange,
        "sector": master.sector,
        "industry": master.industry,
        "is_bank": is_bank,
        "price": snapshot.price,
        "market_cap_cr": snapshot.market_cap_cr,
        "avg_daily_traded_value_cr": snapshot.avg_daily_traded_value_cr,
        "return_1y": snapshot.return_1y,
        "volatility_1y": snapshot.volatility_1y,
        "max_drawdown_1y": snapshot.max_drawdown_1y,
        "as_of_date": snapshot.as_of_date,
        "source": snapshot.source,
        "stale_after_days": stale_after_days,
    }

    return {
        "symbol": master.symbol,
        "stock": overview_dict,
        "overview": overview_dict,
        "active_rule_set": rule_set if rule_set in ("set1", "set2") else "set2",
        "active_screen": active_eval.to_dict(),
        "screening": {
            "active": active_eval.to_dict(),
            "set1": eval_s1.to_dict(),
            "set2": eval_s2.to_dict(),
        },
        "financial_statements": statements,
        "ratios": ratios.to_dict(),
        "valuation": valuation.to_dict(),
        "peers": peer_list,
        "eic": eic,
        "price_history": [
            {"date": p.price_date, "close": p.close, "volume": p.volume}
            for p in sorted(prices, key=lambda x: x.price_date)
        ],
        "narrative": "",
    }
