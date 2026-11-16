"""M14 PII redaction and encrypted document storage with configurable retention.

Redacts Indian PAN, bank account numbers, Aadhaar, phone numbers, and emails before any LLM call
or log entry. Stores raw uploaded statements encrypted at rest using authenticated stream encryption
(PBKDF2-HMAC-SHA256 + HMAC-SHA256) with TTL retention purge.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel

# Indian PAN: 5 uppercase letters, 4 digits, 1 uppercase letter (e.g. ABCDE1234F)
_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
# Indian IFSC: 4 uppercase letters, '0', 6 alphanumeric chars (e.g. HDFC0001234)
_IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
# Aadhaar: 12 digits grouped as 4-4-4
_AADHAAR_RE = re.compile(r"\b\d{4}[\s-]\d{4}[\s-]\d{4}\b")
# Indian mobile numbers: optional +91/91, 10 digits starting with 6-9
_PHONE_RE = re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)")
# Bank account numbers: 9 to 18 consecutive digits (excluding 10-digit phones matched first)
_ACCT_RE = re.compile(r"\b\d{9,18}\b")
# Masked or labeled account references like A/C No: XXXX1234 or Acct 123456789012
_LABEled_ACCT_RE = re.compile(
    r"(?i)\b(?:a/c|acct|account)\s*(?:no\.?|number|#|:)?\s*[xX*\d-]{4,20}\b"
)
# Email addresses
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Redact PAN, IFSC, Aadhaar, bank account numbers, phone numbers, and emails from `text`.

    Returns `(redacted_text, redaction_types_applied)`.
    """
    if not text:
        return text, []

    applied: list[str] = []
    out = text

    if _PAN_RE.search(out):
        out = _PAN_RE.sub("[REDACTED_PAN]", out)
        applied.append("pan")

    if _IFSC_RE.search(out):
        out = _IFSC_RE.sub("[REDACTED_IFSC]", out)
        applied.append("ifsc")

    if _AADHAAR_RE.search(out):
        out = _AADHAAR_RE.sub("[REDACTED_AADHAAR]", out)
        applied.append("aadhaar")

    if _EMAIL_RE.search(out):
        out = _EMAIL_RE.sub("[REDACTED_EMAIL]", out)
        applied.append("email")

    if _LABEled_ACCT_RE.search(out):
        out = _LABEled_ACCT_RE.sub("[REDACTED_ACCOUNT]", out)
        applied.append("account")

    # Match 11-18 digit account numbers before 10-digit phone numbers, then remaining 9-18 digit accounts
    _LONG_ACCT_RE = re.compile(r"\b\d{11,18}\b")
    if _LONG_ACCT_RE.search(out):
        out = _LONG_ACCT_RE.sub("[REDACTED_ACCOUNT]", out)
        if "account" not in applied:
            applied.append("account")

    if _PHONE_RE.search(out):
        out = _PHONE_RE.sub("[REDACTED_PHONE]", out)
        applied.append("phone")

    if _ACCT_RE.search(out):
        out = _ACCT_RE.sub("[REDACTED_ACCOUNT]", out)
        if "account" not in applied:
            applied.append("account")

    return out, applied


def scrub_text(text: str) -> str:
    """Return `text` with all detected PII tokens replaced by `[REDACTED_*]` placeholders."""
    redacted, _ = redact_pii(text)
    return redacted


def contains_raw_pii(text: str) -> bool:
    """Return True if `text` contains any unredacted PAN or bank account number."""
    if not text:
        return False
    return bool(
        _PAN_RE.search(text)
        or _IFSC_RE.search(text)
        or _ACCT_RE.search(text)
        or _AADHAAR_RE.search(text)
    )


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            key, nonce + counter.to_bytes(8, "big"), hashlib.sha256
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt_bytes(plaintext: bytes, secret: str) -> str:
    """Encrypt bytes using PBKDF2-HMAC-SHA256 derived key + authenticated CTR stream + HMAC tag."""
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 10_000, dklen=64)
    enc_key, mac_key = derived[:32], derived[32:]
    stream = _keystream(enc_key, nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    tag = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).digest()
    blob = salt + nonce + tag + ciphertext
    return base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt_bytes(token: str, secret: str) -> bytes:
    """Verify HMAC tag and decrypt bytes produced by `encrypt_bytes`."""
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    if len(raw) < 64:
        raise ValueError("Invalid encrypted payload length")
    salt, nonce, tag, ciphertext = raw[:16], raw[16:32], raw[32:64], raw[64:]
    derived = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 10_000, dklen=64)
    enc_key, mac_key = derived[:32], derived[32:]
    expected_tag = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("Encrypted payload authentication failed")
    stream = _keystream(enc_key, nonce, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, stream))


class StoredDocumentMeta(BaseModel):
    doc_id: str
    doc_type: str
    created_at: date
    expires_at: date
    redacted_preview: str


class EncryptedDocumentStore:
    """SQLite-backed encrypted document store with configurable retention policy."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        secret_key: str | None = None,
        retention_days: int = 30,
    ) -> None:
        self.conn = conn
        self.secret_key = (
            secret_key
            or os.environ.get("WEALTH_DOC_ENCRYPTION_KEY")
            or "wealth-advisor-default-local-encryption-key"
        )
        self.retention_days = retention_days
        self._init_table()

    def _init_table(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS encrypted_documents (
                doc_id TEXT PRIMARY KEY,
                doc_type TEXT NOT NULL,
                ciphertext TEXT NOT NULL,
                redacted_preview TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def save(
        self,
        doc_type: str,
        raw_content: bytes | str,
        *,
        doc_id: str | None = None,
        as_of: date | None = None,
        retention_days: int | None = None,
    ) -> StoredDocumentMeta:
        eff_date = as_of or datetime.now(timezone.utc).date()
        days = retention_days if retention_days is not None else self.retention_days
        expires = eff_date + timedelta(days=days)
        did = doc_id or f"DOC_{secrets.token_hex(6).upper()}"

        raw_bytes = raw_content.encode("utf-8") if isinstance(raw_content, str) else raw_content
        text_preview = raw_bytes.decode("utf-8", errors="replace")[:300]
        redacted_preview = scrub_text(text_preview)
        ciphertext = encrypt_bytes(raw_bytes, self.secret_key)

        self.conn.execute(
            """
            INSERT OR REPLACE INTO encrypted_documents
            (doc_id, doc_type, ciphertext, redacted_preview, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (did, doc_type, ciphertext, redacted_preview, eff_date.isoformat(), expires.isoformat()),
        )
        self.conn.commit()
        return StoredDocumentMeta(
            doc_id=did,
            doc_type=doc_type,
            created_at=eff_date,
            expires_at=expires,
            redacted_preview=redacted_preview,
        )

    def load_decrypted(self, doc_id: str) -> bytes:
        row = self.conn.execute(
            "SELECT ciphertext FROM encrypted_documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Document not found: {doc_id}")
        return decrypt_bytes(row[0], self.secret_key)

    def raw_ciphertext(self, doc_id: str) -> str:
        row = self.conn.execute(
            "SELECT ciphertext FROM encrypted_documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Document not found: {doc_id}")
        return str(row[0])

    def purge_expired(self, as_of: date | None = None) -> int:
        eff_date = as_of or datetime.now(timezone.utc).date()
        cur = self.conn.execute(
            "DELETE FROM encrypted_documents WHERE expires_at <= ?",
            (eff_date.isoformat(),),
        )
        self.conn.commit()
        return cur.rowcount
