"""Input guardrail v0: reject requests outside the recommend-only mutual-fund scope."""

import re

from pydantic import BaseModel

_RULES: list[tuple[str, str]] = [
    (r"\b(leverage[d]?|margin trading|borrow(ed)?\s+(money|to invest)|loan against)\b", "leverage"),
    (r"\bf\s?&\s?o\b|\bderivatives?\b|\b(call|put)\s+options?\b|\boptions?\s+trading\b"
     r"|\bfutures?\b", "derivatives"),
    (r"\b(intraday|day[- ]?trad\w*|scalping)\b", "intraday trading"),
    (r"\b(execute|place|buy|sell)\s+(this|the|my|an?)?\s*(trade|order)s?\b"
     r"|\bexecute\s+(it|now)\b", "trade execution"),
    (r"\b(crypto\w*|bitcoin|forex)\b", "unsupported asset class"),
    (
        r"\b(ignore\s+(all\s+|any\s+)?(previous|prior|above|system)\s+"
        r"(instructions?|prompts?|rules?|guardrails?)"
        r"|disregard\s+(all\s+|any\s+)?(previous|prior|above|system)"
        r"|reveal\s+(your\s+|the\s+)?system\s+prompt"
        r"|you\s+are\s+now\s+(in\s+)?(developer|jailbreak|unrestricted|dan)\b)\b",
        "prompt injection",
    ),
]


class GuardResult(BaseModel):
    allowed: bool
    reason: str = ""
    message: str = ""


def check_input(text: str) -> GuardResult:
    for pattern, reason in _RULES:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return GuardResult(
                allowed=False,
                reason=reason,
                message=(
                    f"Out of scope ({reason}). This assistant only recommends mutual-fund "
                    "allocations; it does not execute trades or advise on leverage or derivatives."
                ),
            )
    return GuardResult(allowed=True)
