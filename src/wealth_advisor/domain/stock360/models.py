"""Core data structures of the 360° report (docs/plans/stock-360-analysis-plan.md)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

MetricStatus = Literal[
    "verified",
    "computed",
    "estimated",
    "manual",
    "unavailable",
    "requires_live_verification",
    "not_applicable",
]


class Metric(BaseModel):
    id: str  # citable fact id, e.g. "TCS:s3:pe_ttm"
    label: str
    value: float | str | None = None
    unit: str = ""
    status: MetricStatus
    as_of: date | None = None
    source: str = ""  # yahoo_chart | yahoo_timeseries | derived | peers | web:<domain> | manual
    url: str = ""
    formula: str = ""
    inputs: list[str] = Field(default_factory=list)


class SectionTable(BaseModel):
    title: str
    columns: list[str]
    rows: list[list[str | float | None]]
    metric_ids: list[str] = Field(default_factory=list)


class Section(BaseModel):
    id: str  # "step0", "s1" ... "s17"
    title: str
    part: Literal[1, 2, 3, 4]
    metrics: list[Metric] = Field(default_factory=list)
    tables: list[SectionTable] = Field(default_factory=list)
    commentary: str = ""
    verdict: str | None = None
    status: Literal["complete", "partial", "unavailable"] = "partial"
    missing: list[str] = Field(default_factory=list)


class WebSource(BaseModel):
    url: str
    title: str = ""
    publisher: str = ""
    published_at: date | None = None
    fetched_at: datetime | None = None


class LegendTrigger(BaseModel):
    legend: str
    trigger: str
    kind: Literal["D", "J", "W"]
    fired: bool | None = None  # None = not evaluable
    detail: str = ""


class MechanicalVerdict(BaseModel):
    score: float = 0.0  # 0-100
    stars: float = 0.0
    action: str = "HOLD"
    sell_signals: int = 0
    evaluable_legends: int = 0
    legend_verdicts: dict[str, str] = Field(default_factory=dict)
    triggers: list[LegendTrigger] = Field(default_factory=list)


class FinalVerdict(BaseModel):
    action: str
    stars: float
    thesis: str
    clamped_reason: str = ""


class Report360(BaseModel):
    report_id: str
    symbol: str
    name: str
    data_mode: Literal["synthetic", "real"]
    template_version: str = "master-v2"
    generated_at: datetime
    as_of: date
    sector_adapter: Literal["general", "bfsi", "cyclical"] = "general"
    sections: list[Section]
    mechanical: MechanicalVerdict = Field(default_factory=MechanicalVerdict)
    final: FinalVerdict | None = None
    sources: list[WebSource] = Field(default_factory=list)
    llm_calls: int = 0
    web_searches: int = 0
    warnings: list[str] = Field(default_factory=list)
    disclosures: list[str] = Field(default_factory=list)
    charts: dict[str, Any] = Field(default_factory=dict)  # structured chart data; absent in older stored reports
