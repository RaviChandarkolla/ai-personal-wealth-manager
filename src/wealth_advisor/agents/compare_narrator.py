"""Grounded summary narrator for head-to-head fund comparisons."""

from typing import Literal

from ..domain.fund_compare import ComparisonResult
from ..guardrails import compliance
from ..guardrails.grounding import check_text
from ..llm.base import LLMClient
from ..models.core import Citation, Fact

_SYSTEM = (
    "You are a SEBI-compliant mutual-fund comparison analyst. "
    "Write a concise 2-to-4 sentence neutral verdict summarizing the head-to-head comparison "
    "using ONLY the numbers and fund names in the provided FACTS table. "
    "Cite the supporting fact id in square brackets like [CODE:metric] after every figure you mention. "
    "Never invent numbers, never Compute or alter scores, and never use promotional language "
    "such as guaranteed, risk-free, assured, or multibagger."
)


def comparison_facts(result: ComparisonResult) -> list[Fact]:
    """Return all facts for both funds plus the deterministic comparison scores."""
    a, b = result.fund_a, result.fund_b
    score_facts = [
        Fact(
            id=f"{a.code}:compare_score",
            name=f"{a.name} comparison score",
            value=result.scores[a.code],
            citation=Citation(source="fund_compare", as_of=result.as_of, url_or_doc_id=f"compare:{a.code}"),
        ),
        Fact(
            id=f"{b.code}:compare_score",
            name=f"{b.name} comparison score",
            value=result.scores[b.code],
            citation=Citation(source="fund_compare", as_of=result.as_of, url_or_doc_id=f"compare:{b.code}"),
        ),
    ]
    seen: set[str] = set()
    out: list[Fact] = []
    for f in [*a.facts, *b.facts, *score_facts]:
        if f.id not in seen:
            out.append(f)
            seen.add(f.id)
    return out


def template_verdict(result: ComparisonResult) -> str:
    """Deterministic grounded summary paragraph used as fallback (or when no LLM is configured)."""
    a, b = result.fund_a, result.fund_b
    sa = result.scores[a.code]
    sb = result.scores[b.code]

    parts: list[str] = []
    if result.winner == "tie":
        parts.append(
            f"{a.name} ({sa} [{a.code}:compare_score]) and {b.name} ({sb} [{b.code}:compare_score]) "
            "finish in a statistical tie across the evaluated criteria."
        )
    elif result.winner == "A":
        parts.append(
            f"{a.name} ranks ahead with a deterministic score of {sa} [{a.code}:compare_score] "
            f"versus {sb} [{b.code}:compare_score] for {b.name}."
        )
    else:
        parts.append(
            f"{b.name} ranks ahead with a deterministic score of {sb} [{b.code}:compare_score] "
            f"versus {sa} [{a.code}:compare_score] for {a.name}."
        )

    scored = [r for r in result.criteria if r.status == "scored"]
    highlights: list[str] = []
    for r in scored:
        if r.id in ("roll_3y_median", "sharpe", "expense_ratio", "max_drawdown") and r.fact_id_a and r.fact_id_b:
            highlights.append(
                f"{r.label}: {a.name} is {r.value_a}{r.unit} [{r.fact_id_a}] vs "
                f"{b.name} at {r.value_b}{r.unit} [{r.fact_id_b}]"
            )
        if len(highlights) >= 2:
            break
    if highlights:
        parts.append("; ".join(highlights) + ".")

    if not result.same_category:
        parts.append(
            "Because the schemes belong to different categories, only category-neutral "
            "risk-adjusted and structural metrics were scored."
        )
    return " ".join(parts)


class CompareNarrator:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def narrate(
        self,
        result: ComparisonResult,
        *,
        narrative: bool = True,
    ) -> tuple[str, Literal["llm", "template", "disabled"]]:
        """Generate a grounded comparison verdict.

        If `narrative=False`, skips the LLM completely and returns ("", "disabled").
        If `llm` is provided, validates output with `check_text(..., require_citation_ids=True)`,
        retries once on failure, and falls back to `template_verdict`.
        """
        if not narrative:
            return "", "disabled"

        fallback = template_verdict(result)
        if self.llm is None:
            return fallback, "template"

        facts = comparison_facts(result)
        a, b = result.fund_a, result.fund_b
        known_names = {a.name, b.name}
        fact_lines = "\n".join(f"  [{f.id}] {f.name} = {f.value}" for f in facts)
        crit_lines = "\n".join(
            f"  {r.label} ({r.session_ref}): {a.name}={r.value_a}{r.unit}, "
            f"{b.name}={r.value_b}{r.unit}, winner={r.winner}"
            for r in result.criteria
            if r.status == "scored"
        )
        prompt = (
            f"Fund A: {a.name} ({a.category})\n"
            f"Fund B: {b.name} ({b.category})\n"
            f"Deterministic winner: {result.winner_name or 'Tie'}\n"
            f"Same category: {result.same_category}\n"
            f"FACTS:\n{fact_lines}\n"
            f"SCORED CRITERIA:\n{crit_lines}\n"
            "Write a short neutral summary paragraph citing [fact_id] for every number."
        )

        for attempt in range(2):
            try:
                raw = self.llm.complete(
                    [
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": prompt},
                    ]
                )
            except Exception:
                break
            cleaned, _ = compliance.scrub(raw)
            report = check_text(
                cleaned,
                facts,
                known_names=known_names,
                require_citation_ids=True,
            )
            if report.ok:
                return cleaned, "llm"
            prompt = (
                f"{prompt}\n\nYour previous draft failed grounding ({report.describe()}). "
                "Rewrite using ONLY numbers from FACTS and cite each with [fact_id]."
            )

        return fallback, "template"
