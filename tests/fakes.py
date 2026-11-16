"""Test doubles for the MVP pipeline."""

import re

from pydantic import BaseModel

from wealth_advisor.agents.analyst import AnalystProse
from wealth_advisor.agents.mf_researcher import Pick, Selection


class ScriptedLLM:
    """Answers the research and analyst prompts from their own evidence tables."""

    model = "scripted"

    def __init__(
        self,
        max_picks: int = 5,
        bad_selection: Selection | None = None,
        *,
        mentioned_funds: list[str] | None = None,
        followup_answer: str | None = None,
    ) -> None:
        self.max_picks = max_picks
        self.bad_selection = bad_selection  # returned on the first selection call only
        self.mentioned_funds = mentioned_funds
        self.followup_answer = followup_answer
        self.calls = 0

    def complete(self, messages, schema: type[BaseModel] | None = None):
        self.calls += 1
        user = messages[-1]["content"] if messages[-1]["role"] == "user" else messages[0]["content"]
        if schema is Selection:
            if self.bad_selection is not None:
                sel, self.bad_selection = self.bad_selection, None
                return sel
            table = next(m["content"] for m in messages if m["content"].startswith("Candidates:"))
            codes = re.findall(r"^- (\w+) \|", table, flags=re.MULTILINE)
            pct = {m[0]: float(m[1]) for m in re.findall(
                r"^- (\w+) \|.*pct_roll_3y=([\d.]+)", table, flags=re.MULTILINE)}
            codes.sort(key=lambda c: -pct.get(c, -1.0))
            return Selection(picks=[Pick(scheme_code=c) for c in codes[: self.max_picks]])
        if schema is AnalystProse:
            ev = re.findall(r"^\[([^\]]+)\] (\w+) = ", user, flags=re.MULTILINE)
            ids = {name: i for i, name in ev}
            first = ids.get("expense_ratio") or ev[0][0]
            second = ids.get("max_drawdown") or ev[0][0]
            return AnalystProse(
                pros=[f"Low cost structure [{first}]"],
                cons=[f"Experienced a drawdown during the period [{second}]"],
                change_conditions=[f"Revisit if expense ratio rises [{first}]"],
            )
        if schema is not None and schema.__name__ == "ExtractedGoal":
            from wealth_advisor.agents.profiling import ExtractedGoal
            return ExtractedGoal(
                amount_text="10 lakhs",
                horizon_text="10 years",
                risk_preference="aggressive",
                purpose="wealth creation",
            )
        if schema is not None and schema.__name__ == "MentionedFunds":
            from wealth_advisor.agents.followup import MentionedFunds
            if self.mentioned_funds is not None:
                return MentionedFunds(fund_queries=list(self.mentioned_funds))
            codes = re.findall(r"\b(DEMO_[A-Za-z0-9_]+|\d{5,6})\b", user)
            return MentionedFunds(fund_queries=codes)
        if schema is not None and schema.__name__ == "FollowUpAnswerPayload":
            from wealth_advisor.agents.followup import FollowUpAnswerPayload
            ev_ids = re.findall(r"\[([A-Za-z0-9_:\-.]+)\]", user)
            if self.followup_answer is not None:
                return FollowUpAnswerPayload(answer=self.followup_answer, fact_ids=ev_ids[:3])
            lines = [
                ln.strip("- ").strip()
                for ln in user.splitlines()
                if ln.strip().startswith("- [")
            ]
            summary = " ".join(lines[:3]) if lines else "Grounded explanation from saved advice."
            return FollowUpAnswerPayload(
                answer=f"{summary} This explanation is for informational purposes only and is not investment advice.",
                fact_ids=ev_ids[:3],
            )
        raise AssertionError(f"unexpected schema {schema}")


class Stock360FakeLLM:
    """Answers the four 360 part prompts by citing the first fact of each section."""

    model = "stock360-fake"

    def __init__(self, *, bad_first: bool = False, always_bad: bool = False, action: str = "BUY",
                 quote: bool = False, fail: bool = False) -> None:
        self.bad_first, self.always_bad, self.action, self.quote, self.fail = bad_first, always_bad, action, quote, fail
        self.calls = 0
        self.prompts: list[str] = []

    def complete(self, messages, schema=None):
        from wealth_advisor.agents.stock_360_analyst import FinalDraft, SectionText

        self.calls += 1
        user = messages[-1]["content"]
        self.prompts.append(user)
        if self.fail:
            raise RuntimeError("boom")
        blocks = re.split(r"^## (\w+): ", user.split("FACTS:", 1)[1], flags=re.MULTILINE)[1:]
        secs = []
        for sid, body in zip(blocks[0::2], blocks[1::2], strict=True):
            m = re.search(r"\[(F\d+)\]", body)
            cite = f" [{m.group(1)}]" if m else ""
            txt = f"Assessment for this area is balanced{cite}."
            bad = self.always_bad or (self.bad_first and self.calls == 1)
            if bad:
                txt += " Value is 98765.43 now."
            if self.quote:
                txt += ' As he said, "be greedy when others are fearful".'
            secs.append(SectionText(id=sid, commentary=txt))
        out = schema(sections=secs)
        if hasattr(out, "final"):
            out.final = FinalDraft(action=self.action, thesis="Balanced view on the cited evidence.")
        return out

