"""M13 Asset-Class & Context Research Agents (13a-13f).

All agents follow Ground Rule #1 & #2:
- Researchers gather evidence only (no scoring or BUY/SELL decisions).
- Numbers come strictly from deterministic catalog/tool facts; any LLM reason is checked
  by `guardrails.grounding.check_text` (regenerated once on failure, then stripped).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from ..data.multi_asset import (
    ALTERNATIVES_CATALOG,
    COMMODITY_CATALOG,
    EQUITY_CATALOG,
    FIXED_INCOME_CATALOG,
    MultiAssetRecord,
    facts_for_multi_asset,
)
from ..data.news import (
    NewsItem,
    classify_headline,
    compute_news_and_sentiment_scores,
    compute_sentiment_trend,
    enrich_entries_with_news,
    get_macro_and_regulatory_news,
)
from ..guardrails.grounding import check_text
from ..llm.base import LLMBudgetExceeded, LLMClient
from ..models.core import (
    Citation,
    DossierEntry,
    DossierSection,
    Fact,
    Goal,
    Instrument,
    NoData,
)
from ..models.enums import Bucket
from .mf_researcher import MAX_ATTEMPTS, Pick, ResearcherConfig, ResearchResult, Selection

log = logging.getLogger(__name__)

FD_RESEARCH_CONFIG = ResearcherConfig(
    asset_class=Bucket.DEBT,
    persona=(
        "You are a Fixed Income & Bank FD researcher covering Indian scheduled commercial "
        "bank term deposits and high-grade debt instruments. You gather evidence only."
    ),
    tools=("query_fixed_income",),
    output_field="fixed_income",
    categories=("Bank FD",),
    max_picks=3,
)

COMMODITY_RESEARCH_CONFIG = ResearcherConfig(
    asset_class=Bucket.GOLD,
    persona=(
        "You are a Commodity (Gold) researcher covering Sovereign Gold Bonds (SGBs) and "
        "Gold ETFs. You gather evidence only."
    ),
    tools=("query_commodity",),
    output_field="commodity",
    categories=("SGB", "Gold ETF"),
    max_picks=2,
)

EQUITY_RESEARCH_CONFIG = ResearcherConfig(
    asset_class=Bucket.EQUITY,
    persona=(
        "You are a Direct Equity researcher covering Nifty-50 large-cap Indian equities. "
        "You gather valuation, earnings, leverage, and governance facts only."
    ),
    tools=("query_equity",),
    output_field="equity",
    categories=("Large Cap Equity",),
    max_picks=3,
)

ALTERNATIVES_RESEARCH_CONFIG = ResearcherConfig(
    asset_class=Bucket.ALTERNATIVES,
    persona=(
        "You are an Alternatives researcher covering listed Indian REITs and InvITs only "
        "(excluding PMS and AIF due to high minimum ticket sizes). You gather evidence only."
    ),
    tools=("query_alternatives",),
    output_field="alt",
    categories=("REIT", "InvIT"),
    max_picks=2,
)

NEWS_RESEARCH_CONFIG = ResearcherConfig(
    asset_class=Bucket.EQUITY,
    persona="You are a financial news researcher gathering deduplicated, materiality-tagged headlines.",
    tools=("fetch_news",),
    output_field="news_context",
    categories=(),
    max_picks=5,
)

SENTIMENT_RESEARCH_CONFIG = ResearcherConfig(
    asset_class=Bucket.EQUITY,
    persona="You are a market sentiment researcher aggregating sentiment score, trend, and sample size.",
    tools=("fetch_sentiment",),
    output_field="sentiment",
    categories=(),
    max_picks=5,
)


def _format_multi_asset_table(cands: list[tuple[MultiAssetRecord, list[Fact]]]) -> str:
    lines = []
    for rec, facts in cands:
        vals = ", ".join(f"{f.name}={f.value}" for f in facts)
        lines.append(f"- {rec.code} | {rec.name} | {vals}")
    return "\n".join(lines)


@dataclass
class MultiAssetResearchAgent:
    """Generic researcher for 13a (FD), 13b (Commodity), 13c (Equity), and 13d (REIT/InvIT)."""

    llm: LLMClient | None
    config: ResearcherConfig
    catalog: Sequence[MultiAssetRecord]
    last_prompts: list[list[dict]] = field(default_factory=list)

    def _filter_candidates(self, goal: Goal) -> list[MultiAssetRecord]:
        excluded = [e.lower() for e in goal.excluded_categories]
        out: list[MultiAssetRecord] = []
        for rec in self.catalog:
            if self.config.categories and rec.category not in self.config.categories:
                continue
            if any(e in rec.category.lower() or e in rec.name.lower() for e in excluded):
                continue
            # 13d rule: skip PMS / AIF (minimum ticket >= ₹50L)
            min_inv = rec.metrics.get("min_investment_inr")
            if isinstance(min_inv, (int, float)) and float(min_inv) >= 5_000_000:
                continue
            # 13a rule: credit rating floor for Bank FD (AAA, SOV, AA+, AA)
            if rec.category == "Bank FD":
                cr = str(rec.metrics.get("credit_rating") or "").upper()
                if cr and cr not in ("AAA", "SOV", "AA+", "AA"):
                    continue
            # 13c rule: skip high promoter pledging (>25%) in initial screen
            if rec.category == "Large Cap Equity":
                pledge = rec.metrics.get("promoter_pledge_pct")
                if isinstance(pledge, (int, float)) and float(pledge) > 25.0:
                    continue
            out.append(rec)
        return out

    def run(self, goal: Goal, as_of: date) -> ResearchResult:
        cfg = self.config
        recs = self._filter_candidates(goal)
        if not recs:
            return ResearchResult(
                config_field=cfg.output_field,
                section=NoData(reason="no_candidates"),
            )

        cand_facts: dict[str, list[Fact]] = {}
        for r in recs:
            fs = facts_for_multi_asset(r, as_of)
            # 13a: annotate DICGC insurance coverage ratio if goal amount > ₹5L
            if r.category == "Bank FD":
                dicgc_lakhs = float(r.metrics.get("dicgc_insured_limit_lakhs", 5.0))
                goal_lakhs = round(goal.amount / 100_000.0, 2)
                covered_pct = round(min(100.0, (dicgc_lakhs / max(goal_lakhs, 0.01)) * 100.0), 2)
                fs.append(
                    Fact(
                        id=f"{r.code}:dicgc_coverage_pct",
                        name="dicgc_coverage_pct",
                        value=covered_pct,
                        citation=Citation(
                            source="dicgc_rbi_rule",
                            as_of=as_of,
                            url_or_doc_id="https://www.dicgc.org.in",
                        ),
                    )
                )
            cand_facts[r.code] = fs

        by_code = {r.code: r for r in recs}
        known = (
            {r.name for r in recs}
            | {r.code for r in recs}
            | set(cfg.categories)
            | {f"{c} Fund" for c in cfg.categories}
        )

        dropped: list[str] = []
        picks: list[Pick] = []

        if self.llm is None:
            picks = [Pick(scheme_code=r.code, reason="") for r in recs[: cfg.max_picks]]
        else:
            sys_prompt = (
                f"{cfg.persona}\n"
                f"Investor goal: horizon {goal.horizon_years:g} years, risk preference "
                f"{goal.risk_preference.value}. Select up to {cfg.max_picks} instruments from the "
                "candidate list ONLY, using their code as scheme_code. Write a one-sentence "
                "reason using ONLY numbers and names present in the table."
            )
            messages = [
                {"role": "system", "content": sys_prompt},
                {
                    "role": "user",
                    "content": "Candidates:\n"
                    + _format_multi_asset_table([(by_code[c], fs) for c, fs in cand_facts.items()]),
                },
            ]
            for attempt in range(MAX_ATTEMPTS):
                self.last_prompts.append(messages)
                try:
                    sel = self.llm.complete(messages, Selection)
                except LLMBudgetExceeded:
                    picks = [Pick(scheme_code=r.code, reason="") for r in recs[: cfg.max_picks]]
                    break
                picks, dropped = [], []
                problems: list[str] = []
                for p in sel.picks[: cfg.max_picks]:
                    if p.scheme_code not in by_code:
                        dropped.append(f"unknown scheme_code {p.scheme_code!r}")
                        continue
                    if p.reason:
                        rep = check_text(p.reason, cand_facts[p.scheme_code], known)
                        if not rep.ok:
                            problems.append(f"{p.scheme_code}: {rep.describe()}")
                            if attempt == MAX_ATTEMPTS - 1:
                                dropped.append(f"reason stripped for {p.scheme_code}: {rep.describe()}")
                                p = Pick(scheme_code=p.scheme_code, reason="")
                    picks.append(p)
                if not problems and not any(d.startswith("unknown") for d in dropped):
                    break
                if attempt < MAX_ATTEMPTS - 1:
                    feedback = "; ".join(problems + dropped)
                    messages = messages + [
                        {"role": "assistant", "content": sel.model_dump_json()},
                        {"role": "user", "content": f"Rejected: {feedback}. Fix and answer again."},
                    ]

        seen: set[str] = set()
        entries: list[DossierEntry] = []
        notes: dict[str, str] = {}
        all_facts: list[Fact] = []
        for p in picks:
            if p.scheme_code in seen or p.scheme_code not in by_code:
                continue
            seen.add(p.scheme_code)
            rec = by_code[p.scheme_code]
            fs = cand_facts[rec.code]
            entries.append(
                DossierEntry(
                    instrument=Instrument(
                        id=rec.code,
                        name=rec.name,
                        asset_class=rec.bucket,
                        category=rec.category,
                        plan_type=rec.plan,
                    ),
                    facts=fs,
                )
            )
            all_facts.extend(fs)
            if p.reason:
                notes[rec.code] = p.reason

        section: DossierSection = entries if entries else NoData(reason="no_grounded_candidates")
        return ResearchResult(
            config_field=cfg.output_field,
            section=section,
            notes=notes,
            dropped=dropped,
            facts=all_facts,
        )


class FDResearchAgent(MultiAssetResearchAgent):
    """13a — FD / Debt researcher (`fixed_income`)."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        config: ResearcherConfig = FD_RESEARCH_CONFIG,
        catalog: Sequence[MultiAssetRecord] = FIXED_INCOME_CATALOG,
    ) -> None:
        super().__init__(llm=llm, config=config, catalog=catalog)


class CommodityResearchAgent(MultiAssetResearchAgent):
    """13b — Commodity (gold) researcher (`commodity`)."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        config: ResearcherConfig = COMMODITY_RESEARCH_CONFIG,
        catalog: Sequence[MultiAssetRecord] = COMMODITY_CATALOG,
    ) -> None:
        super().__init__(llm=llm, config=config, catalog=catalog)


class EquityResearchAgent(MultiAssetResearchAgent):
    """13c — Direct Equity researcher (`equity`)."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        config: ResearcherConfig = EQUITY_RESEARCH_CONFIG,
        catalog: Sequence[MultiAssetRecord] = EQUITY_CATALOG,
    ) -> None:
        super().__init__(llm=llm, config=config, catalog=catalog)


class AlternativesResearchAgent(MultiAssetResearchAgent):
    """13d — Alternatives (listed REITs/InvITs) researcher (`alt`)."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        config: ResearcherConfig = ALTERNATIVES_RESEARCH_CONFIG,
        catalog: Sequence[MultiAssetRecord] = ALTERNATIVES_CATALOG,
    ) -> None:
        super().__init__(llm=llm, config=config, catalog=catalog)


@dataclass
class NewsResearchAgent:
    """13e — News Agent (`news_context`): per-instrument + macro/regulatory headlines (SEBI, RBI)
    with deduplication and materiality tags (`none / low / medium / high`).
    """

    entries: list[DossierEntry] = field(default_factory=list)
    fetch_live: bool = False
    config: ResearcherConfig = NEWS_RESEARCH_CONFIG
    custom_headlines: list[dict] = field(default_factory=list)

    def run(self, goal: Goal, as_of: date) -> ResearchResult:
        if self.entries:
            news_sec, _, _ = enrich_entries_with_news(
                self.entries, as_of=as_of, fetch_live=self.fetch_live
            )
            return ResearchResult(
                config_field=self.config.output_field,
                section=news_sec if news_sec else NoData(reason="no_news"),
            )

        raw_items: list[NewsItem] = []
        if self.custom_headlines:
            for idx, row in enumerate(self.custom_headlines, start=1):
                title = str(row.get("title") or "").strip()
                if not title:
                    continue
                sent, mat, scope = classify_headline(title)
                if "materiality" in row:
                    mat = row["materiality"]
                if "sentiment" in row:
                    sent = row["sentiment"]
                raw_items.append(
                    NewsItem(
                        id=str(row.get("id") or f"NEWS:CUSTOM:{idx}"),
                        title=title,
                        summary=str(row.get("summary") or title),
                        source=str(row.get("source") or "curated_news"),
                        url=str(row.get("url") or "https://www.sebi.gov.in"),
                        published_at=row.get("published_at") or as_of,
                        sentiment=sent,
                        materiality=mat,
                        scope=scope,
                    )
                )
        else:
            raw_items = get_macro_and_regulatory_news(as_of=as_of, fetch_live=self.fetch_live)

        # Deduplicate by normalized title
        deduped: list[NewsItem] = []
        seen: set[str] = set()
        for item in raw_items:
            key = " ".join(item.title.lower().split())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        if not deduped:
            return ResearchResult(
                config_field=self.config.output_field,
                section=NoData(reason="no_news"),
            )

        facts: list[Fact] = []
        for idx, item in enumerate(deduped, start=1):
            facts.append(
                Fact(
                    id=item.id,
                    name=f"news_headline_{idx}",
                    value=f"[{item.materiality.upper()} | {item.sentiment.upper()}] {item.title}",
                    citation=Citation(
                        source=item.source,
                        as_of=item.published_at,
                        url_or_doc_id=item.url or "https://www.sebi.gov.in",
                        snippet=item.summary,
                    ),
                )
            )
            facts.append(
                Fact(
                    id=f"{item.id}:materiality",
                    name=f"news_materiality_{idx}",
                    value=item.materiality,
                    citation=Citation(
                        source=item.source,
                        as_of=item.published_at,
                        url_or_doc_id=item.url or "https://www.sebi.gov.in",
                    ),
                )
            )

        macro_inst = Instrument(
            id="MACRO_INDIA",
            name="India Macro & Regulatory Context",
            asset_class=Bucket.DEBT,
            category="Liquid",
        )
        return ResearchResult(
            config_field=self.config.output_field,
            section=[DossierEntry(instrument=macro_inst, facts=facts)],
            facts=facts,
        )


@dataclass
class SentimentResearchAgent:
    """13f — Sentiment Agent (`sentiment`): aggregates news/forum sentiment into a score +
    trend (`improving / stable / deteriorating`) + sample_size with small-sample discount.
    """

    entries: list[DossierEntry] = field(default_factory=list)
    fetch_live: bool = False
    config: ResearcherConfig = SENTIMENT_RESEARCH_CONFIG
    headlines: list[NewsItem] = field(default_factory=list)

    def run(self, goal: Goal, as_of: date) -> ResearchResult:
        if self.entries and not self.headlines:
            _, sent_sec, _ = enrich_entries_with_news(
                self.entries, as_of=as_of, fetch_live=self.fetch_live
            )
            return ResearchResult(
                config_field=self.config.output_field,
                section=sent_sec if sent_sec else NoData(reason="no_sentiment"),
            )

        items = list(self.headlines) if self.headlines else get_macro_and_regulatory_news(
            as_of=as_of, fetch_live=self.fetch_live
        )
        if not items:
            return ResearchResult(
                config_field=self.config.output_field,
                section=NoData(reason="no_sentiment"),
            )

        _, sent_val, overall = compute_news_and_sentiment_scores(items)
        trend = compute_sentiment_trend(items, sent_val)
        sample_size = len(items)
        top = items[0]
        cite = Citation(
            source=top.source,
            as_of=top.published_at,
            url_or_doc_id=top.url or "https://www.amfiindia.com",
        )
        facts = [
            Fact(
                id="SENTIMENT:score",
                name="sentiment_score",
                value=sent_val,
                citation=cite,
            ),
            Fact(
                id="SENTIMENT:overall",
                name="overall_sentiment",
                value=overall,
                citation=cite,
            ),
            Fact(
                id="SENTIMENT:trend",
                name="sentiment_trend",
                value=trend,
                citation=cite,
            ),
            Fact(
                id="SENTIMENT:sample_size",
                name="sentiment_sample_size",
                value=sample_size,
                citation=cite,
            ),
        ]
        inst = Instrument(
            id="MARKET_SENTIMENT",
            name="Aggregate Market Sentiment",
            asset_class=Bucket.EQUITY,
            category="Index Fund",
        )
        return ResearchResult(
            config_field=self.config.output_field,
            section=[DossierEntry(instrument=inst, facts=facts)],
            facts=facts,
        )
