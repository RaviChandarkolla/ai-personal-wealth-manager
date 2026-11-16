from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import Action, Bucket, RiskCategory, RiskPreference


def _now() -> datetime:
    return datetime.now(UTC)


class FundRequest(BaseModel):
    """`count` funds of `category` (canonical category, or generic 'Debt' / 'Equity' / 'Gold')."""

    category: str
    count: int = Field(default=1, ge=1, le=10)


class PortfolioSpec(BaseModel):
    """What the user asked for in the AI notes. Takes priority over default selection limits."""

    fund_requests: list[FundRequest] = []
    stock_count: int | None = Field(default=None, ge=0, le=30)
    trendy_stock_count: int | None = Field(default=None, ge=0, le=30)
    include_gold: bool | None = None
    warnings: list[str] = []

    @property
    def is_custom(self) -> bool:
        """True when the notes name specific funds or stocks ("only X" mode)."""
        non_gold = any(r.category != "Gold ETF" for r in self.fund_requests)
        return non_gold or bool(self.stock_count)

    @property
    def total_funds(self) -> int:
        return sum(r.count for r in self.fund_requests)

    @property
    def all_stocks_from_web(self) -> bool:
        return bool(self.stock_count) and self.trendy_stock_count == self.stock_count


class Goal(BaseModel):
    amount: float = Field(gt=0)
    horizon_years: float = Field(gt=0)
    risk_preference: RiskPreference
    purpose: str = ""
    excluded_categories: list[str] = []
    spec: PortfolioSpec | None = None


class AllocationBand(BaseModel):
    """Percent ranges per bucket as (min, max)."""

    ranges: dict[Bucket, tuple[float, float]]


class UserFinancialProfile(BaseModel):
    """Immutable, versioned row; a change creates a new version."""

    model_config = ConfigDict(frozen=True)

    profile_id: str
    version: int = Field(ge=1)
    created_at: datetime = Field(default_factory=_now)
    inputs: dict[str, Any]
    risk_score: float = Field(ge=1, le=10)
    category: RiskCategory
    allocation_band: AllocationBand


class Instrument(BaseModel):
    id: str  # ISIN or scheme code
    name: str
    asset_class: Bucket
    category: str
    plan_type: str = "direct"


class Citation(BaseModel):
    source: str
    as_of: date
    url_or_doc_id: str = ""
    snippet: str = ""


class Fact(BaseModel):
    """The only way a number may enter a dossier: a value plus its citation."""

    id: str
    name: str
    value: float | str | int
    citation: Citation  # required: validation fails without it


class NoData(BaseModel):
    reason: str


class DossierEntry(BaseModel):
    instrument: Instrument
    facts: list[Fact]


DossierSection = list[DossierEntry] | NoData


class ResearchDossier(BaseModel):
    equity: DossierSection = NoData(reason="not_requested")
    mf: DossierSection = NoData(reason="not_requested")
    commodity: DossierSection = NoData(reason="not_requested")
    fixed_income: DossierSection = NoData(reason="not_requested")
    alt: DossierSection = NoData(reason="not_requested")
    news_context: DossierSection = NoData(reason="not_requested")
    sentiment: DossierSection = NoData(reason="not_requested")


class AnalystVerdict(BaseModel):
    instrument_id: str
    pros: list[str]
    cons: list[str]
    score: float = Field(ge=0, le=100)
    sub_scores: dict[str, float]
    confidence: float = Field(ge=0, le=1)
    change_conditions: list[str] = []


class Recommendation(BaseModel):
    instrument_id: str
    action: Action
    target_weight: float = Field(ge=0, le=100)
    rationale: str
    citations: list[Citation]

    @model_validator(mode="after")
    def _needs_citations(self):
        if not self.citations:
            raise ValueError("Recommendation requires at least one citation")
        return self


class DecisionRecord(BaseModel):
    run_id: str
    profile_id: str
    profile_version: int
    created_at: datetime = Field(default_factory=_now)
    recommendations: list[Recommendation]
    model: str = ""
    prompt_version: str = ""
