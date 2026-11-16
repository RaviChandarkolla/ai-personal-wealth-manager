"""M14: Bank statement (CSV/PDF + OCR fallback) and CAS (PDF/JSON) parsers.

Supports:
- Major Indian bank formats: HDFC, ICICI, SBI, and generic CSV/PDF layouts.
- OCR fallback (`OCRProvider` protocol + deterministic `RegexLayoutOCR`) for scanned statements.
- CAS (CAMS/KFintech / NSDL/CDSL) JSON and text/PDF parsing to extract existing
  MF/equity holdings and active SIP schedules.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol

from wealth_advisor.ingestion.pii import redact_pii, scrub_text

BankFormat = Literal["hdfc", "icici", "sbi", "generic"]


@dataclass(frozen=True)
class RawTransaction:
    txn_id: str
    txn_date: date
    description: str
    amount_inr: float  # positive = credit/inflow, negative = debit/outflow
    txn_type: Literal["credit", "debit"]
    closing_balance_inr: float | None = None
    bank_format: BankFormat = "generic"
    reference: str = ""


@dataclass(frozen=True)
class ParsedBankStatement:
    bank_format: BankFormat
    account_masked: str
    period_start: date | None
    period_end: date | None
    opening_balance_inr: float | None
    closing_balance_inr: float | None
    transactions: tuple[RawTransaction, ...] = ()
    used_ocr: bool = False


@dataclass(frozen=True)
class CASHolding:
    instrument_id: str
    name: str
    asset_type: Literal["mutual_fund", "equity", "etf", "bond"]
    category: str
    units: float
    nav_or_price_inr: float
    market_value_inr: float
    cost_value_inr: float = 0.0
    folio_or_demat: str = ""


@dataclass(frozen=True)
class ActiveSIP:
    sip_id: str
    instrument_id: str
    scheme_name: str
    monthly_amount_inr: float
    frequency: Literal["monthly", "quarterly", "weekly"] = "monthly"
    active: bool = True


@dataclass(frozen=True)
class ParsedCASStatement:
    statement_as_of: date
    investor_pan_masked: str
    holdings: tuple[CASHolding, ...] = ()
    active_sips: tuple[ActiveSIP, ...] = ()
    extra_metadata: dict[str, Any] = field(default_factory=dict)


class OCRProvider(Protocol):
    """Protocol for OCR extraction on scanned PDF/image bank statements."""

    def extract_text(self, raw_bytes: bytes) -> str: ...


class RegexLayoutOCR:
    """Deterministic OCR layout fallback for scanned PDF/image bytes."""

    _PRINTABLE_CHUNK = re.compile(rb"[\x20-\x7e\r\n\t]{6,}")

    def extract_text(self, raw_bytes: bytes) -> str:
        try:
            decoded = raw_bytes.decode("utf-8", errors="ignore")
            if self._looks_like_statement(decoded):
                return decoded
        except Exception:
            pass
        chunks = [m.group(0).decode("ascii", errors="ignore") for m in self._PRINTABLE_CHUNK.finditer(raw_bytes)]
        return "\n".join(chunks)

    @staticmethod
    def _looks_like_statement(text: str) -> bool:
        return bool(re.search(r"\d{2}[/-]\d{2}[/-]\d{2,4}", text))


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d/%m/%y",
    "%d-%m-%y",
    "%d %b %Y",
    "%d-%b-%Y",
    "%d %b %y",
)


def _parse_date(raw: str) -> date:
    cleaned = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {raw!r}")


def _parse_float(raw: str | float | int | None) -> float:
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = re.sub(r"[,\s₹INR]", "", str(raw).strip(), flags=re.IGNORECASE)
    if not cleaned or cleaned in ("-", "--", "NA", "N/A"):
        return 0.0
    # Handle Cr/Dr suffix
    if cleaned.upper().endswith("CR"):
        return float(cleaned[:-2].strip())
    if cleaned.upper().endswith("DR"):
        return -abs(float(cleaned[:-2].strip()))
    return float(cleaned)


def detect_bank_format(headers_or_text: str | Sequence[str]) -> BankFormat:
    joined = headers_or_text if isinstance(headers_or_text, str) else " ".join(headers_or_text)
    lower = joined.lower()
    if "narration" in lower and ("chq./ref.no." in lower or "withdrawal amt." in lower or "hdfc" in lower):
        return "hdfc"
    if "transaction remarks" in lower or ("withdrawal amount (inr)" in lower) or "icici" in lower:
        return "icici"
    if ("debit" in lower and "credit" in lower and "txn date" in lower) or "sbi" in lower:
        return "sbi"
    return "generic"


def parse_bank_csv(
    csv_content: str | bytes,
    *,
    bank_format: BankFormat | None = None,
) -> ParsedBankStatement:
    """Parse a CSV bank statement (HDFC, ICICI, SBI, or generic)."""
    text = csv_content.decode("utf-8", errors="replace") if isinstance(csv_content, bytes) else csv_content
    _, applied_pii = redact_pii(text)
    fmt = bank_format or detect_bank_format(text[:1000])

    reader = csv.reader(io.StringIO(text.strip()))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return ParsedBankStatement(
            bank_format=fmt,
            account_masked="[REDACTED_ACCT]",
            period_start=None,
            period_end=None,
            opening_balance_inr=None,
            closing_balance_inr=None,
            transactions=(),
        )

    # Locate header row
    header_idx = 0
    for idx, row in enumerate(rows):
        joined = " ".join(c.lower() for c in row)
        if any(k in joined for k in ("date", "narration", "description", "particulars", "remarks")):
            header_idx = idx
            break

    headers = [c.strip().lower() for c in rows[header_idx]]
    if bank_format is None:
        fmt = detect_bank_format(" ".join(headers))

    date_col = _find_col(headers, ("txn date", "transaction date", "value date", "date"))
    desc_col = _find_col(headers, ("narration", "transaction remarks", "description", "particulars", "remarks"))
    debit_col = _find_col(headers, ("withdrawal amt.", "withdrawal amount (inr)", "withdrawal", "debit", "dr"))
    credit_col = _find_col(headers, ("deposit amt.", "deposit amount (inr)", "deposit", "credit", "cr"))
    amount_col = _find_col(headers, ("amount", "txn amount", "transaction amount"))
    type_col = _find_col(headers, ("type", "dr/cr", "cr/dr", "txn type"))
    bal_col = _find_col(headers, ("closing balance", "balance (inr)", "balance", "running balance"))
    ref_col = _find_col(headers, ("chq./ref.no.", "cheque number", "ref no./cheque no.", "reference", "ref"))

    txns: list[RawTransaction] = []
    for row_idx, row in enumerate(rows[header_idx + 1 :], start=1):
        if date_col is None or date_col >= len(row):
            continue
        raw_date = row[date_col].strip()
        if not raw_date:
            continue
        try:
            txn_date = _parse_date(raw_date)
        except ValueError:
            continue

        raw_desc = row[desc_col].strip() if desc_col is not None and desc_col < len(row) else ""
        desc = scrub_text(raw_desc)

        debit_val = _parse_float(row[debit_col]) if debit_col is not None and debit_col < len(row) else 0.0
        credit_val = _parse_float(row[credit_col]) if credit_col is not None and credit_col < len(row) else 0.0

        if credit_val > 0 and debit_val == 0:
            amount = credit_val
            txn_type: Literal["credit", "debit"] = "credit"
        elif debit_val > 0 and credit_val == 0:
            amount = -abs(debit_val)
            txn_type = "debit"
        elif amount_col is not None and amount_col < len(row):
            raw_amt = _parse_float(row[amount_col])
            raw_type = row[type_col].strip().lower() if type_col is not None and type_col < len(row) else ""
            if raw_type in ("dr", "debit", "withdrawal") or raw_amt < 0:
                amount = -abs(raw_amt)
                txn_type = "debit"
            else:
                amount = abs(raw_amt)
                txn_type = "credit"
        else:
            continue

        closing_bal: float | None = None
        if bal_col is not None and bal_col < len(row) and row[bal_col].strip():
            closing_bal = _parse_float(row[bal_col])

        ref = row[ref_col].strip() if ref_col is not None and ref_col < len(row) else ""

        txns.append(
            RawTransaction(
                txn_id=f"txn_{row_idx:04d}",
                txn_date=txn_date,
                description=desc,
                amount_inr=round(amount, 2),
                txn_type=txn_type,
                closing_balance_inr=closing_bal,
                bank_format=fmt,
                reference=scrub_text(ref) if ref else "",
            )
        )

    return _build_statement_summary(fmt, txns, used_ocr=False, had_pii=len(applied_pii) > 0)


def _find_col(headers: list[str], candidates: tuple[str, ...]) -> int | None:
    for cand in candidates:
        for i, h in enumerate(headers):
            if cand == h or cand in h:
                return i
    return None


_LINE_TXN_RE = re.compile(
    r"(?P<date>\d{2}[/-]\d{2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<amount>[+-]?\d[\d,]*(?:\.\d{1,2})?)\s*"
    r"(?P<drcr>CR|DR|Credit|Debit)?"
    r"(?:\s+(?P<bal>\d[\d,]*(?:\.\d{1,2})?))?\s*$",
    re.IGNORECASE,
)


def parse_bank_pdf(
    pdf_or_text: bytes | str,
    *,
    bank_format: BankFormat | None = None,
    ocr_provider: OCRProvider | None = None,
) -> ParsedBankStatement:
    """Parse a text-based or scanned PDF bank statement (with OCR fallback)."""
    used_ocr = False
    if isinstance(pdf_or_text, bytes):
        text = _extract_text_from_pdf_bytes(pdf_or_text)
        if not _LINE_TXN_RE.search(text):
            provider = ocr_provider or RegexLayoutOCR()
            text = provider.extract_text(pdf_or_text)
            used_ocr = True
    else:
        text = pdf_or_text

    fmt = bank_format or detect_bank_format(text[:1000])
    txns: list[RawTransaction] = []

    for idx, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        m = _LINE_TXN_RE.match(line)
        if not m:
            continue
        try:
            txn_date = _parse_date(m.group("date"))
        except ValueError:
            continue
        raw_desc = m.group("desc").strip()
        amt_val = _parse_float(m.group("amount"))
        drcr = (m.group("drcr") or "").upper()
        bal_str = m.group("bal")
        closing_bal = _parse_float(bal_str) if bal_str else None

        if drcr in ("DR", "DEBIT") or amt_val < 0:
            amount = -abs(amt_val)
            txn_type: Literal["credit", "debit"] = "debit"
        elif drcr in ("CR", "CREDIT") or amt_val > 0:
            # Infer debit from common keywords if no explicit CR/DR marker is present
            if not drcr and any(
                kw in raw_desc.upper()
                for kw in ("EMI", "SIP", "RENT", "UPI/", "POS ", "ATM ", "BILL", "DEBIT", "ACH D", "NACH-DR")
            ):
                amount = -abs(amt_val)
                txn_type = "debit"
            else:
                amount = abs(amt_val)
                txn_type = "credit"
        else:
            continue

        txns.append(
            RawTransaction(
                txn_id=f"pdf_txn_{idx:04d}",
                txn_date=txn_date,
                description=scrub_text(raw_desc),
                amount_inr=round(amount, 2),
                txn_type=txn_type,
                closing_balance_inr=closing_bal,
                bank_format=fmt,
            )
        )

    return _build_statement_summary(fmt, txns, used_ocr=used_ocr, had_pii=True)


def _extract_text_from_pdf_bytes(raw_bytes: bytes) -> str:
    """Extract embedded text streams from PDF bytes without external system deps."""
    decoded = raw_bytes.decode("latin-1", errors="ignore")
    # Extract parenthesized PDF text literals `( ... ) Tj` if present, else direct UTF-8
    literals = re.findall(r"\(([^\(\)]{3,})\)", decoded)
    if literals and any(re.search(r"\d{2}[/-]\d{2}[/-]\d{2,4}", lit) for lit in literals):
        return "\n".join(literals)
    return raw_bytes.decode("utf-8", errors="ignore")


def _build_statement_summary(
    fmt: BankFormat,
    txns: list[RawTransaction],
    *,
    used_ocr: bool,
    had_pii: bool,
) -> ParsedBankStatement:
    if not txns:
        return ParsedBankStatement(
            bank_format=fmt,
            account_masked="[REDACTED_ACCT]",
            period_start=None,
            period_end=None,
            opening_balance_inr=None,
            closing_balance_inr=None,
            transactions=(),
            used_ocr=used_ocr,
        )
    dates = [t.txn_date for t in txns]
    bals = [t.closing_balance_inr for t in txns if t.closing_balance_inr is not None]
    opening = (bals[0] - txns[0].amount_inr) if bals else None
    closing = bals[-1] if bals else None
    return ParsedBankStatement(
        bank_format=fmt,
        account_masked="[REDACTED_ACCT]",
        period_start=min(dates),
        period_end=max(dates),
        opening_balance_inr=round(opening, 2) if opening is not None else None,
        closing_balance_inr=round(closing, 2) if closing is not None else None,
        transactions=tuple(txns),
        used_ocr=used_ocr,
    )


# ── CAS (Consolidated Account Statement) Parser ───────────────────────────

_CAS_HOLDING_LINE_RE = re.compile(
    r"^(?P<id>[A-Z0-9_-]{3,20})\s*\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<category>[^|]+?)\s*\|\s*"
    r"Units:\s*(?P<units>[\d,.]+)\s*\|\s*NAV:\s*(?P<nav>[\d,.]+)\s*\|\s*Value:\s*(?P<val>[\d,.]+)",
    re.IGNORECASE,
)

_CAS_SIP_LINE_RE = re.compile(
    r"^SIP(?:\s*\|\s*(?P<id>[A-Z0-9_-]{3,20})\s*\|\s*|\s*:\s*)(?P<name>[^|]+?)\s*\|\s*Amount:\s*(?P<amt>[\d,.]+)",
    re.IGNORECASE,
)


def parse_cas_statement(cas_input: str | bytes | dict[str, Any]) -> ParsedCASStatement:
    """Parse a CAS statement from JSON (dict/string) or text/PDF representation."""
    if isinstance(cas_input, dict):
        return _parse_cas_dict(cas_input)

    text = cas_input.decode("utf-8", errors="ignore") if isinstance(cas_input, bytes) else cas_input
    stripped = text.strip()
    if stripped.startswith("{"):
        data = json.loads(stripped)
        return _parse_cas_dict(data)

    # Text / PDF layout CAS
    holdings: list[CASHolding] = []
    sips: list[ActiveSIP] = []
    as_of = date(2026, 3, 31)

    for idx, raw_line in enumerate(stripped.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        m_date = re.search(r"Statement\s+as\s+of:\s*(\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4})", line, re.I)
        if m_date:
            try:
                as_of = _parse_date(m_date.group(1))
            except ValueError:
                pass
            continue

        m_hold = _CAS_HOLDING_LINE_RE.match(line)
        if m_hold:
            inst_id = m_hold.group("id").strip()
            name = m_hold.group("name").strip()
            cat = m_hold.group("category").strip()
            units = _parse_float(m_hold.group("units"))
            nav = _parse_float(m_hold.group("nav"))
            val = _parse_float(m_hold.group("val"))
            asset_type: Literal["mutual_fund", "equity", "etf", "bond"] = (
                "equity" if "equity" in cat.lower() and "fund" not in name.lower() else "mutual_fund"
            )
            holdings.append(
                CASHolding(
                    instrument_id=inst_id,
                    name=name,
                    asset_type=asset_type,
                    category=cat,
                    units=units,
                    nav_or_price_inr=nav,
                    market_value_inr=val,
                )
            )
            continue

        m_sip = _CAS_SIP_LINE_RE.match(line)
        if m_sip:
            sip_inst_id = (m_sip.group("id") or "").strip()
            sip_name = m_sip.group("name").strip()
            if not sip_inst_id:
                sip_inst_id = next((h.instrument_id for h in holdings if h.name.lower() == sip_name.lower()), "UNKNOWN")
            sips.append(
                ActiveSIP(
                    sip_id=f"cas_sip_{idx:03d}",
                    instrument_id=sip_inst_id,
                    scheme_name=sip_name,
                    monthly_amount_inr=_parse_float(m_sip.group("amt")),
                )
            )

    return ParsedCASStatement(
        statement_as_of=as_of,
        investor_pan_masked="[REDACTED_PAN]",
        holdings=tuple(holdings),
        active_sips=tuple(sips),
    )


def _parse_cas_dict(data: dict[str, Any]) -> ParsedCASStatement:
    raw_as_of = data.get("statement_as_of") or data.get("as_of") or "2026-03-31"
    as_of = _parse_date(str(raw_as_of)) if isinstance(raw_as_of, str) else date(2026, 3, 31)

    holdings: list[CASHolding] = []
    for h in data.get("holdings", []):
        units = _parse_float(h.get("units", 0.0))
        nav = _parse_float(h.get("nav_or_price_inr", h.get("nav", 0.0)))
        mv = _parse_float(h.get("market_value_inr", h.get("market_value", units * nav)))
        holdings.append(
            CASHolding(
                instrument_id=str(h.get("instrument_id", h.get("isin", "UNKNOWN"))),
                name=str(h.get("name", h.get("scheme_name", "Unknown Scheme"))),
                asset_type=h.get("asset_type", "mutual_fund"),
                category=str(h.get("category", "Flexi Cap")),
                units=units,
                nav_or_price_inr=nav,
                market_value_inr=mv,
                cost_value_inr=_parse_float(h.get("cost_value_inr", h.get("cost_value", 0.0))),
                folio_or_demat=scrub_text(str(h.get("folio_or_demat", ""))),
            )
        )

    sips: list[ActiveSIP] = []
    for idx, s in enumerate(data.get("active_sips", data.get("sips", [])), start=1):
        sips.append(
            ActiveSIP(
                sip_id=str(s.get("sip_id", f"cas_sip_{idx:03d}")),
                instrument_id=str(s.get("instrument_id", "UNKNOWN")),
                scheme_name=str(s.get("scheme_name", s.get("name", "Unknown SIP"))),
                monthly_amount_inr=_parse_float(s.get("monthly_amount_inr", s.get("amount", 0.0))),
                frequency=s.get("frequency", "monthly"),
                active=bool(s.get("active", True)),
            )
        )

    return ParsedCASStatement(
        statement_as_of=as_of,
        investor_pan_masked="[REDACTED_PAN]",
        holdings=tuple(holdings),
        active_sips=tuple(sips),
    )
