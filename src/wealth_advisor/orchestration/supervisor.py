"""M12 Supervisor fan-out + blackboard dossier collation."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel

from ..agents.asset_researchers import (
    ALTERNATIVES_RESEARCH_CONFIG,
    COMMODITY_RESEARCH_CONFIG,
    EQUITY_RESEARCH_CONFIG,
    FD_RESEARCH_CONFIG,
    NEWS_RESEARCH_CONFIG,
    SENTIMENT_RESEARCH_CONFIG,
    MultiAssetResearchAgent,
    NewsResearchAgent,
    SentimentResearchAgent,
)
from ..agents.mf_researcher import MFResearchAgent, ResearcherConfig, ResearchResult, default_config
from ..agents.tools import ResearchTools
from ..data.multi_asset import (
    ALTERNATIVES_CATALOG,
    COMMODITY_CATALOG,
    EQUITY_CATALOG,
    FIXED_INCOME_CATALOG,
    MultiAssetRecord,
)
from ..data.news import enrich_entries_with_news
from ..data.store import MetadataStore
from ..domain.allocation import target_allocation
from ..domain.categories import categories_for
from ..llm.base import LLMClient
from ..models.core import (
    DossierEntry,
    DossierSection,
    Goal,
    NoData,
    ResearchDossier,
    UserFinancialProfile,
)
from ..models.enums import Bucket, RiskCategory
from ..rag.index import VectorIndex

log = logging.getLogger(__name__)

DOSSIER_FIELDS: tuple[str, ...] = (
    "equity",
    "mf",
    "commodity",
    "fixed_income",
    "alt",
    "news_context",
    "sentiment",
)

ASSET_DOSSIER_FIELDS: tuple[str, ...] = (
    "mf",
    "equity",
    "fixed_income",
    "commodity",
    "alt",
)

ALL_MULTI_ASSET_RECORDS: tuple[MultiAssetRecord, ...] = (
    *FIXED_INCOME_CATALOG,
    *COMMODITY_CATALOG,
    *EQUITY_CATALOG,
    *ALTERNATIVES_CATALOG,
)


class SupervisorRunResult(BaseModel):
    dossier: ResearchDossier
    results: list[ResearchResult] = []
    dispatched_configs: list[str] = []
    skipped_configs: list[str] = []
    timed_out_sections: list[str] = []
    research_log: list[dict[str, Any]] = []


def all_dossier_entries(dossier: ResearchDossier) -> list[DossierEntry]:
    """Return all unique instrument DossierEntry items across asset-class sections of a dossier."""
    seen: set[str] = set()
    out: list[DossierEntry] = []
    for field_name in ASSET_DOSSIER_FIELDS:
        sec = getattr(dossier, field_name, None)
        if isinstance(sec, list):
            for entry in sec:
                if entry.instrument.id not in seen:
                    seen.add(entry.instrument.id)
                    out.append(entry)
    return out


def dossier_timeout_sections(dossier: ResearchDossier) -> list[str]:
    """Return names of dossier sections whose value is `NoData(reason='timeout')`."""
    timed_out: list[str] = []
    for field_name in DOSSIER_FIELDS:
        sec = getattr(dossier, field_name, None)
        if isinstance(sec, NoData) and sec.reason == "timeout":
            timed_out.append(field_name)
    return timed_out


def collate_dossier(
    results: Sequence[ResearchResult],
    *,
    skipped_fields: Mapping[str, str] | None = None,
) -> ResearchDossier:
    """Blackboard dossier collation: schema validation + NoData markers only (no opinions).

    When multiple ResearcherConfigs write to the same `output_field` (e.g., MF debt and MF equity
    both writing to `mf`), their validated `DossierEntry` lists are merged in order without
    duplicates. If all results for a field are `NoData`, `"timeout"` takes precedence.
    """
    sections_entries: dict[str, list[DossierEntry]] = {}
    sections_nodata: dict[str, list[str]] = {}

    for res in results:
        field_name = res.config_field
        if field_name not in DOSSIER_FIELDS:
            raise ValueError(f"Unknown ResearchDossier field: {field_name!r}")
        if isinstance(res.section, NoData):
            sections_nodata.setdefault(field_name, []).append(res.section.reason)
        else:
            validated_list = sections_entries.setdefault(field_name, [])
            seen_ids = {e.instrument.id for e in validated_list}
            for entry in res.section:
                validated = DossierEntry.model_validate(entry)
                for f in validated.facts:
                    if not f.citation or not f.citation.source:
                        raise ValueError(
                            f"Uncited fact {f.id!r} in DossierEntry {validated.instrument.id!r}"
                        )
                if validated.instrument.id not in seen_ids:
                    seen_ids.add(validated.instrument.id)
                    validated_list.append(validated)

    payload: dict[str, DossierSection] = {}
    for field_name in DOSSIER_FIELDS:
        if sections_entries.get(field_name):
            payload[field_name] = sections_entries[field_name]
        elif field_name in sections_nodata:
            reasons = sections_nodata[field_name]
            if "timeout" in reasons:
                payload[field_name] = NoData(reason="timeout")
            else:
                payload[field_name] = NoData(reason=reasons[0])
        elif skipped_fields and field_name in skipped_fields:
            payload[field_name] = NoData(reason=skipped_fields[field_name])
        else:
            payload[field_name] = NoData(reason="not_requested")

    return ResearchDossier(**payload)


def default_multi_asset_configs() -> list[ResearcherConfig]:
    """Return standard M12/M13 ResearcherConfigs across MF, FD, Commodity, Equity, Alt, News, Sentiment."""
    mf_configs = [
        default_config(bucket, categories_for(bucket))
        for bucket in (Bucket.DEBT, Bucket.EQUITY, Bucket.GOLD)
        if categories_for(bucket)
    ]
    return [
        *mf_configs,
        FD_RESEARCH_CONFIG,
        COMMODITY_RESEARCH_CONFIG,
        EQUITY_RESEARCH_CONFIG,
        ALTERNATIVES_RESEARCH_CONFIG,
        NEWS_RESEARCH_CONFIG,
        SENTIMENT_RESEARCH_CONFIG,
    ]


@dataclass
class ResearchSupervisor:
    """M12 Research Supervisor: selects researchers for goal/profile, fans out via asyncio.gather
    with per-agent timeout, and collates results into a blackboard ResearchDossier.
    """

    llm: LLMClient | None = None
    store: MetadataStore | None = None
    index: VectorIndex | None = None
    configs: list[ResearcherConfig] = field(default_factory=default_multi_asset_configs)
    timeout_seconds: float = 5.0
    runners: dict[str, Callable[[Goal, date], Any]] = field(default_factory=dict)

    def register(
        self,
        config: ResearcherConfig,
        runner: Callable[[Goal, date], Any] | None = None,
    ) -> None:
        """Register a ResearcherConfig and optional runner callable."""
        self.configs.append(config)
        if runner is not None:
            self.runners[self._config_key(config)] = runner

    @staticmethod
    def _config_key(cfg: ResearcherConfig) -> str:
        cats = ",".join(cfg.categories)
        return f"{cfg.output_field}:{cfg.asset_class.value}:{cats}"

    def should_run(
        self,
        cfg: ResearcherConfig,
        goal: Goal,
        profile: UserFinancialProfile | None = None,
        target: Mapping[Bucket, float] | None = None,
    ) -> tuple[bool, str]:
        """Decide whether a researcher config should run for the given goal and profile."""
        # Cross-cutting context researchers (news & sentiment) always run when configured
        if cfg.output_field in ("news_context", "sentiment"):
            return True, ""

        # Short-horizon guard: skip equity and alternatives for < 1 year (e.g. 6-month goal)
        if goal.horizon_years < 1.0 and cfg.asset_class in (Bucket.EQUITY, Bucket.ALTERNATIVES):
            return False, "skipped_short_horizon"

        # Conservative + <= 1 year guard: skip equity and alternatives
        if (
            profile is not None
            and profile.category == RiskCategory.CONSERVATIVE
            and goal.horizon_years <= 1.0
            and cfg.asset_class in (Bucket.EQUITY, Bucket.ALTERNATIVES)
        ):
            return False, "skipped_conservative_short_horizon"

        # If target weights are provided (or derived from profile), skip buckets with 0% target weight
        eff_target = target
        if eff_target is None and profile is not None:
            eff_target = target_allocation(profile.allocation_band)
        if eff_target is not None:
            w = eff_target.get(cfg.asset_class, 0.0)
            if w <= 0.0:
                return False, "skipped_zero_target_weight"

        # Check if all categories in config are excluded by the goal
        if cfg.categories and goal.excluded_categories:
            excluded = [e.lower() for e in goal.excluded_categories]
            remaining = [
                c for c in cfg.categories if not any(e in c.lower() for e in excluded)
            ]
            if not remaining:
                return False, "skipped_excluded_category"

        return True, ""

    def select_configs(
        self,
        goal: Goal,
        profile: UserFinancialProfile | None = None,
        target: Mapping[Bucket, float] | None = None,
    ) -> tuple[list[ResearcherConfig], dict[str, str]]:
        """Return `(active_configs, skipped_field_reasons)`."""
        active: list[ResearcherConfig] = []
        skipped: dict[str, str] = {}
        for cfg in self.configs:
            ok, reason = self.should_run(cfg, goal, profile, target)
            if ok:
                active.append(cfg)
            else:
                skipped[cfg.output_field] = reason
        return active, skipped

    def _build_runner(self, cfg: ResearcherConfig, as_of: date) -> Callable[[Goal, date], Any]:
        key = self._config_key(cfg)
        if key in self.runners:
            return self.runners[key]
        if cfg.output_field in self.runners:
            return self.runners[cfg.output_field]

        if cfg.output_field == "news_context":
            return NewsResearchAgent(config=cfg).run
        if cfg.output_field == "sentiment":
            return SentimentResearchAgent(config=cfg).run

        # If MF store has matching categories and output_field is "mf", use MFResearchAgent
        if cfg.output_field == "mf" and self.store is not None and self.index is not None and self.llm is not None:
            recs = self.store.query_funds(categories=list(cfg.categories))
            if recs:
                tools = ResearchTools(self.store, self.index, as_of=as_of)
                return MFResearchAgent(self.llm, tools, cfg).run

        # Match against multi-asset catalog
        matching = [
            r
            for r in ALL_MULTI_ASSET_RECORDS
            if (not cfg.categories or r.category in cfg.categories)
            and r.bucket == cfg.asset_class
        ]
        if not matching and cfg.categories:
            matching = [r for r in ALL_MULTI_ASSET_RECORDS if r.category in cfg.categories]

        if not matching:
            # Synthesize deterministic catalog records for any custom/dummy ResearcherConfig
            cat_name = cfg.categories[0] if cfg.categories else "Bank FD"
            code_prefix = cfg.output_field.upper()[:4]
            matching = [
                MultiAssetRecord(
                    code=f"{code_prefix}_DUMMY_1",
                    name=f"{cat_name} Benchmark Instrument",
                    bucket=cfg.asset_class,
                    category=cat_name,
                    source="configured_researcher_catalog",
                    source_url="https://www.sebi.gov.in",
                    metrics={
                        "expense_ratio": 0.20,
                        "aum_cr": 2500.0,
                        "track_years": 5.0,
                        "rating": 4.0,
                        "roll_3y_median": 10.5,
                        "cagr_5y": 11.0,
                        "pct_roll_3y": 75.0,
                        "pct_cagr_5y": 75.0,
                        "volatility": 8.0,
                        "max_drawdown": -8.0,
                        "sharpe": 1.0,
                        "plan": "direct",
                    },
                )
            ]

        return MultiAssetResearchAgent(llm=self.llm, config=cfg, catalog=matching).run

    async def _run_one(
        self, cfg: ResearcherConfig, goal: Goal, as_of: date
    ) -> tuple[ResearcherConfig, ResearchResult, bool]:
        runner = self._build_runner(cfg, as_of)

        async def _invoke() -> ResearchResult:
            if inspect.iscoroutinefunction(runner):
                res = await runner(goal, as_of)
            else:
                res = await asyncio.to_thread(runner, goal, as_of)
            if isinstance(res, ResearchResult):
                return res
            if isinstance(res, (list, NoData)):
                return ResearchResult(config_field=cfg.output_field, section=res)
            raise TypeError(f"Unexpected researcher return type: {type(res)!r}")

        try:
            res = await asyncio.wait_for(_invoke(), timeout=self.timeout_seconds)
            return cfg, res, False
        except TimeoutError:
            log.warning(
                "Researcher timed out after %.2fs: field=%s bucket=%s",
                self.timeout_seconds,
                cfg.output_field,
                cfg.asset_class.value,
            )
            return (
                cfg,
                ResearchResult(
                    config_field=cfg.output_field,
                    section=NoData(reason="timeout"),
                ),
                True,
            )
        except Exception as exc:
            log.warning("Researcher failed (%s): %s", cfg.output_field, exc)
            return (
                cfg,
                ResearchResult(
                    config_field=cfg.output_field,
                    section=NoData(reason=f"error:{type(exc).__name__}"),
                ),
                False,
            )

    async def arun(
        self,
        goal: Goal,
        as_of: date,
        profile: UserFinancialProfile | None = None,
        target: Mapping[Bucket, float] | None = None,
    ) -> SupervisorRunResult:
        """Select researchers and fan out concurrently via asyncio.gather with per-agent timeouts."""
        active_configs, skipped_map = self.select_configs(goal, profile, target)
        tasks = [self._run_one(cfg, goal, as_of) for cfg in active_configs]
        completed = await asyncio.gather(*tasks) if tasks else []

        results: list[ResearchResult] = []
        timed_out: list[str] = []
        research_log: list[dict[str, Any]] = []

        for cfg, res, is_timeout in completed:
            results.append(res)
            if is_timeout and cfg.output_field not in timed_out:
                timed_out.append(cfg.output_field)
            if isinstance(res.section, NoData):
                research_log.append(
                    {
                        "bucket": cfg.asset_class.value,
                        "output_field": cfg.output_field,
                        "no_data": res.section.reason,
                    }
                )
            research_log.append(
                {
                    "bucket": cfg.asset_class.value,
                    "output_field": cfg.output_field,
                    "notes": res.notes,
                    "dropped": res.dropped,
                    "tool_calls": [c.model_dump() for c in res.tool_calls],
                    "funnel_stats": res.funnel_stats,
                }
            )

        dossier = collate_dossier(results, skipped_fields=skipped_map)

        # Enrich asset entries with news/sentiment if news_context/sentiment ran or entries exist
        asset_entries = all_dossier_entries(dossier)
        if asset_entries:
            news_sec, sent_sec, _ = enrich_entries_with_news(
                asset_entries, as_of=as_of, fetch_live=False
            )
            if not isinstance(dossier.news_context, NoData) and news_sec:
                dossier.news_context = news_sec
            if not isinstance(dossier.sentiment, NoData) and sent_sec:
                dossier.sentiment = sent_sec

        return SupervisorRunResult(
            dossier=dossier,
            results=results,
            dispatched_configs=[self._config_key(c) for c in active_configs],
            skipped_configs=[
                f"{field_name}:{reason}" for field_name, reason in skipped_map.items()
            ],
            timed_out_sections=timed_out,
            research_log=research_log,
        )

    def run(
        self,
        goal: Goal,
        as_of: date,
        profile: UserFinancialProfile | None = None,
        target: Mapping[Bucket, float] | None = None,
    ) -> SupervisorRunResult:
        """Synchronous wrapper around `arun`."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    lambda: asyncio.run(self.arun(goal, as_of, profile, target))
                ).result()
        return asyncio.run(self.arun(goal, as_of, profile, target))
