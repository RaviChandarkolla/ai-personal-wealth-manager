"""360° stock analyst: deterministic sections (Python) + LLM commentary in four parts.

The LLM never supplies numbers; it only writes judgement text that must cite the fact ids it is
given. Ungrounded text is retried once and then replaced by a deterministic sentence.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, Field

from ..data.web_research import WebResearchResult
from ..domain.stock360.assemble import Assembled, ReportContext, assemble
from ..domain.stock360.mechanical import clamp_action
from ..domain.stock360.models import FinalVerdict, Metric, Report360, Section, WebSource
from ..domain.stock360.template import SECTION_BY_ID, SECTIONS, TEMPLATE_VERSION
from ..guardrails.compliance import scrub
from ..guardrails.disclosures import STOCK_DISCLOSURE
from ..guardrails.grounding import check_text
from ..llm.base import BudgetedLLM, LLMBudgetExceeded, LLMClient
from ..llm.langchain_client import flush_langfuse, trace_context
from ..models.core import Citation, Fact

log = logging.getLogger(__name__)

REPORT_DISCLOSURE = (
    "This 360° report is an educational analysis built from the listed data and web sources. Numbers marked "
    "'Not available' or 'Requires live verification' were not fetched and are not estimated. Famous-investor "
    "views are simulated applications of their published frameworks, not their actual statements. "
    "It is not investment advice."
)
PARTS: dict[int, list[str]] = {n: [s.id for s in SECTIONS if s.part == n] for n in (1, 2, 3, 4)}


# ------------------------------------------------------------------ LLM output schemas
class SectionText(BaseModel):
    id: str
    commentary: str
    verdict: str | None = None


class FinalDraft(BaseModel):
    action: str
    thesis: str


class Part1Output(BaseModel):
    sections: list[SectionText] = Field(default_factory=list)


class Part2Output(BaseModel):
    sections: list[SectionText] = Field(default_factory=list)


class Part3Output(BaseModel):
    sections: list[SectionText] = Field(default_factory=list)


class Part4Output(BaseModel):
    sections: list[SectionText] = Field(default_factory=list)
    final: FinalDraft | None = None


PART_SCHEMAS: dict[int, type[BaseModel]] = {1: Part1Output, 2: Part2Output, 3: Part3Output, 4: Part4Output}

SYSTEM = (
    "You are an equity research writer producing ONE PART of an educational 360° stock report.\n"
    "Rules:\n"
    "1. Use ONLY the numbered facts given. Cite them as [F12] or [F12, F13]. Never write a number that is not in a fact.\n"
    "2. Facts marked 'Not available' or 'Requires live verification' must be reported as such - never guess or estimate them.\n"
    "3. You write judgement and interpretation, not computations: Python already computed every figure.\n"
    "4. Any investor/legend quote must be labelled '(simulated)'; do not present it as an actual statement.\n"
    "5. For Fisher-style judgement, evaluate only the points that the facts support and say what is unknown.\n"
    "6. Banks/NBFCs: judge on NIM/NPA/CAR/P-B/ROE, not debt or FCF. Cyclical firms: judge on mid-cycle earnings.\n"
    "7. No guarantees, no 'risk-free', no certainty language. This is educational, not advice.\n"
    "8. Return one entry per requested section id, 2-6 sentences each. Keep each under 900 characters."
)


# ------------------------------------------------------------------ fact pack
class FactPack:
    def __init__(self, a: Assembled, name: str, as_of: date) -> None:
        self.facts: list[Fact] = []
        self.lines: dict[str, list[str]] = {}
        self.by_metric: dict[str, str] = {}
        self.names = {name}
        for s in a.sections:
            lines = self.lines.setdefault(s.id, [])
            for m in s.metrics:
                lines.append(self._metric(m, as_of))
            for t in s.tables:
                for ri, row in enumerate(t.rows[:12]):
                    label = str(row[0])
                    for ci, v in enumerate(row[1:], 1):
                        if isinstance(v, int | float) and not isinstance(v, bool):
                            fid = f"F{len(self.facts) + 1}"
                            nm = f"{t.title} / {label} / {t.columns[ci] if ci < len(t.columns) else ci}"
                            self.facts.append(Fact(id=fid, name=nm, value=v, citation=Citation(
                                source="computed", as_of=as_of, snippet=nm)))
                            lines.append(f"[{fid}] {nm} = {v}")

    def _metric(self, m: Metric, as_of: date) -> str:
        fid = f"F{len(self.facts) + 1}"
        self.by_metric[m.id] = fid
        v: float | str = m.value if m.value is not None else "Not available"
        self.facts.append(Fact(id=fid, name=m.label, value=v, citation=Citation(
            source=m.source or m.status, as_of=m.as_of or as_of, url_or_doc_id=m.url, snippet=m.formula)))
        shown = f"{m.value}{(' ' + m.unit) if m.unit and m.value is not None else ''}" if m.value is not None else (
            "Not available" if m.status != "requires_live_verification" else "Requires live verification")
        src = f" | src={m.source}" if m.source else ""
        url = f" | {m.url}" if m.url else ""
        return f"[{fid}] {m.label} = {shown} ({m.status}{src}{url})"

    def text_for(self, ids: list[str]) -> str:
        return "\n".join(f"## {i}: {SECTION_BY_ID[i].title}\n" + "\n".join(self.lines.get(i, [])) for i in ids)


def fallback_text(sec: Section, pack: FactPack, reason: str) -> str:
    """Deterministic sentence built only from the section's own computed facts."""
    parts = []
    for m in sec.metrics[:6]:
        if m.value is not None and m.status not in ("unavailable", "requires_live_verification"):
            parts.append(f"{m.label} {m.value}{(' ' + m.unit) if m.unit else ''} [{pack.by_metric.get(m.id, '')}]")
    body = ("Key figures: " + "; ".join(parts) + ".") if parts else "No computed figures were available for this section."
    return f"{body} ({reason})"


# ------------------------------------------------------------------ analyst
class Stock360Analyst:
    def __init__(self, llm: LLMClient | None, *, max_llm_calls: int = 8, run_id: str | None = None) -> None:
        self.llm = BudgetedLLM(llm, max_llm_calls) if llm is not None and not isinstance(llm, BudgetedLLM) else llm
        self.run_id = run_id

    @property
    def calls(self) -> int:
        return getattr(self.llm, "calls", 0) if self.llm is not None else 0

    # -------------------------------------------------------------- one part
    def _prompt(self, part: int, a: Assembled, pack: FactPack, done: dict[str, str], name: str,
                errors: dict[str, str] | None) -> list[dict[str, str]]:
        ids = PARTS[part]
        req = "\n".join(f"- {i}: " + ", ".join(SECTION_BY_ID[i].items) for i in ids)
        prior = "\n".join(f"{k}: {v[:200]}" for k, v in done.items()) or "(none yet)"
        head = (f"Company: {name}. Sector adapter: {a.adapter}. Part {part} of 4. Quant score {a.score}/100.\n"
                f"Write commentary for these section ids and the items they must cover:\n{req}\n")
        if part == 4:
            head += ("Also return `final`: action (one of SELL, AVOID, REDUCE, HOLD, ACCUMULATE, BUY, STRONG BUY) "
                     "and a thesis of 3-5 sentences with citations.\n")
        user = f"{head}\nEarlier parts (summary):\n{prior}\n\nFACTS:\n{pack.text_for(ids)}"
        if errors:
            user += "\n\nYour previous answer failed validation. Fix these and keep everything else:\n" + "\n".join(
                f"- {k}: {v}" for k, v in errors.items())
        return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]

    def _validate(self, text: str, pack: FactPack) -> str:
        return check_text(text, pack.facts, pack.names).describe()

    def _run_part(self, part: int, a: Assembled, pack: FactPack, done: dict[str, str], name: str) -> tuple[Any, set[str], list[str]]:
        """Returns (output or None, bad ids, warnings). One retry on grounding errors."""
        warns: list[str] = []
        errors: dict[str, str] | None = None
        out = None
        for attempt in (0, 1):
            try:
                with trace_context(run_id=self.run_id, step=f"stock360_part{part}"):
                    out = self.llm.complete(self._prompt(part, a, pack, done, name, errors), PART_SCHEMAS[part])  # type: ignore[union-attr]
            except LLMBudgetExceeded:
                warns.append(f"part {part}: LLM budget exhausted")
                break
            except Exception as exc:  # noqa: BLE001 - any provider/schema failure -> mechanical fallback
                log.warning("stock360 part %s failed: %s", part, exc)
                warns.append(f"part {part}: LLM call failed ({type(exc).__name__})")
                out = None
                break
            errors = {}
            for st in out.sections:
                if st.id not in PARTS[part]:
                    continue
                err = self._validate(st.commentary + " " + (st.verdict or ""), pack)
                if err:
                    errors[st.id] = err
            if part == 4 and out.final:
                err = self._validate(out.final.thesis, pack)
                if err:
                    errors["final"] = err
            missing = [i for i in PARTS[part] if i not in {s.id for s in out.sections}]
            for i in missing:
                errors[i] = "section missing from your answer"
            if not errors:
                return out, set(), warns
            if attempt == 0:
                continue
        if out is not None:
            warns.append(f"part {part}: some text stayed ungrounded after retry; deterministic text used: {sorted(errors or {})}")
        return out, set(errors or {}) if out is not None else set(), warns

    # -------------------------------------------------------------- orchestrate
    def build(self, ctx: ReportContext, *, web: WebResearchResult | None = None, name: str = "",
              data_mode: str = "synthetic", report_id: str | None = None,
              progress: Callable[[str, int], None] | None = None) -> Report360:
        if web is not None:
            ctx.web_facts = list(web.facts)
        a = assemble(ctx)
        name = name or ctx.snapshot.name or ctx.symbol
        as_of = date.fromisoformat(ctx.bars[-1].date[:10]) if ctx.bars else ctx.today
        pack = FactPack(a, name, as_of)
        pack.names |= {p.name for p in ctx.peers if p.name}
        secs = {s.id: s for s in a.sections}
        warnings = list(a.warnings) + (list(web.warnings) if web else [])
        done: dict[str, str] = {}
        final_draft: FinalDraft | None = None
        for part in (1, 2, 3, 4):
            if progress:
                progress(f"writing part {part} of 4", 40 + part * 12)
            out = None
            bad: set[str] = set()
            if self.llm is not None:
                out, bad, w = self._run_part(part, a, pack, done, name)
                warnings += w

            texts = {s.id: s for s in out.sections} if out is not None else {}
            for sid in PARTS[part]:
                sec, st = secs[sid], texts.get(sid)
                if st is None or sid in bad:
                    reason = "LLM commentary unavailable" if out is None else "LLM text failed grounding; replaced"
                    sec.commentary = fallback_text(sec, pack, reason)
                else:
                    clean, _ = scrub(_label_quotes(st.commentary))
                    sec.commentary = clean
                    sec.verdict = scrub(st.verdict)[0] if st.verdict else None
                done[sid] = sec.commentary
            if part == 4 and out is not None and out.final and "final" not in bad:
                final_draft = out.final
        flush_langfuse()
        report = self._finalize(a, ctx, secs, final_draft, pack, web, name, data_mode, report_id, warnings, as_of)
        return report

    def _finalize(self, a: Assembled, ctx: ReportContext, secs: dict[str, Section], draft: FinalDraft | None,
                  pack: FactPack, web: WebResearchResult | None, name: str, data_mode: str,
                  report_id: str | None, warnings: list[str], as_of: date) -> Report360:
        mech = a.mech
        action, reason = mech.action, ""
        thesis = ""
        if draft is not None:
            action, reason = clamp_action(draft.action, mech.action)
            thesis = scrub(draft.thesis)[0]
        else:
            thesis = (f"Mechanical verdict only: quant score {mech.score}/100 ({mech.stars} stars) maps to {mech.action}. "
                      "LLM commentary was unavailable.")
            warnings.append("final verdict is mechanical only")
        if action in ("SELL", "AVOID"):
            fired = [f"{lg}: {v}" for lg, v in mech.legend_verdicts.items() if v in ("SELL", "REDUCE")]
            if fired:
                thesis += " Triggering legend signals - " + "; ".join(fired) + "."
        for s in secs.values():
            if s.commentary and not s.missing and s.status != "unavailable":
                s.status = "complete"
        report = Report360(
            report_id=report_id or uuid.uuid4().hex[:12], symbol=ctx.symbol.upper(), name=name,
            data_mode="real" if data_mode == "real" else "synthetic", template_version=TEMPLATE_VERSION,
            generated_at=datetime.now(UTC), as_of=as_of, sector_adapter=a.adapter,  # type: ignore[arg-type]
            sections=[secs[s.id] for s in SECTIONS], mechanical=mech,
            final=FinalVerdict(action=action, stars=mech.stars, thesis=thesis, clamped_reason=reason),
            sources=_sources(ctx), llm_calls=self.calls, web_searches=web.searches if web else 0,
            warnings=warnings, disclosures=[STOCK_DISCLOSURE, REPORT_DISCLOSURE],
            charts=a.values.get("charts", {}),
        )
        report.warnings += completeness(report)
        return report


def _label_quotes(text: str) -> str:
    """A quotation must be flagged as simulated (the report never fabricates real quotes)."""
    if any(q in text for q in ('"', "“", "”")) and "(simulated)" not in text.lower():
        return text + " (simulated - not an actual quote)"
    return text


def _sources(ctx: ReportContext) -> list[WebSource]:
    seen: dict[str, WebSource] = {}
    for f in ctx.web_facts:
        seen.setdefault(f.source_url, WebSource(url=f.source_url, publisher=f.publisher, published_at=f.as_of))
    return list(seen.values())


def completeness(r: Report360) -> list[str]:
    """Every template section must be present with some content."""
    have = {s.id: s for s in r.sections}
    out = []
    for sp in SECTIONS:
        s = have.get(sp.id)
        if s is None:
            out.append(f"section {sp.id} missing from report")
        elif not (s.commentary or s.metrics or s.tables):
            out.append(f"section {sp.id} is empty")
    return out
