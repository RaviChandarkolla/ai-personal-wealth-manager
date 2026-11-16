"""Synthetic fact-sheet corpus generated from the demo fund records, plus a recall@k eval."""

from datetime import date

from ..data.store import FundRecord, MetadataStore
from .chunking import Document
from .index import VectorIndex

_EXIT_LOAD = {
    "Liquid": "Graded exit load of 0.0070% on day 1 reducing to nil from day 7 onwards.",
    "Short Duration": "Nil exit load.",
    "Corporate Bond": "Nil exit load.",
    "Conservative Hybrid": "1% if redeemed within 1 year of allotment, nil thereafter.",
    "Gold ETF": "Nil exit load; units are traded on the exchange.",
}
_OBJECTIVE = {
    "Liquid": "parking surplus cash for days to weeks in money market instruments",
    "Short Duration": "income over 1-3 years from short maturity debt securities",
    "Corporate Bond": "accrual income from high-rated corporate bonds",
    "Conservative Hybrid": "regular income with limited equity participation",
    "Index Fund": "returns tracking a broad market index at low cost",
    "Large Cap": "long-term growth from large capitalisation companies",
    "Flexi Cap": "long-term growth from a flexible mix of market capitalisations",
    "Mid Cap": "long-term growth from mid capitalisation companies",
    "Gold ETF": "returns that track the domestic price of physical gold",
}


def factsheet_for(f: FundRecord) -> Document:
    exit_load = _EXIT_LOAD.get(f.category, "1% if redeemed within 1 year of allotment, nil thereafter.")
    text = f"""# {f.name}

## Investment Objective
{f.name} aims at {_OBJECTIVE.get(f.category, 'long-term returns')}. Category: {f.category}.
Benchmark: {f.benchmark}.

## Fees and Load
Exit load for {f.name}: {exit_load}

## Risks
Market risk, credit risk and interest-rate risk apply as per the category. Past performance
is not indicative of future results.

## Fund Management
The scheme is managed by {f.manager} at {f.amc}.
"""
    source_url = (
        f"demo://factsheet/{f.code}"
        if f.source == "synthetic_demo"
        else f"https://api.mfapi.in/mf/{f.code}"
    )
    return Document(
        doc_id=f"factsheet-{f.code}", text=text, isin=f.isin, doc_type="factsheet",
        as_of=f.as_of.isoformat(), source_url=source_url,
    )


def circular_doc(as_of: date) -> Document:
    text = """# SEBI circular on mutual fund categorisation (demo summary)

## Categories
Schemes are grouped into debt, equity, hybrid and other categories so that investors can compare
like with like.

## Risk-o-meter
Every scheme must display a risk-o-meter and disclose the riskometer level in its factsheet.
"""
    return Document(
        doc_id="sebi-categorisation-demo", text=text, isin="", doc_type="circular",
        as_of=as_of.isoformat(), source_url="demo://sebi/categorisation",
    )


def build_demo_corpus(store: MetadataStore, index: VectorIndex) -> int:
    funds = store.query_funds()
    docs = [factsheet_for(f) for f in funds]
    as_of = funds[0].as_of if funds else date.today()
    docs.append(circular_doc(as_of))
    return index.ingest(docs)


def recall_at_k(index: VectorIndex, cases: list[tuple[str, str]], k: int = 3) -> float:
    """cases = (question, expected isin). Unfiltered search; hit if expected isin is in top k."""
    hits = sum(any(r.chunk.isin == isin for r in index.search(q, k=k)) for q, isin in cases)
    return hits / len(cases) if cases else 0.0
