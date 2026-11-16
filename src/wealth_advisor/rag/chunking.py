"""Section-aware chunking: split on markdown headings, then on paragraphs past a size limit."""

import re

from pydantic import BaseModel


class Chunk(BaseModel):
    text: str
    section: str
    isin: str = ""
    doc_type: str = ""
    doc_id: str = ""
    as_of: str = ""  # ISO date
    source_url: str = ""


class Document(BaseModel):
    doc_id: str
    text: str
    isin: str = ""
    doc_type: str = "factsheet"
    as_of: str
    source_url: str = ""


def chunk_document(doc: Document, max_chars: int = 800) -> list[Chunk]:
    title = ""
    sections: list[tuple[str, list[str]]] = []
    for line in doc.text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            if len(m.group(1)) == 1 and not title:
                title = m.group(2).strip()
            sections.append((m.group(2).strip(), []))
        elif sections:
            sections[-1][1].append(line)
        elif line.strip():
            sections.append(("", [line]))
    chunks: list[Chunk] = []
    for name, lines in sections:
        paragraphs = [p.strip() for p in "\n".join(lines).split("\n\n") if p.strip()]
        buf = ""
        pieces: list[str] = []
        for p in paragraphs:
            if buf and len(buf) + len(p) > max_chars:
                pieces.append(buf)
                buf = ""
            buf = f"{buf}\n\n{p}".strip()
        if buf:
            pieces.append(buf)
        for piece in pieces:
            prefix = f"{title} - {name}" if title and name != title else (name or title)
            chunks.append(
                Chunk(
                    text=f"{prefix}\n{piece}".strip(), section=name, isin=doc.isin,
                    doc_type=doc.doc_type, doc_id=doc.doc_id, as_of=doc.as_of,
                    source_url=doc.source_url,
                )
            )
    return chunks
