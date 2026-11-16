"""M11: Portfolio upload (CSV), valuation, 5/25 drift analysis, rebalance deltas & profile versioning."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, Field

from .agents.profiling import PROMPT_VERSION as PROFILING_PV
from .agents.profiling import ProfileAssumptions, ProfilingAgent
from .audit.log import AuditLog, ProfileStore
from .config import Settings, get_settings
from .data.store import FundRecord, MetadataStore
from .domain.allocation import target_allocation
from .domain.drift import DriftItem, compute_drift, rebalance_plan
from .logging_setup import new_run_id
from .models.core import Goal, UserFinancialProfile
from .models.enums import Action, Bucket

_PORTFOLIO_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_snapshot (
    profile_id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    holdings_json TEXT NOT NULL,
    valuation_json TEXT NOT NULL
);
"""


class PortfolioRowError(BaseModel):
    row: int
    field: str
    value: str
    message: str


class PortfolioHoldingInput(BaseModel):
    row: int = 1
    scheme_key: str
    code: str
    isin: str = ""
    name: str
    bucket: Bucket
    category: str
    units: float = Field(gt=0)
    avg_cost: float = Field(ge=0)
    as_of: date


class PortfolioCSVParseResult(BaseModel):
    holdings: list[PortfolioHoldingInput] = Field(default_factory=list)
    errors: list[PortfolioRowError] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0 and len(self.holdings) > 0


class ValuedHolding(BaseModel):
    code: str
    isin: str = ""
    name: str
    bucket: Bucket
    category: str
    units: float
    avg_cost: float
    as_of: date
    latest_nav: float
    nav_date: date
    cost_value: float
    current_value: float
    unrealized_gain: float
    unrealized_gain_pct: float
    holding_days: int
    weight: float = Field(ge=0.0, le=1.0)
    weight_pct: float = Field(ge=0.0, le=100.0)


class PortfolioValuation(BaseModel):
    as_of: date
    holdings: list[ValuedHolding]
    total_cost: float
    total_value: float
    total_unrealized_gain: float
    bucket_values: dict[Bucket, float]
    bucket_weights: dict[Bucket, float]
    hhi: float = Field(ge=0.0, le=1.0)


class RebalanceTradeSuggestion(BaseModel):
    instrument_id: str
    name: str
    bucket: Bucket
    category: str
    action: Action
    current_amount: float
    target_amount: float
    amount_delta: float
    holding_days: int | None = None
    unrealized_gain: float | None = None
    exit_load_warning: str | None = None
    tax_warning: str | None = None
    warnings: list[str] = Field(default_factory=list)


class PortfolioDriftReport(BaseModel):
    valuation: PortfolioValuation
    target: dict[Bucket, float]
    drift_items: list[DriftItem]
    breached: bool
    bucket_deltas: dict[Bucket, float]
    trades: list[RebalanceTradeSuggestion]
    exit_load_warnings: list[str] = Field(default_factory=list)


def _resolve_scheme_identifier(row_dict: dict[str, str]) -> tuple[str, str]:
    """Return (field_name, value) for the scheme/code/isin column in a CSV row."""
    for col in ("scheme", "code", "scheme_code", "isin"):
        val = (row_dict.get(col) or "").strip()
        if val:
            return col, val
    for col in ("scheme", "code", "scheme_code", "isin"):
        if col in row_dict:
            return col, ""
    return "scheme", ""


def parse_portfolio_csv(
    source: str | bytes | Path | io.TextIOBase,
    store: MetadataStore,
    *,
    max_as_of: date | None = None,
) -> PortfolioCSVParseResult:
    """Parse and validate a portfolio CSV against MetadataStore.

    Expected columns: (`scheme` | `code` | `scheme_code` | `isin`), `units`, `avg_cost`, `as_of`.
    Returns both valid holdings and a detailed row-level error report.
    """
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8-sig")
    elif isinstance(source, bytes):
        text = source.decode("utf-8-sig", errors="replace")
    elif isinstance(source, io.TextIOBase):
        text = source.read()
    else:
        # Distinguish a file path string vs inline CSV content
        if "\n" not in source and "," not in source and Path(source).exists():
            text = Path(source).read_text(encoding="utf-8-sig")
        else:
            text = source

    reader = csv.DictReader(io.StringIO(text.strip()))
    if not reader.fieldnames:
        return PortfolioCSVParseResult(
            errors=[
                PortfolioRowError(
                    row=1,
                    field="header",
                    value="",
                    message="CSV file is empty or missing a header row.",
                )
            ]
        )

    normalized_headers = {h.strip().lower(): h for h in reader.fieldnames if h}
    id_cols = {"scheme", "code", "scheme_code", "isin"}
    has_id_col = bool(id_cols & set(normalized_headers.keys()))
    required_other = {"units", "avg_cost", "as_of"}
    missing = sorted(required_other - set(normalized_headers.keys()))
    if not has_id_col:
        missing.insert(0, "scheme/isin")
    if missing:
        return PortfolioCSVParseResult(
            errors=[
                PortfolioRowError(
                    row=1,
                    field="header",
                    value=",".join(reader.fieldnames),
                    message=f"Missing required column(s): {', '.join(missing)}",
                )
            ]
        )

    holdings: list[PortfolioHoldingInput] = []
    errors: list[PortfolioRowError] = []

    for row_num, raw_row in enumerate(reader, start=2):
        row = {
            k.strip().lower(): (v.strip() if isinstance(v, str) else "")
            for k, v in raw_row.items()
            if k is not None
        }
        if not any(row.values()):
            continue

        row_errors: list[PortfolioRowError] = []
        id_field, scheme_key = _resolve_scheme_identifier(row)
        fund: FundRecord | None = None
        if not scheme_key:
            row_errors.append(
                PortfolioRowError(
                    row=row_num,
                    field=id_field,
                    value="",
                    message="Missing scheme code or ISIN.",
                )
            )
        else:
            fund = store.find_fund(scheme_key)
            # Also check if a separate isin column was provided and mismatches
            isin_col = row.get("isin", "").strip()
            if fund is None and isin_col and isin_col != scheme_key:
                fund = store.find_fund(isin_col)
            if fund is None:
                row_errors.append(
                    PortfolioRowError(
                        row=row_num,
                        field=id_field,
                        value=scheme_key,
                        message=f"Unknown scheme code or ISIN {scheme_key!r} in metadata store.",
                    )
                )

        units_raw = row.get("units", "")
        units_val: float | None = None
        if not units_raw:
            row_errors.append(
                PortfolioRowError(
                    row=row_num, field="units", value="", message="Missing units."
                )
            )
        else:
            try:
                units_val = float(units_raw)
                if units_val <= 0:
                    row_errors.append(
                        PortfolioRowError(
                            row=row_num,
                            field="units",
                            value=units_raw,
                            message="Units must be greater than 0.",
                        )
                    )
            except ValueError:
                row_errors.append(
                    PortfolioRowError(
                        row=row_num,
                        field="units",
                        value=units_raw,
                        message=f"Invalid numeric value for units: {units_raw!r}.",
                    )
                )

        cost_raw = row.get("avg_cost", "")
        cost_val: float | None = None
        if not cost_raw:
            row_errors.append(
                PortfolioRowError(
                    row=row_num, field="avg_cost", value="", message="Missing avg_cost."
                )
            )
        else:
            try:
                cost_val = float(cost_raw)
                if cost_val <= 0:
                    row_errors.append(
                        PortfolioRowError(
                            row=row_num,
                            field="avg_cost",
                            value=cost_raw,
                            message="avg_cost must be greater than 0.",
                        )
                    )
            except ValueError:
                row_errors.append(
                    PortfolioRowError(
                        row=row_num,
                        field="avg_cost",
                        value=cost_raw,
                        message=f"Invalid numeric value for avg_cost: {cost_raw!r}.",
                    )
                )

        as_of_raw = row.get("as_of", "")
        as_of_val: date | None = None
        if not as_of_raw:
            row_errors.append(
                PortfolioRowError(
                    row=row_num, field="as_of", value="", message="Missing as_of date."
                )
            )
        else:
            try:
                as_of_val = date.fromisoformat(as_of_raw)
                if max_as_of is not None and as_of_val > max_as_of:
                    row_errors.append(
                        PortfolioRowError(
                            row=row_num,
                            field="as_of",
                            value=as_of_raw,
                            message=f"as_of date {as_of_raw} cannot be after valuation date {max_as_of.isoformat()}.",
                        )
                    )
            except ValueError:
                row_errors.append(
                    PortfolioRowError(
                        row=row_num,
                        field="as_of",
                        value=as_of_raw,
                        message=f"Invalid ISO date for as_of: {as_of_raw!r} (expected YYYY-MM-DD).",
                    )
                )

        if row_errors:
            errors.extend(row_errors)
            continue

        assert fund is not None and units_val is not None and cost_val is not None and as_of_val is not None
        holdings.append(
            PortfolioHoldingInput(
                row=row_num,
                scheme_key=scheme_key,
                code=fund.code,
                isin=fund.isin,
                name=fund.name,
                bucket=fund.bucket,
                category=fund.category,
                units=units_val,
                avg_cost=cost_val,
                as_of=as_of_val,
            )
        )

    return PortfolioCSVParseResult(holdings=holdings, errors=errors)


def valuate_portfolio(
    holdings: Sequence[PortfolioHoldingInput],
    store: MetadataStore,
    *,
    as_of: date | None = None,
) -> PortfolioValuation:
    """Valuate holdings using the latest NAV on or before `as_of` and compute bucket weights + HHI."""
    valuation_date = as_of or store.latest_nav_date() or date.today()
    if not holdings:
        empty_buckets = {b: 0.0 for b in Bucket}
        return PortfolioValuation(
            as_of=valuation_date,
            holdings=[],
            total_cost=0.0,
            total_value=0.0,
            total_unrealized_gain=0.0,
            bucket_values=dict(empty_buckets),
            bucket_weights=dict(empty_buckets),
            hhi=0.0,
        )

    raw_items: list[dict] = []
    total_cost = 0.0
    total_value = 0.0
    bucket_values: dict[Bucket, float] = {b: 0.0 for b in Bucket}
    by_code_value: dict[str, float] = {}

    for h in holdings:
        nav_pair = store.latest_nav_as_of(h.code, valuation_date)
        if nav_pair is None:
            # Fallback to earliest available NAV if valuation_date precedes history
            nav_pair = store.latest_nav_as_of(h.code, None)
        if nav_pair is None:
            raise ValueError(f"No NAV data available in store for scheme {h.code!r}")
        nav_date, latest_nav = nav_pair
        cost_val = round(h.units * h.avg_cost, 2)
        curr_val = round(h.units * latest_nav, 2)
        gain = round(curr_val - cost_val, 2)
        gain_pct = round((latest_nav - h.avg_cost) / h.avg_cost * 100.0, 2) if h.avg_cost > 0 else 0.0
        holding_days = max(0, (valuation_date - h.as_of).days)

        total_cost += cost_val
        total_value += curr_val
        bucket_values[h.bucket] = bucket_values.get(h.bucket, 0.0) + curr_val
        by_code_value[h.code] = by_code_value.get(h.code, 0.0) + curr_val

        raw_items.append(
            {
                "code": h.code,
                "isin": h.isin,
                "name": h.name,
                "bucket": h.bucket,
                "category": h.category,
                "units": h.units,
                "avg_cost": h.avg_cost,
                "as_of": h.as_of,
                "latest_nav": round(latest_nav, 4),
                "nav_date": nav_date,
                "cost_value": cost_val,
                "current_value": curr_val,
                "unrealized_gain": gain,
                "unrealized_gain_pct": gain_pct,
                "holding_days": holding_days,
            }
        )

    valued_holdings: list[ValuedHolding] = []
    for item in raw_items:
        w = (item["current_value"] / total_value) if total_value > 0 else 0.0
        valued_holdings.append(
            ValuedHolding(
                **item,
                weight=round(w, 6),
                weight_pct=round(w * 100.0, 2),
            )
        )

    bucket_values_rounded = {b: round(v, 2) for b, v in bucket_values.items()}
    bucket_weights = {
        b: round((v / total_value) * 100.0, 2) if total_value > 0 else 0.0
        for b, v in bucket_values.items()
    }
    hhi = (
        round(sum((v / total_value) ** 2 for v in by_code_value.values()), 4)
        if total_value > 0
        else 0.0
    )
    hhi = min(1.0, max(0.0, hhi))

    return PortfolioValuation(
        as_of=valuation_date,
        holdings=valued_holdings,
        total_cost=round(total_cost, 2),
        total_value=round(total_value, 2),
        total_unrealized_gain=round(total_value - total_cost, 2),
        bucket_values=bucket_values_rounded,
        bucket_weights=bucket_weights,
        hhi=hhi,
    )


def build_sell_warnings(
    *,
    name: str,
    category: str,
    bucket: Bucket,
    sell_amount: float,
    holding_days: int | None,
    unrealized_gain: float | None = None,
    current_value: float | None = None,
    s: Settings | None = None,
) -> tuple[str | None, str | None, list[str]]:
    """Construct exit-load and tax-impact warnings before suggesting any sell/reduction."""
    s = s or get_settings()
    abs_sell = abs(sell_amount)
    exit_warn: str | None = None
    tax_warn: str | None = None
    warnings: list[str] = []

    if category == "ELSS" and holding_days is not None and holding_days < 3 * 365:
        exit_warn = (
            f"LOCK-IN WARNING ({name}): ELSS units held for {holding_days} days (< 3 years) "
            f"are under statutory lock-in and cannot be redeemed."
        )
        warnings.append(exit_warn)
    elif holding_days is not None and holding_days < s.exit_load_holding_days:
        exit_warn = (
            f"Exit-load warning ({name}): Held for {holding_days} days (< {s.exit_load_holding_days} days); "
            f"selling Rs {abs_sell:,.0f} may incur an exit load (typically up to 1% for early redemption)."
        )
        warnings.append(exit_warn)
    else:
        exit_warn = (
            f"Exit-load check ({name}): Verify scheme-specific exit-load window before selling Rs {abs_sell:,.0f}."
        )
        warnings.append(exit_warn)

    # Estimate proportional realized gain on the sold slice
    est_gain_str = ""
    if unrealized_gain is not None and current_value and current_value > 0:
        prop_gain = unrealized_gain * min(1.0, abs_sell / current_value)
        if prop_gain > 0:
            est_gain_str = f" (estimated realized gain ~Rs {prop_gain:,.0f})"

    if holding_days is not None and holding_days < 365:
        tax_warn = (
            f"Tax-impact warning ({name}): Short-Term Capital Gains (STCG) applies on units held "
            f"{holding_days} days (< 12 months) when selling Rs {abs_sell:,.0f}{est_gain_str}."
        )
    else:
        tax_warn = (
            f"Tax-impact warning ({name}): Long-Term Capital Gains (LTCG) rules apply on selling "
            f"Rs {abs_sell:,.0f}{est_gain_str}; review annual exemption limits before redeeming."
        )
    warnings.append(tax_warn)
    return exit_warn, tax_warn, warnings


def analyze_portfolio_drift(
    valuation: PortfolioValuation,
    target: dict[Bucket, float],
    *,
    s: Settings | None = None,
) -> PortfolioDriftReport:
    """Compute M2 bucket drift (5/25 rule), rupee bucket deltas, and instrument trade suggestions."""
    s = s or get_settings()
    drift_items = compute_drift(valuation.bucket_weights, target, s)
    breached = any(d.breached for d in drift_items)
    bucket_deltas = rebalance_plan(valuation.bucket_values, target, s)

    trades: list[RebalanceTradeSuggestion] = []
    all_warnings: list[str] = []

    if breached and valuation.total_value > 0:
        # Group holdings by bucket to distribute bucket-level deltas proportionally
        by_bucket: dict[Bucket, list[ValuedHolding]] = {}
        for h in valuation.holdings:
            by_bucket.setdefault(h.bucket, []).append(h)

        for bucket, b_delta in bucket_deltas.items():
            if abs(b_delta) < 1.0:
                continue
            b_holdings = by_bucket.get(bucket, [])
            b_val = valuation.bucket_values.get(bucket, 0.0)
            if b_holdings and b_val > 0:
                for h in b_holdings:
                    share = h.current_value / b_val
                    inst_delta = round(b_delta * share, 2)
                    if abs(inst_delta) < 1.0:
                        continue
                    target_amt = max(0.0, round(h.current_value + inst_delta, 2))
                    if inst_delta < 0:
                        action = Action.EXIT if target_amt == 0 else Action.DECREASE
                        exit_w, tax_w, warns = build_sell_warnings(
                            name=h.name,
                            category=h.category,
                            bucket=h.bucket,
                            sell_amount=abs(inst_delta),
                            holding_days=h.holding_days,
                            unrealized_gain=h.unrealized_gain,
                            current_value=h.current_value,
                            s=s,
                        )
                        all_warnings.extend(warns)
                    else:
                        action = Action.INCREASE
                        exit_w, tax_w, warns = None, None, []
                    trades.append(
                        RebalanceTradeSuggestion(
                            instrument_id=h.code,
                            name=h.name,
                            bucket=h.bucket,
                            category=h.category,
                            action=action,
                            current_amount=h.current_value,
                            target_amount=target_amt,
                            amount_delta=inst_delta,
                            holding_days=h.holding_days,
                            unrealized_gain=h.unrealized_gain,
                            exit_load_warning=exit_w,
                            tax_warning=tax_w,
                            warnings=warns,
                        )
                    )
            elif b_delta > 0:
                # Bucket currently has no holdings but needs allocation
                trades.append(
                    RebalanceTradeSuggestion(
                        instrument_id=f"BUCKET:{bucket.value}",
                        name=f"New {bucket.value.title()} Allocation",
                        bucket=bucket,
                        category=bucket.value.title(),
                        action=Action.BUY,
                        current_amount=0.0,
                        target_amount=round(b_delta, 2),
                        amount_delta=round(b_delta, 2),
                    )
                )

    return PortfolioDriftReport(
        valuation=valuation,
        target=target,
        drift_items=drift_items,
        breached=breached,
        bucket_deltas=bucket_deltas,
        trades=trades,
        exit_load_warnings=all_warnings,
    )


def update_profile_from_portfolio(
    conn: sqlite3.Connection,
    valuation: PortfolioValuation,
    goal: Goal,
    *,
    prev_profile: UserFinancialProfile | None = None,
    assumptions: ProfileAssumptions | None = None,
    run_id: str | None = None,
) -> tuple[UserFinancialProfile, ProfileAssumptions]:
    """Update risk-score 'existing allocation' factor (HHI) from real portfolio -> new profile version."""
    profiles = ProfileStore(conn)
    audit = AuditLog(conn)

    if prev_profile is not None:
        latest = profiles.latest(prev_profile.profile_id)
        base_profile = latest or prev_profile
        profile_id = base_profile.profile_id
        next_version = base_profile.version + 1
    else:
        profile_id = None
        next_version = 1

    base_assumptions = assumptions or ProfileAssumptions()
    updated_assumptions = base_assumptions.model_copy(update={"hhi": valuation.hhi})
    new_profile, updated_assumptions = ProfilingAgent.build_profile(
        goal,
        updated_assumptions,
        profile_id=profile_id,
        version=next_version,
    )
    new_profile.inputs["existing_allocation"] = {
        b.value: w for b, w in valuation.bucket_weights.items()
    }
    new_profile.inputs["portfolio_total_value"] = valuation.total_value
    new_profile.inputs["hhi"] = valuation.hhi

    profiles.save(new_profile)
    rid = run_id or new_run_id()
    audit.append(
        rid,
        "profiling",
        "profile_portfolio_update",
        {
            "profile_id": new_profile.profile_id,
            "version": new_profile.version,
            "previous_version": next_version - 1 if next_version > 1 else None,
            "hhi": valuation.hhi,
            "existing_allocation": new_profile.inputs["existing_allocation"],
            "portfolio_total_value": valuation.total_value,
            "risk_score": new_profile.risk_score,
            "category": new_profile.category.value,
            "band": new_profile.allocation_band.model_dump(mode="json"),
            "target": {b.value: w for b, w in target_allocation(new_profile.allocation_band).items()},
        },
        prompt_version=PROFILING_PV,
    )
    return new_profile, updated_assumptions


class PortfolioStore:
    """Persist uploaded portfolio holdings and latest valuation per profile_id."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        conn.executescript(_PORTFOLIO_SCHEMA)
        conn.commit()

    def save(
        self,
        profile_id: str,
        holdings: Sequence[PortfolioHoldingInput],
        valuation: PortfolioValuation,
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO portfolio_snapshot "
            "(profile_id, updated_at, as_of, holdings_json, valuation_json) VALUES (?,?,?,?,?)",
            (
                profile_id,
                datetime.now(UTC).isoformat(),
                valuation.as_of.isoformat(),
                json.dumps([h.model_dump(mode="json") for h in holdings]),
                valuation.model_dump_json(),
            ),
        )
        self.conn.commit()

    def get_holdings(self, profile_id: str) -> list[PortfolioHoldingInput] | None:
        row = self.conn.execute(
            "SELECT holdings_json FROM portfolio_snapshot WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if not row:
            return None
        data = json.loads(row["holdings_json"])
        return [PortfolioHoldingInput.model_validate(item) for item in data]

    def get_valuation(self, profile_id: str) -> PortfolioValuation | None:
        row = self.conn.execute(
            "SELECT valuation_json FROM portfolio_snapshot WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if not row:
            return None
        return PortfolioValuation.model_validate_json(row["valuation_json"])
