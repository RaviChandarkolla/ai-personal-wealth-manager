"""Grounding validator: every number, fund name and citation in generated text must trace to a Fact."""

import re

from pydantic import BaseModel

from ..models.core import Fact

_CITE = re.compile(r"\[([^\[\]]+)\]")
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_PERIOD = re.compile(r"\b\d+\s*-?\s*(?:y|yr|yrs|year|years|month|months)\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
_FUND_NAME = re.compile(r"((?:[A-Z][\w&.-]*\s+){1,6}(?i:fund|etf|scheme|fof|reit|invit|sgb|deposit|fd|ltd)\b)")
_LEADING = {"the", "this", "a", "an", "our", "its", "best", "top", "strong", "good", "in", "for", "with"}



class GroundingReport(BaseModel):
    ungrounded_numbers: list[str] = []
    unknown_names: list[str] = []
    unknown_citations: list[str] = []

    @property
    def fabricated_funds(self) -> list[str]:
        return self.unknown_names

    @property
    def ok(self) -> bool:
        return not (self.ungrounded_numbers or self.unknown_names or self.unknown_citations)

    def describe(self) -> str:
        parts = []
        if self.ungrounded_numbers:
            parts.append(f"numbers not in the evidence: {self.ungrounded_numbers}")
        if self.unknown_names:
            parts.append(f"fund names not in the evidence: {self.unknown_names}")
        if self.unknown_citations:
            parts.append(f"unknown citation ids: {self.unknown_citations}")
        return "; ".join(parts)


def citations_in(text: str) -> list[str]:
    """Fact ids cited as [id] or [id1, id2]."""
    out: list[str] = []
    for m in _CITE.findall(text):
        out.extend(p.strip() for p in m.split(",") if p.strip())
    return out


def _numeric_values(facts: list[Fact]) -> list[float]:
    vals = []
    for f in facts:
        if isinstance(f.value, int | float) and not isinstance(f.value, bool):
            vals.append(float(f.value))
    return vals


def _matches(token: str, values: list[float]) -> bool:
    x = float(token)
    decimals = len(token.split(".")[1]) if "." in token else 0
    tol = 0.5 * 10**-decimals + 1e-9
    return any(abs(x - v) <= tol or abs(abs(x) - abs(v)) <= tol for v in values)


def check_text(
    text: str, facts: list[Fact], known_names: set[str], require_citation_ids: bool = False
) -> GroundingReport:
    """Numbers must match a fact value (to the precision written); names must be known;
    [ids] must be fact ids. Period labels like '5-year' and ISO dates are ignored."""
    fact_ids = {f.id for f in facts}
    cited = citations_in(text)
    unknown_cites = [c for c in cited if c not in fact_ids]
    body = _CITE.sub(" ", text)
    body = _ISO_DATE.sub(" ", body)
    body = _PERIOD.sub(" ", body)
    values = _numeric_values(facts)
    bad_numbers = [t for t in _NUMBER.findall(body) if not _matches(t, values)]

    known = {n.lower() for n in known_names}
    bad_names = []
    for m in _FUND_NAME.findall(body):
        words = m.split()
        while words and words[0].lower() in _LEADING:
            words.pop(0)
        cand = " ".join(words).lower()
        if len(words) >= 2 and not any(
            cand == k or cand in k or (len(k.split()) >= 2 and k in cand) for k in known
        ):
            bad_names.append(" ".join(words))
    if require_citation_ids and not cited:
        unknown_cites.append("<missing citation>")
    return GroundingReport(
        ungrounded_numbers=bad_numbers, unknown_names=bad_names, unknown_citations=unknown_cites
    )
