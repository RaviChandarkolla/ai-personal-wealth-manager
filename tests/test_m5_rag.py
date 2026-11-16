from datetime import date

from tests.conftest import AS_OF

from wealth_advisor.data.store import MetadataStore
from wealth_advisor.db import open_db
from wealth_advisor.rag.chunking import Document, chunk_document
from wealth_advisor.rag.demo_corpus import recall_at_k
from wealth_advisor.rag.index import VectorIndex


def test_section_aware_chunks_carry_metadata():
    doc = Document(doc_id="d1", isin="INF1", doc_type="factsheet", as_of="2026-10-01",
                   text="# Orchid Fund\n\n## Fees\nExit load 1%.\n\n## Risks\nMarket risk.")
    chunks = chunk_document(doc)
    assert len(chunks) == 2
    fees = next(c for c in chunks if c.section == "Fees")
    assert "Orchid Fund - Fees" in fees.text and fees.isin == "INF1" and fees.as_of == "2026-10-01"


def test_ingest_is_versioned_and_does_not_overwrite():
    idx = VectorIndex(open_db(":memory:"))
    d1 = Document(doc_id="a", isin="I1", as_of="2026-10-01", text="# A\n## Fees\nexit load alpha")
    d2 = Document(doc_id="b", isin="I2", as_of="2026-10-01", text="# B\n## Fees\nexit load beta")
    assert idx.ingest([d1]) == 1
    assert idx.ingest([d2]) == 2
    assert idx.active_version() == 2
    rows = idx.conn.execute("SELECT version, COUNT(*) AS n FROM chunk GROUP BY version").fetchall()
    assert {r["version"]: r["n"] for r in rows} == {1: 1, 2: 1}
    assert [r.chunk.isin for r in idx.search("exit load", as_of=date(2026, 10, 2))] == ["I2"]


def test_metadata_filter_and_citations(demo_conn):
    idx = VectorIndex(demo_conn)
    hits = idx.search("exit load", isin="XSDEMO00000", k=3, as_of=AS_OF)
    assert hits and all(h.chunk.isin == "XSDEMO00000" for h in hits)
    assert all(h.citation.source == "factsheet" and h.citation.as_of == AS_OF for h in hits)
    assert hits[0].citation.url_or_doc_id


def test_stale_docs_dropped():
    idx = VectorIndex(open_db(":memory:"))
    idx.ingest([
        Document(doc_id="old", isin="I1", as_of="2024-01-01", text="# Old\n## Fees\nexit load"),
        Document(doc_id="new", isin="I1", as_of="2026-10-01", text="# New\n## Fees\nexit load"),
    ])
    hits = idx.search("exit load", as_of=date(2026, 10, 9), max_age_days=365)
    assert {h.chunk.doc_id for h in hits} == {"new"}
    assert idx.search("exit load", as_of=date(2026, 10, 9), max_age_days=3000)[0].chunk.doc_id == "new"


def test_retrieval_recall_at_k(demo_conn):
    funds = MetadataStore(demo_conn).query_funds()
    # 20 questions across categories/brands; expected answer is that fund's fact sheet
    picked = [f for f in funds if f.code.endswith(("0", "1", "2"))][:20]
    cases = [(f"What is the exit load of {f.name}?", f.isin) for f in picked]
    assert len(cases) == 20
    assert recall_at_k(VectorIndex(demo_conn), cases, k=3) >= 0.9
