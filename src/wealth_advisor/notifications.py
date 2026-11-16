"""M10 Notification abstraction (Console -> Email) for recommend-only rebalance alerts."""

from __future__ import annotations

import logging
import smtplib
from datetime import date
from email.message import EmailMessage
from typing import Callable, Protocol

from pydantic import BaseModel, Field

from .models.enums import DailyOutcome
from .portfolio import RebalanceTradeSuggestion

log = logging.getLogger(__name__)


class NotificationMessage(BaseModel):
    notification_id: str
    profile_id: str
    goal_id: str
    run_id: str
    as_of: date
    outcome: DailyOutcome
    subject: str
    body: str
    reasons: list[str] = Field(default_factory=list)
    trades: list[RebalanceTradeSuggestion] = Field(default_factory=list)
    exit_load_warnings: list[str] = Field(default_factory=list)
    disclosures: list[str] = Field(default_factory=list)
    requires_confirmation: bool = True


def format_notification_body(
    *,
    profile_id: str,
    goal_id: str,
    as_of: date,
    outcome: DailyOutcome,
    reasons: list[str],
    trades: list[RebalanceTradeSuggestion],
    exit_load_warnings: list[str],
    disclosures: list[str],
) -> str:
    lines: list[str] = [
        f"Daily Recalibration Update ({as_of.isoformat()}) — {outcome.value}",
        f"Profile: {profile_id} | Goal: {goal_id}",
        "",
    ]
    if reasons:
        lines.append("Trigger Reason(s):")
        for r in reasons:
            lines.append(f"  - {r}")
        lines.append("")

    if trades:
        lines.append("Proposed Rebalance Actions (Recommend-Only — User Confirmation Required):")
        for t in trades:
            sign = "+" if t.amount_delta >= 0 else "-"
            lines.append(
                f"  - [{t.action.value}] {t.name} ({t.instrument_id}, {t.bucket.value}): "
                f"Rs {t.current_amount:,.0f} -> Rs {t.target_amount:,.0f} "
                f"({sign}Rs {abs(t.amount_delta):,.0f})"
            )
        lines.append("")

    if exit_load_warnings:
        lines.append("Exit-Load & Tax-Impact Warnings:")
        for w in exit_load_warnings:
            lines.append(f"  ! {w}")
        lines.append("")

    lines.append(
        "Action Required: This is a recommend-only proposal. No trades are executed automatically. "
        "Please review and confirm to update your active target position."
    )

    if disclosures:
        lines.append("")
        lines.append("Disclosures:")
        for d in disclosures:
            lines.append(f"  * {d}")

    return "\n".join(lines)


class Notifier(Protocol):
    def send(self, message: NotificationMessage) -> None: ...


class ConsoleNotifier:
    """Console/log notification sink that also records sent messages for inspection."""

    def __init__(self, *, printer: Callable[[str], None] | None = None, quiet: bool = False) -> None:
        self.sent: list[NotificationMessage] = []
        self._printer = printer
        self._quiet = quiet

    def send(self, message: NotificationMessage) -> None:
        self.sent.append(message)
        formatted = f"[NOTIFICATION] {message.subject}\n{message.body}"
        log.info(formatted)
        if not self._quiet and self._printer is not None:
            self._printer(formatted)


class EmailNotifier:
    """Email notification channel (with offline outbox capture and optional SMTP dispatch)."""

    def __init__(
        self,
        *,
        to_address: str = "investor@example.com",
        from_address: str = "wealth-advisor@example.com",
        smtp_host: str | None = None,
        smtp_port: int = 587,
    ) -> None:
        self.to_address = to_address
        self.from_address = from_address
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sent: list[NotificationMessage] = []
        self.emails: list[EmailMessage] = []

    def send(self, message: NotificationMessage) -> None:
        self.sent.append(message)
        msg = EmailMessage()
        msg["Subject"] = message.subject
        msg["From"] = self.from_address
        msg["To"] = self.to_address
        msg.set_content(message.body)
        self.emails.append(msg)

        if self.smtp_host:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as client:
                client.send_message(msg)


class CompositeNotifier:
    """Fan-out notifier that dispatches to multiple channels (e.g., Console + Email)."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = list(notifiers)

    def send(self, message: NotificationMessage) -> None:
        for n in self.notifiers:
            n.send(message)
