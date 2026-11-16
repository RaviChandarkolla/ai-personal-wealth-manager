"""Research tools (allow-listed per agent) and the code that turns store rows into cited Facts."""

import hashlib
import json
from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel

from ..data.store import FundRecord, FundStats, MetadataStore
from ..models.core import Citation, Fact
from ..rag.index import Retrieved, VectorIndex


class ToolCall(BaseModel):
    tool: str
    args: dict
    result_hash: str


class ResearchTools:
    """`query_funds` (M4), `retrieve_docs` (M5), `get_metrics` (M2); every call is recorded."""

    ALL = ("query_funds", "retrieve_docs", "get_metrics")

    def __init__(
        self, store: MetadataStore, index: VectorIndex, allowed: Sequence[str] = ALL,
        as_of: date | None = None,
    ) -> None:
        self._store, self._index, self._allowed = store, index, set(allowed)
        self._as_of = as_of
        self.calls: list[ToolCall] = []

    def call(self, tool: str, **kwargs):
        if tool not in self._allowed:
            raise PermissionError(f"tool {tool!r} not allowed for this agent")
        if tool == "query_funds":
            result: list | FundStats | None = self._store.query_funds(**kwargs)
        elif tool == "retrieve_docs":
            result = self._index.search(as_of=self._as_of, **kwargs)
        else:
            result = self._store.get_stats(**kwargs)
        blob = json.dumps(
            [r.model_dump(mode="json") for r in result]
            if isinstance(result, list)
            else (result.model_dump(mode="json") if result else None),
            sort_keys=True,
        )
        args = {k: (v.value if hasattr(v, "value") else v) for k, v in kwargs.items()}
        self.calls.append(
            ToolCall(tool=tool, args=args, result_hash=hashlib.sha256(blob.encode()).hexdigest()[:16])
        )
        return result


def fact_id(code: str, name: str) -> str:
    return f"{code}:{name}"


def facts_for_fund(
    rec: FundRecord, stats: FundStats | None, as_of: date, docs: Sequence[Retrieved] = ()
) -> list[Fact]:
    """Every figure the agents may use for a fund, each with its citation."""
    meta = Citation(source=rec.source, as_of=rec.as_of, url_or_doc_id=rec.code)
    out: list[Fact] = []

    def add(name: str, value, cite: Citation) -> None:
        if value is not None:
            out.append(Fact(id=fact_id(rec.code, name), name=name, value=value, citation=cite))

    add("category", rec.category, meta)
    add("plan", rec.plan, meta)
    if rec.option:
        add("option", rec.option, meta)
    add("expense_ratio", rec.expense_ratio, meta)
    add("aum_cr", rec.aum_cr, meta)
    add("rating", rec.rating, meta)
    if rec.amc:
        add("amc", rec.amc, meta)
    if rec.manager:
        add("manager", rec.manager, meta)
    if rec.manager_since:
        add("manager_tenure_years", round((as_of - rec.manager_since).days / 365.25, 2), meta)
    if rec.ytm is not None:
        add("ytm", rec.ytm, meta)
    if rec.modified_duration is not None:
        add("modified_duration", rec.modified_duration, meta)
    if rec.avg_maturity_years is not None:
        add("avg_maturity_years", rec.avg_maturity_years, meta)
    if rec.launch_date:
        add("track_years", round((as_of - rec.launch_date).days / 365.25, 2), meta)
    if stats:
        calc = Citation(
            source="computed:nav_stats", as_of=stats.as_of, url_or_doc_id=rec.code,
            snippet=f"{stats.n_points} NAV points",
        )
        for name in (
            "cagr_3y", "cagr_5y", "roll_3y_median", "volatility", "max_drawdown", "sharpe",
            "sortino", "pct_roll_3y", "pct_cagr_5y", "alpha", "beta", "pct_low_vol",
        ):
            val = getattr(stats, name)
            add(name, None if val is None else round(val, 2), calc)
    for i, d in enumerate(docs):
        add(f"doc_{i}", d.chunk.text.split("\n", 1)[-1][:200], d.citation)
    return out
