"""Stock Researcher Agent — screens & ranks synced NSE stocks for the Direct Equity sleeve."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from ..config import Settings, get_settings
from ..data.stocks.sync import StockSyncService
from ..domain.momentum import compute_momentum, regime_from_breadth
from ..domain.scoring import score_instrument
from ..domain.stock_ratios import compute_stock_ratios
from ..domain.stock_rulesets import evaluate_stock
from ..guardrails.suitability import Violation
from ..models.core import (
    AnalystVerdict,
    Citation,
    DossierEntry,
    Fact,
    Goal,
    Instrument,
)
from ..models.enums import Bucket, RiskCategory


@dataclass
class StockResearchResult:
    """Output of StockResearcherAgent.run()."""

    entries: list[DossierEntry] = field(default_factory=list)
    verdicts: list[AnalystVerdict] = field(default_factory=list)
    rejected: list[Violation] = field(default_factory=list)
    sector_by_symbol: dict[str, str] = field(default_factory=dict)
    sync_warning: str | None = None
    stale_warning: str | None = None
    warnings: list[str] = field(default_factory=list)
    sync_as_of: str | None = None
    filters_applied: dict[str, float | None] = field(default_factory=dict)
    research_log_entry: dict = field(default_factory=dict)
    trend_regime: dict = field(default_factory=dict)


class StockResearcherAgent:
    """Reads pre-synced stocks from SQLite and produces grounded DossierEntries + AnalystVerdicts."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        data_mode: str = "synthetic",
        stale_after_days: int = 7,
    ) -> None:
        self.conn = conn
        self.data_mode = data_mode
        self.stale_after_days = stale_after_days
        self.sync_service = StockSyncService(
            conn, data_mode=data_mode, stale_after_days=stale_after_days
        )

    def run(
        self,
        *,
        goal: Goal,
        risk_category: RiskCategory,
        as_of: date,
        rule_set: str = "set2",
        min_market_cap_cr: float | None = None,
        min_traded_value_cr: float | None = None,
        max_candidates: int = 8,
        trend_based: bool = False,
        waive_context_rules: bool = False,
        s: Settings | None = None,
    ) -> StockResearchResult:
        """Rank passing synced stocks for the Direct Equity sleeve.

        `waive_context_rules` skips the horizon and risk-profile gates when the user asked for stocks.
        """
        s = s or get_settings()
        rs = rule_set if rule_set in ("set1", "set2") else "set2"
        filters_applied = {
            "min_market_cap_cr": min_market_cap_cr,
            "min_traded_value_cr": min_traded_value_cr,
        }

        if not waive_context_rules and goal.horizon_years < s.stock_min_horizon_years:
            reason = (
                f"Direct stocks skipped: goal horizon {goal.horizon_years:g}y is below "
                f"the {s.stock_min_horizon_years:g}y minimum for direct equity."
            )
            return StockResearchResult(
                warnings=[reason],
                filters_applied=filters_applied,
                research_log_entry={
                    "bucket": Bucket.EQUITY.value,
                    "output_field": "equity_stocks",
                    "stock_rule_set": rs,
                    "skipped_reason": "horizon_below_minimum",
                    "detail": reason,
                },
            )

        sleeve_pct = s.stock_sleeve_pct_of_equity.get(risk_category.value, 0.0)
        if not waive_context_rules and sleeve_pct <= 0:
            reason = (
                f"Direct stocks skipped: {risk_category.value} risk profile allocates 0% "
                "to the direct equity sleeve."
            )
            return StockResearchResult(
                warnings=[reason],
                filters_applied=filters_applied,
                research_log_entry={
                    "bucket": Bucket.EQUITY.value,
                    "output_field": "equity_stocks",
                    "stock_rule_set": rs,
                    "skipped_reason": "zero_sleeve_for_risk_profile",
                    "detail": reason,
                },
            )

        if self.data_mode == "synthetic":
            self.sync_service.ensure_seeded()
        status = self.sync_service.get_status()
        if not status.get("has_synced_data"):
            warning = (
                "stock_sync_required: No synced stock data found in local store. "
                "Run 'Sync Stocks' to populate direct equity candidates; "
                "equity sleeve weight reallocated to Mutual Funds."
            )
            return StockResearchResult(
                entries=[],
                verdicts=[],
                rejected=[],
                sector_by_symbol={},
                sync_warning=warning,
                warnings=[warning],
                filters_applied=filters_applied,
                research_log_entry={
                    "bucket": Bucket.EQUITY.value,
                    "output_field": "equity_stocks",
                    "no_data": "stock_sync_required",
                    "skipped_reason": "stock_data_not_synced",
                },
            )

        warnings: list[str] = []
        latest_run = status.get("latest_run") or {}
        sync_as_of = latest_run.get("finished_at") or latest_run.get("started_at")
        stale_warning: str | None = None
        if status.get("is_stale"):
            stale_warning = (
                f"Stock data is stale (last synced {sync_as_of or 'unknown'}, "
                f"older than {self.stale_after_days} days). Consider running a fresh sync."
            )
            warnings.append(stale_warning)

        sync_min_mcap = latest_run.get("min_market_cap_cr")
        if sync_min_mcap is not None and float(sync_min_mcap) > 0:
            adv_min = float(min_market_cap_cr) if min_market_cap_cr is not None else 0.0
            if float(sync_min_mcap) > adv_min:
                warnings.append(
                    f"Stocks were synced with a ₹{float(sync_min_mcap):g} Cr minimum; "
                    "smaller companies are unavailable. Run a sync without a minimum to include them."
                )

        rows = self.conn.execute(
            "SELECT symbol FROM stock_master WHERE is_active = 1 ORDER BY symbol"
        ).fetchall()

        candidates: list[tuple[float, DossierEntry, AnalystVerdict, str]] = []
        rejected: list[Violation] = []
        sector_by_symbol: dict[str, str] = {}
        notes: dict[str, str] = {}
        dropped: list[dict[str, str]] = []
        above_flags: list[bool] = []

        for r in rows:
            sym = r["symbol"]
            bundle = self.sync_service.get_stock_bundle(sym)
            if bundle is None:
                continue

            sector = bundle.master.sector or "Diversified"
            sector_by_symbol[sym] = sector
            ratios = compute_stock_ratios(
                bundle.snapshot, bundle.financials, is_bank=bundle.master.is_bank
            )
            ev = evaluate_stock(
                bundle.snapshot,
                bundle.financials,
                bundle.sector_median,
                rule_set=rs,
                is_bank=bundle.master.is_bank,
                min_market_cap_cr=min_market_cap_cr,
                min_traded_value_cr=min_traded_value_cr,
                risk_profile=risk_category.value,
                horizon_years=goal.horizon_years,
            )

            if not ev.passed:
                failed_names = [c.name for c in ev.criteria if c.status == "failed"]
                reasons: list[str] = []
                if ev.red_flags:
                    reasons.extend(ev.red_flags)
                if failed_names:
                    reasons.append(
                        f"Failed {rs.upper()} screen ({ev.passed_count}/{ev.evaluated_count}): "
                        + ", ".join(failed_names[:3])
                    )
                if not reasons:
                    reasons.append(
                        f"Insufficient criteria coverage ({ev.evaluated_count}/{ev.min_required_evaluated})"
                    )
                detail = "; ".join(reasons)
                rejected.append(
                    Violation(instrument_id=sym, rule=f"stock_screen_{rs}", detail=detail)
                )
                dropped.append({"id": sym, "name": bundle.master.name, "reason": detail})
                continue

            try:
                snap_date = date.fromisoformat(bundle.snapshot.as_of_date[:10])
            except ValueError:
                snap_date = as_of

            src_tag = (
                "synthetic_demo"
                if bundle.snapshot.source == "synthetic"
                else f"stock_{bundle.snapshot.source}"
            )
            cite = Citation(
                source=src_tag,
                as_of=snap_date,
                url_or_doc_id=f"nse:{sym}",
                snippet=f"{bundle.master.name} ({sym}) {rs.upper()} screen {ev.passed_count}/{ev.evaluated_count}",
            )

            roe_pct = round((ratios.roe or 0.0) * 100.0, 1)
            pe_val = round(ratios.pe or 0.0, 1)
            de_val = round(ratios.debt_to_equity or 0.0, 2)
            pledge_val = round(ratios.pledge_pct or 0.0, 1)
            dd_pct = round((ratios.max_drawdown_1y or -0.12) * 100.0, 1)
            cagr_5y_pct = round(
                (ratios.pat_cagr_5y if ratios.pat_cagr_5y is not None else (ratios.earnings_growth_yoy or 0.12))
                * 100.0,
                1,
            )
            ordered_fy = sorted(bundle.financials, key=lambda x: x.fiscal_year)
            latest_nw = (
                ordered_fy[-1].net_worth_cr
                if ordered_fy and ordered_fy[-1].net_worth_cr is not None
                else 1000.0
            )
            ic_val = round(ratios.interest_coverage if ratios.interest_coverage is not None else 10.0, 2)
            nnpa_val = round(ratios.nnpa_pct if ratios.nnpa_pct is not None else 0.0, 2)

            facts = [
                Fact(id=f"{sym}:roe_pct", name="roe_pct", value=roe_pct, citation=cite),
                Fact(id=f"{sym}:pe", name="pe", value=pe_val, citation=cite),
                Fact(id=f"{sym}:debt_to_equity", name="debt_to_equity", value=de_val, citation=cite),
                Fact(
                    id=f"{sym}:promoter_pledge_pct",
                    name="promoter_pledge_pct",
                    value=pledge_val,
                    citation=cite,
                ),
                Fact(id=f"{sym}:max_drawdown", name="max_drawdown", value=dd_pct, citation=cite),
                Fact(id=f"{sym}:cagr_5y", name="cagr_5y", value=cagr_5y_pct, citation=cite),
                Fact(id=f"{sym}:passed_count", name="passed_count", value=ev.passed_count, citation=cite),
                Fact(id=f"{sym}:evaluated_count", name="evaluated_count", value=ev.evaluated_count, citation=cite),
                Fact(id=f"{sym}:checks_passed", name="checks_passed", value=ev.passed_count, citation=cite),
                Fact(id=f"{sym}:checks_total", name="checks_total", value=ev.evaluated_count, citation=cite),
                Fact(
                    id=f"{sym}:market_cap_cr",
                    name="market_cap_cr",
                    value=round(float(bundle.snapshot.market_cap_cr or 0.0), 2),
                    citation=cite,
                ),
                Fact(
                    id=f"{sym}:auditor_flags",
                    name="auditor_flags",
                    value=int(ratios.auditor_flags),
                    citation=cite,
                ),
                Fact(
                    id=f"{sym}:net_worth_cr",
                    name="net_worth_cr",
                    value=float(latest_nw),
                    citation=cite,
                ),
                Fact(
                    id=f"{sym}:interest_coverage",
                    name="interest_coverage",
                    value=ic_val,
                    citation=cite,
                ),
                Fact(id=f"{sym}:nnpa_pct", name="nnpa_pct", value=nnpa_val, citation=cite),
                Fact(
                    id=f"{sym}:is_bank",
                    name="is_bank",
                    value=1 if bundle.master.is_bank else 0,
                    citation=cite,
                ),
                Fact(
                    id=f"{sym}:screen_passed",
                    name="screen_passed",
                    value=1 if ev.passed else 0,
                    citation=cite,
                ),
                Fact(
                    id=f"{sym}:stock_rule_set",
                    name="stock_rule_set",
                    value=rs,
                    citation=cite,
                ),
                Fact(id=f"{sym}:sector", name="sector", value=sector, citation=cite),
                Fact(id=f"{sym}:plan", name="plan", value="direct", citation=cite),
            ]

            aligned_score = score_instrument(ev.sub_scores, s.analyst_weights)
            sub_scores = dict(ev.sub_scores)
            trend_pros: list[str] = []
            trend_cons: list[str] = []
            mom = (
                compute_momentum([(p.price_date, p.close) for p in self.sync_service.get_prices(sym)])
                if trend_based
                else None
            )
            if mom is not None:
                w = s.trend_momentum_weight
                aligned_score = round((1 - w) * aligned_score + w * mom.score, 4)
                sub_scores["momentum"] = mom.score
                for name, val in (
                    ("momentum_score", mom.score),
                    ("ret_3m_pct", mom.ret_3m_pct),
                    ("ret_6m_pct", mom.ret_6m_pct),
                    ("ret_12m_pct", mom.ret_12m_pct),
                    ("above_200dma", None if mom.above_200dma is None else int(mom.above_200dma)),
                ):
                    if val is not None:
                        facts.append(Fact(id=f"{sym}:{name}", name=name, value=val, citation=cite))
                if mom.above_200dma is not None:
                    above_flags.append(mom.above_200dma)
                if mom.score >= 60:
                    trend_pros.append(f"Price trend is positive (momentum {mom.score:g}/100) [{sym}:momentum_score]")
                elif mom.score <= 40:
                    trend_cons.append(f"Price trend is weak (momentum {mom.score:g}/100) [{sym}:momentum_score]")
            entry = DossierEntry(
                instrument=Instrument(
                    id=sym,
                    name=bundle.master.name,
                    asset_class=Bucket.EQUITY,
                    category="Direct Equity",
                    plan_type="direct",
                ),
                facts=facts,
            )
            verdict = AnalystVerdict(
                instrument_id=sym,
                pros=[
                    f"Return on equity is {roe_pct}% with P/E at {pe_val} [{sym}:roe_pct, {sym}:pe]"
                ] + trend_pros,
                cons=[
                    f"Maximum drawdown over the past year was {dd_pct}% [{sym}:max_drawdown]"
                ] + trend_cons,
                score=aligned_score,
                sub_scores=sub_scores,
                confidence=0.85,
                change_conditions=[
                    f"Re-evaluate if promoter pledge rises above {pledge_val}% [{sym}:promoter_pledge_pct]"
                ],
            )
            notes[sym] = (
                f"Passed {rs.upper()} stock screen ({ev.passed_count}/{ev.evaluated_count}) "
                f"in {sector} with ROE {roe_pct}% and P/E {pe_val}x."
            )
            candidates.append((aligned_score, entry, verdict, sector))

        # Sort by score descending, diversify across sectors (at most 2 per sector in initial pass)
        candidates.sort(key=lambda x: (-x[0], x[1].instrument.id))

        sel_entries: list[DossierEntry] = []
        sel_verdicts: list[AnalystVerdict] = []
        sector_counts: dict[str, int] = {}
        overflow: list[tuple[DossierEntry, AnalystVerdict]] = []

        for _, entry, verdict, sec in candidates:
            if len(sel_entries) >= max_candidates:
                break
            cnt = sector_counts.get(sec, 0)
            if cnt < 2:
                sel_entries.append(entry)
                sel_verdicts.append(verdict)
                sector_counts[sec] = cnt + 1
            else:
                overflow.append((entry, verdict))

        for entry, verdict in overflow:
            if len(sel_entries) >= max_candidates:
                break
            sel_entries.append(entry)
            sel_verdicts.append(verdict)

        trend_regime: dict = {}
        if trend_based:
            label, shift = regime_from_breadth(above_flags, s.trend_sleeve_shift_pct)
            trend_regime = {
                "regime": label,
                "sleeve_shift_pct": shift,
                "breadth_pct": round(100.0 * sum(above_flags) / len(above_flags), 1) if above_flags else None,
                "stocks_measured": len(above_flags),
            }

        return StockResearchResult(
            entries=sel_entries,
            verdicts=sel_verdicts,
            rejected=rejected,
            sector_by_symbol=sector_by_symbol,
            sync_warning=None,
            stale_warning=stale_warning,
            warnings=warnings,
            sync_as_of=sync_as_of,
            filters_applied=filters_applied,
            trend_regime=trend_regime,
            research_log_entry={
                "bucket": Bucket.EQUITY.value,
                "output_field": "equity_stocks",
                "stock_rule_set": rs,
                "filters_applied": filters_applied,
                "notes": notes,
                "dropped": dropped,
                "funnel_stats": {
                    "universe": len(rows),
                    "passed_screen": len(candidates),
                    "shortlisted": len(sel_entries),
                },
            },
        )
