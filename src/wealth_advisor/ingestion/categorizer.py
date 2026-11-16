"""M14: Transaction categorizer — rules first, LLM fallback, user-confirmation queue.

Guarantees:
1. Deterministic regex/keyword rules run first with high confidence (>= 0.85).
2. Unmatched descriptions fall back to an optional `LLMClient` ONLY after passing
   through `scrub_text(...)` so raw PII never reaches LLM prompts or logs.
3. Ambiguous transactions (`confidence < confirmation_threshold`) are placed in
   `ConfirmationQueue` for explicit user confirmation.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol

from wealth_advisor.db import connect_sqlite, ensure_schema
from wealth_advisor.ingestion.parsers import RawTransaction
from wealth_advisor.ingestion.pii import scrub_text

TransactionCategory = Literal[
    "salary",
    "other_income",
    "emi",
    "rent",
    "sip",
    "investment",
    "utilities",
    "discretionary",
    "transfer",
    "ambiguous",
]

VALID_CATEGORIES: frozenset[str] = frozenset(
    {
        "salary",
        "other_income",
        "emi",
        "rent",
        "sip",
        "investment",
        "utilities",
        "discretionary",
        "transfer",
        "ambiguous",
    }
)


@dataclass(frozen=True)
class CategorizedTransaction:
    txn: RawTransaction
    category: TransactionCategory
    confidence: float
    source: Literal["rule", "llm", "user_confirmed", "default"]
    needs_confirmation: bool


@dataclass(frozen=True)
class ConfirmationQueueItem:
    item_id: str
    profile_id: str
    txn: RawTransaction
    suggested_category: TransactionCategory
    confidence: float
    status: Literal["pending", "confirmed"] = "pending"
    confirmed_category: TransactionCategory | None = None


class CategorizerLLM(Protocol):
    """Protocol for optional LLM fallback categorizer (receives PII-redacted text only)."""

    def classify_transaction(self, redacted_description: str, txn_type: str) -> tuple[TransactionCategory, float]: ...


# ── Deterministic Rules (credit vs debit) ─────────────────────────────────

_CREDIT_RULES: tuple[tuple[re.Pattern[str], TransactionCategory, float], ...] = (
    (re.compile(r"\b(SALARY|PAYROLL|SAL\s+CR|NEFT[-/\s]*SALARY|STIPEND)\b", re.I), "salary", 0.96),
    (re.compile(r"\b(DIVIDEND|INTEREST\s+CR|FD\s+INT|REFUND|CASHBACK|FREELANCE|CONSULTING)\b", re.I), "other_income", 0.88),
    (re.compile(r"\b(SELF\s+TRANSFER|OWN\s+ACCOUNT)\b", re.I), "transfer", 0.90),
)

_DEBIT_RULES: tuple[tuple[re.Pattern[str], TransactionCategory, float], ...] = (
    (re.compile(r"\b(SIP|CAMS|KFINTECH|ZERODHA\s+COIN|GROWW\s+MF|KUVERA|MF\s+PURCHASE|NACH[-/\s]*MF|ACH[-/\s]*MF)\b", re.I), "sip", 0.95),
    (re.compile(r"\b(EMI|HOME\s+LOAN|CAR\s+LOAN|PERSONAL\s+LOAN|BAJAJ\s+FIN|HDFC\s+LTD\s+LOAN|LOAN\s+REPAY|ACH\s+D[-/\s]*LOAN)\b", re.I), "emi", 0.95),
    (re.compile(r"\b(RENT|HOUSE\s+RENT|LANDLORD|NOBROKER\s+RENT|CRED\s+RENT)\b", re.I), "rent", 0.92),
    (re.compile(r"\b(ELECTRICITY|BESCOM|TATA\s+POWER|BROADBAND|AIRTEL|JIO\s+FIBER|WATER\s+BILL|GAS\s+BILL|MAINTENANCE|INSURANCE\s+PREM|LIC)\b", re.I), "utilities", 0.90),
    (re.compile(r"\b(ZERODHA\s+BROKING|UPSTOX|SGB|FIXED\s+DEPOSIT|FD\s+BOOKING|NPS|PPF)\b", re.I), "investment", 0.90),
    (re.compile(r"\b(SWIGGY|ZOMATO|AMAZON|FLIPKART|MYNTRA|NETFLIX|SPOTIFY|UBER|OLA|PVR|BOOKMYSHOW|DINING|POS\s+|RESTAURANT)\b", re.I), "discretionary", 0.88),
    (re.compile(r"\b(SELF\s+TRANSFER|TO\s+OWN\s+ACCT)\b", re.I), "transfer", 0.90),
)


class ConfirmationQueue:
    """Queue for ambiguous transactions requiring user confirmation (SQLite-backed)."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS transaction_confirmation_queue (
        item_id             TEXT PRIMARY KEY,
        profile_id          TEXT NOT NULL,
        txn_json            TEXT NOT NULL,
        suggested_category  TEXT NOT NULL,
        confidence          REAL NOT NULL,
        status              TEXT NOT NULL,
        confirmed_category  TEXT,
        updated_at          TEXT NOT NULL
    );
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn: sqlite3.Connection = connect_sqlite(db_path)
        ensure_schema(self._conn)
        self._conn.executescript(self._DDL)
        self._conn.commit()

    def enqueue(
        self,
        *,
        profile_id: str,
        txn: RawTransaction,
        suggested_category: TransactionCategory,
        confidence: float,
    ) -> ConfirmationQueueItem:
        item_id = f"{profile_id}:{txn.txn_id}"
        redacted_txn = replace(
            txn,
            description=scrub_text(txn.description),
            reference=scrub_text(txn.reference),
        )
        payload = {
            "txn_id": redacted_txn.txn_id,
            "txn_date": redacted_txn.txn_date.isoformat(),
            "description": redacted_txn.description,
            "amount_inr": redacted_txn.amount_inr,
            "txn_type": redacted_txn.txn_type,
            "closing_balance_inr": redacted_txn.closing_balance_inr,
            "bank_format": redacted_txn.bank_format,
            "reference": redacted_txn.reference,
        }
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO transaction_confirmation_queue
                (item_id, profile_id, txn_json, suggested_category, confidence, status, confirmed_category, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    suggested_category = excluded.suggested_category,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (item_id, profile_id, json.dumps(payload), suggested_category, confidence, now),
            )
        return ConfirmationQueueItem(
            item_id=item_id,
            profile_id=profile_id,
            txn=redacted_txn,
            suggested_category=suggested_category,
            confidence=confidence,
            status="pending",
            confirmed_category=None,
        )

    def list_pending(self, profile_id: str | None = None) -> list[ConfirmationQueueItem]:
        if profile_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM transaction_confirmation_queue WHERE profile_id = ? AND status = 'pending' ORDER BY item_id",
                (profile_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM transaction_confirmation_queue WHERE status = 'pending' ORDER BY item_id"
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def confirm(self, item_id: str, confirmed_category: TransactionCategory) -> ConfirmationQueueItem:
        if confirmed_category not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {confirmed_category!r}")
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            cur = self._conn.execute(
                """
                UPDATE transaction_confirmation_queue
                SET status = 'confirmed', confirmed_category = ?, confidence = 1.0, updated_at = ?
                WHERE item_id = ?
                """,
                (confirmed_category, now, item_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"Confirmation queue item not found: {item_id!r}")
        row = self._conn.execute(
            "SELECT * FROM transaction_confirmation_queue WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        return self._row_to_item(row)

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> ConfirmationQueueItem:
        from datetime import date as _date

        data = json.loads(row["txn_json"])
        txn = RawTransaction(
            txn_id=data["txn_id"],
            txn_date=_date.fromisoformat(data["txn_date"]),
            description=data["description"],
            amount_inr=float(data["amount_inr"]),
            txn_type=data["txn_type"],
            closing_balance_inr=data.get("closing_balance_inr"),
            bank_format=data.get("bank_format", "generic"),
            reference=data.get("reference", ""),
        )
        return ConfirmationQueueItem(
            item_id=row["item_id"],
            profile_id=row["profile_id"],
            txn=txn,
            suggested_category=row["suggested_category"],
            confidence=float(row["confidence"]),
            status=row["status"],
            confirmed_category=row["confirmed_category"],
        )


class TransactionCategorizer:
    """Categorizes bank transactions using deterministic rules -> PII-redacted LLM fallback -> confirmation queue."""

    def __init__(
        self,
        *,
        llm_fallback: CategorizerLLM | None = None,
        confirmation_queue: ConfirmationQueue | None = None,
        confirmation_threshold: float = 0.70,
    ) -> None:
        self._llm = llm_fallback
        self._queue = confirmation_queue
        self._threshold = confirmation_threshold

    def categorize_one(
        self,
        txn: RawTransaction,
        *,
        profile_id: str = "default_user",
    ) -> CategorizedTransaction:
        safe_desc = scrub_text(txn.description)
        safe_txn = replace(txn, description=safe_desc, reference=scrub_text(txn.reference))

        rules = _CREDIT_RULES if safe_txn.txn_type == "credit" else _DEBIT_RULES
        for pattern, category, conf in rules:
            if pattern.search(safe_desc):
                return CategorizedTransaction(
                    txn=safe_txn,
                    category=category,
                    confidence=conf,
                    source="rule",
                    needs_confirmation=False,
                )

        # LLM fallback (only receives PII-scrubbed description)
        if self._llm is not None:
            llm_cat, llm_conf = self._llm.classify_transaction(safe_desc, safe_txn.txn_type)
            needs_conf = llm_conf < self._threshold or llm_cat == "ambiguous"
            result = CategorizedTransaction(
                txn=safe_txn,
                category=llm_cat,
                confidence=llm_conf,
                source="llm",
                needs_confirmation=needs_conf,
            )
            if needs_conf and self._queue is not None:
                self._queue.enqueue(
                    profile_id=profile_id,
                    txn=safe_txn,
                    suggested_category=llm_cat,
                    confidence=llm_conf,
                )
            return result

        # Default fallback: ambiguous -> needs user confirmation
        default_cat: TransactionCategory = "other_income" if safe_txn.txn_type == "credit" else "ambiguous"
        default_conf = 0.45
        if self._queue is not None:
            self._queue.enqueue(
                profile_id=profile_id,
                txn=safe_txn,
                suggested_category=default_cat,
                confidence=default_conf,
            )
        return CategorizedTransaction(
            txn=safe_txn,
            category=default_cat,
            confidence=default_conf,
            source="default",
            needs_confirmation=True,
        )

    def categorize_many(
        self,
        transactions: list[RawTransaction] | tuple[RawTransaction, ...],
        *,
        profile_id: str = "default_user",
    ) -> list[CategorizedTransaction]:
        return [self.categorize_one(t, profile_id=profile_id) for t in transactions]
