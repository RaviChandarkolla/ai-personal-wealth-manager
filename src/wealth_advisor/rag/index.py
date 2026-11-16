"""Versioned vector index in SQLite with hybrid retrieval: exact metadata filter, then cosine, then rerank."""

import sqlite3
from collections.abc import Sequence
from datetime import date, timedelta

import numpy as np
from pydantic import BaseModel

from ..config import get_settings
from ..models.core import Citation
from .chunking import Chunk, Document, chunk_document
from .embeddings import Embedder, HashingEmbedder

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collection (
    name TEXT NOT NULL, version INTEGER NOT NULL, embedder TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, PRIMARY KEY (name, version)
);
CREATE TABLE IF NOT EXISTS chunk (
    id INTEGER PRIMARY KEY AUTOINCREMENT, collection TEXT NOT NULL, version INTEGER NOT NULL,
    isin TEXT, doc_type TEXT, doc_id TEXT, as_of TEXT, source_url TEXT, section TEXT,
    text TEXT NOT NULL, emb BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS chunk_lookup ON chunk (collection, version, isin, doc_type);
"""

RECENCY_WEIGHT = 0.1


class Retrieved(BaseModel):
    chunk: Chunk
    score: float
    citation: Citation


class VectorIndex:
    def __init__(
        self, conn: sqlite3.Connection, collection: str = "mf_docs", embedder: Embedder | None = None
    ) -> None:
        self.conn = conn
        self.collection = collection
        self.embedder = embedder or HashingEmbedder(get_settings().embedding_dim)
        conn.executescript(_SCHEMA)

    def active_version(self) -> int | None:
        row = self.conn.execute(
            "SELECT version FROM collection WHERE name = ? AND active = 1", (self.collection,)
        ).fetchone()
        return row["version"] if row else None

    def ingest(self, docs: Sequence[Document]) -> int:
        """Write a NEW collection version and activate it; older versions stay on disk."""
        row = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM collection WHERE name = ?",
            (self.collection,),
        ).fetchone()
        version = row["v"] + 1
        chunks = [c for d in docs for c in chunk_document(d)]
        embs = self.embedder.embed([c.text for c in chunks]) if chunks else np.zeros((0, 1))
        with self.conn:
            self.conn.execute(
                "INSERT INTO collection VALUES (?,?,?,0,?)",
                (self.collection, version, self.embedder.name, date.today().isoformat()),
            )
            self.conn.executemany(
                "INSERT INTO chunk (collection, version, isin, doc_type, doc_id, as_of, source_url,"
                " section, text, emb) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (self.collection, version, c.isin, c.doc_type, c.doc_id, c.as_of, c.source_url,
                     c.section, c.text, e.astype(np.float32).tobytes())
                    for c, e in zip(chunks, embs, strict=True)
                ],
            )
            self.conn.execute("UPDATE collection SET active = 0 WHERE name = ?", (self.collection,))
            self.conn.execute(
                "UPDATE collection SET active = 1 WHERE name = ? AND version = ?",
                (self.collection, version),
            )
        return version

    def search(
        self,
        query: str,
        *,
        isin: str | None = None,
        doc_type: str | None = None,
        k: int = 4,
        as_of: date | None = None,
        max_age_days: int | None = None,
    ) -> list[Retrieved]:
        """Docs older than `max_age_days` (vs `as_of`) are dropped; newer ones get a small boost."""
        version = self.active_version()
        if version is None:
            return []
        max_age = get_settings().doc_max_age_days if max_age_days is None else max_age_days
        today = as_of or date.today()
        where, args = ["collection = ?", "version = ?"], [self.collection, version]
        if isin:
            where.append("isin = ?")
            args.append(isin)
        if doc_type:
            where.append("doc_type = ?")
            args.append(doc_type)
        where.append("as_of >= ?")
        args.append((today - timedelta(days=max_age)).isoformat())
        rows = self.conn.execute(f"SELECT * FROM chunk WHERE {' AND '.join(where)}", args).fetchall()
        if not rows:
            return []
        mat = np.vstack([np.frombuffer(r["emb"], dtype=np.float32) for r in rows])
        sims = mat @ self.embedder.embed([query])[0]
        scored: list[Retrieved] = []
        for r, sim in zip(rows, sims, strict=True):
            age = max(0, (today - date.fromisoformat(r["as_of"])).days)
            score = float(sim) + RECENCY_WEIGHT * (1 - min(age, max_age) / max_age)
            chunk = Chunk(
                text=r["text"], section=r["section"] or "", isin=r["isin"] or "",
                doc_type=r["doc_type"] or "", doc_id=r["doc_id"] or "", as_of=r["as_of"],
                source_url=r["source_url"] or "",
            )
            citation = Citation(
                source=chunk.doc_type or "document",
                as_of=date.fromisoformat(chunk.as_of),
                url_or_doc_id=chunk.source_url or chunk.doc_id,
                snippet=chunk.text[:200],
            )
            scored.append(Retrieved(chunk=chunk, score=round(score, 6), citation=citation))
        scored.sort(key=lambda x: (-x.score, x.chunk.doc_id, x.chunk.section))
        return scored[:k]
