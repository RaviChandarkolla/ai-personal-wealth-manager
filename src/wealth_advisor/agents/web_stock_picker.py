"""Direct-stock selection for AI-notes portfolios: web trend view picks + screened-list fill."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from ..config import Settings
from ..data.trend_view import TrendStock, TrendView, trend_view_status
from ..models.core import (
    AnalystVerdict,
    Citation,
    DossierEntry,
    Fact,
    Goal,
    Instrument,
    PortfolioSpec,
)
from ..models.enums import Bucket, RiskCategory
from .stock_researcher import StockResearcherAgent

WEB_CONFIDENCE = 0.7


@dataclass
class StockSelection:
    entries: list[DossierEntry] = field(default_factory=list)
    verdicts: list[AnalystVerdict] = field(default_factory=list)
    web_symbols: list[str] = field(default_factory=list)
    screened_symbols: list[str] = field(default_factory=list)
    shortfalls: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    research_log: list[dict] = field(default_factory=list)
    sync_as_of: str | None = None


def _web_entry(st: TrendStock, sector_outlook: str, view: TrendView) -> tuple[DossierEntry, AnalystVerdict]:
    first = st.sources[0]
    cite = Citation(
        source="web_trend_view", as_of=view.as_of,
        url_or_doc_id=first.url or f"nse:{st.symbol}", snippet=first.title[:200],
    )
    sym = st.symbol
    facts = [
        Fact(id=f"{sym}:sector", name="sector", value=st.sector, citation=cite),
        Fact(id=f"{sym}:sector_outlook", name="sector_outlook", value=sector_outlook, citation=cite),
        Fact(id=f"{sym}:web_trend_score", name="web_trend_score", value=st.score, citation=cite),
        Fact(id=f"{sym}:plan", name="plan", value="direct", citation=cite),
    ]
    entry = DossierEntry(
        instrument=Instrument(
            id=sym, name=st.name, asset_class=Bucket.EQUITY, category="Direct Equity",
            plan_type="direct",
        ),
        facts=facts,
    )
    verdict = AnalystVerdict(
        instrument_id=sym,
        pros=[
            f"In the news as a likely beneficiary of the {st.sector} sector outlook, "
            f"trend score {st.score:g} [{sym}:web_trend_score, {sym}:sector_outlook]",
            f"News: {first.title} ({first.publisher or 'Google News'})",
        ],
        cons=[f"Chosen from sector and news research, not fundamentally screened [{sym}:web_trend_score]"],
        score=st.score,
        # Replay recomputes score from the five standard sub-scores; a web-trend pick has one signal.
        sub_scores={
            "fundamental": st.score, "fit": st.score, "news": st.score,
            "sentiment": st.score, "liquidity_cost": st.score, "web_trend": st.score,
        },
        confidence=WEB_CONFIDENCE,
        change_conditions=[f"Re-evaluate if the {st.sector} sector outlook turns negative [{sym}:sector_outlook]"],
    )
    return entry, verdict


def select_stocks(
    spec: PortfolioSpec,
    goal: Goal,
    conn: sqlite3.Connection,
    *,
    view: TrendView | None,
    data_mode: str,
    risk_category: RiskCategory,
    as_of: date,
    stock_rule_set: str,
    min_market_cap_cr: float | None,
    min_traded_value_cr: float | None,
    s: Settings,
) -> StockSelection:
    """`trendy_stock_count` stocks from the web view (best score first); the rest from the screened list."""
    out = StockSelection()
    total = spec.stock_count or 0
    want_web = spec.trendy_stock_count or 0
    status = trend_view_status(view, s)

    if want_web:
        if view is None or not view.stocks:
            out.warnings.append(
                "No web trend view is saved yet; use 'Refresh trend view' in the stock panel. "
                "The requested trendy stocks were filled from the screened list instead."
            )
        else:
            if status["stale"]:
                out.warnings.append(
                    f"The web trend view is {status['age_days']} days old (older than "
                    f"{s.trend_view_stale_days} days); refresh it for current picks."
                )
            outlook = {sec.name.lower(): sec.outlook for sec in view.sectors}
            for st in view.stocks[:want_web]:
                entry, verdict = _web_entry(st, outlook.get(st.sector.lower(), "neutral"), view)
                out.entries.append(entry)
                out.verdicts.append(verdict)
                out.web_symbols.append(st.symbol)
            if len(out.web_symbols) < want_web:
                out.shortfalls.append(
                    f"Requested {want_web} trendy stocks; the web view has only {len(out.web_symbols)}."
                )

    remaining = total - len(out.entries)
    if remaining > 0:
        agent = StockResearcherAgent(
            conn, data_mode=data_mode, stale_after_days=s.stock_stale_after_days
        )
        if data_mode == "synthetic":
            agent.sync_service.ensure_seeded()
        res = agent.run(
            goal=goal, risk_category=risk_category, as_of=as_of, rule_set=stock_rule_set,
            min_market_cap_cr=min_market_cap_cr, min_traded_value_cr=min_traded_value_cr,
            max_candidates=remaining + len(out.web_symbols), waive_context_rules=True, s=s,
        )
        taken = set(out.web_symbols)
        pairs = [(e, v) for e, v in zip(res.entries, res.verdicts, strict=True)
                 if e.instrument.id not in taken][:remaining]
        for e, v in pairs:
            out.entries.append(e)
            out.verdicts.append(v)
            out.screened_symbols.append(e.instrument.id)
        out.warnings.extend(res.warnings)
        out.sync_as_of = res.sync_as_of
        if res.research_log_entry:
            out.research_log.append(res.research_log_entry)
        if len(pairs) < remaining:
            out.shortfalls.append(
                f"Requested {total} stocks; only {len(out.entries)} available "
                f"({remaining - len(pairs)} short on the screened list)."
            )
    return out
