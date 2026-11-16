"""M14 tests: Document ingestion (Bank CSV/PDF + OCR, CAS JSON/PDF, PII redaction, profile update)."""

from __future__ import annotations

from datetime import date, timedelta
import json

from wealth_advisor.agents.profiling import ProfilingAgent
from wealth_advisor.audit.log import AuditLog, ProfileStore
from wealth_advisor.db import connect_sqlite, ensure_schema
from wealth_advisor.ingestion import (
    ConfirmationQueue,
    EncryptedDocumentStore,
    RegexLayoutOCR,
    TransactionCategorizer,
    contains_raw_pii,
    derive_financial_summary,
    detect_bank_format,
    parse_bank_csv,
    parse_bank_pdf,
    parse_cas_statement,
    redact_pii,
    scrub_text,
    update_profile_from_ingestion,
)
from wealth_advisor.models.core import Goal
from wealth_advisor.models.enums import IncomeStability, RiskPreference


# ── PII Redaction & Encrypted Storage ─────────────────────────────────────


def test_pii_redaction_strips_pan_account_phone_email_aadhaar():
    raw = (
        "Holder Rajesh Kumar, PAN ABCDE1234F, Account 00112233445566, "
        "IFSC HDFC0001234, Mobile +91 9876543210, Email rajesh.kumar@example.com, "
        "Aadhaar 4567 8901 2345"
    )
    assert contains_raw_pii(raw) is True
    scrubbed, kinds = redact_pii(raw)
    assert "ABCDE1234F" not in scrubbed
    assert "00112233445566" not in scrubbed
    assert "HDFC0001234" not in scrubbed
    assert "9876543210" not in scrubbed
    assert "rajesh.kumar@example.com" not in scrubbed
    assert "pan" in kinds
    assert "account" in kinds
    assert "aadhaar" in kinds
    assert "ifsc" in kinds
    assert "phone" in kinds
    assert "email" in kinds
    assert contains_raw_pii(scrubbed) is False


def test_encrypted_document_store_roundtrip_and_retention_purge():
    conn = connect_sqlite(":memory:")
    ensure_schema(conn)
    store = EncryptedDocumentStore(conn, secret_key="unit-test-secret-key", retention_days=30)

    raw_doc = "PAN: ABCDE1234F, Account: 123456789012, Salary Credit 150000"
    meta = store.save("bank_csv", raw_doc, doc_id="doc_001", as_of=date(2026, 4, 1), retention_days=15)

    assert meta.doc_id == "doc_001"
    assert meta.expires_at == date(2026, 4, 16)

    # Verify ciphertext at rest does not expose raw PII
    ciphertext = store.raw_ciphertext("doc_001")
    assert "ABCDE1234F" not in ciphertext
    assert "123456789012" not in ciphertext
    assert "Salary Credit" not in ciphertext

    # Round-trip decrypt works
    decrypted = store.load_decrypted("doc_001").decode("utf-8")
    assert decrypted == raw_doc

    # Purge before expiry deletes 0; after expiry deletes 1
    assert store.purge_expired(as_of=date(2026, 4, 10)) == 0
    assert store.purge_expired(as_of=date(2026, 4, 16)) == 1


# ── Bank CSV / PDF + OCR & CAS Parsers ────────────────────────────────────


def test_parse_bank_csv_hdfc_icici_sbi_formats():
    hdfc_csv = """Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance
01/01/2026,NEFT-SALARY-ACME CORP-PAN ABCDE1234F,REF101,01/01/2026,0.00,150000.00,320000.00
05/01/2026,ACH D-HDFC HOME LOAN EMI,REF102,05/01/2026,35000.00,0.00,285000.00
10/01/2026,NACH-MF-CAMS SIP PURCHASE,REF103,10/01/2026,25000.00,0.00,260000.00
"""
    assert detect_bank_format(["Date", "Narration", "Chq./Ref.No.", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]) == "hdfc"
    stmt = parse_bank_csv(hdfc_csv)
    assert stmt.bank_format == "hdfc"
    assert len(stmt.transactions) == 3
    assert stmt.closing_balance_inr == 260000.0
    # PII inside narration is scrubbed during parsing
    assert "ABCDE1234F" not in stmt.transactions[0].description
    assert "[REDACTED_PAN]" in stmt.transactions[0].description

    icici_csv = """Transaction Date,Transaction Remarks,Withdrawal Amount (INR ),Deposit Amount (INR ),Balance (INR )
01-02-2026,SALARY CREDIT FEB,0,120000,240000
07-02-2026,SWIGGY ORDER,850,0,239150
"""
    stmt_icici = parse_bank_csv(icici_csv)
    assert stmt_icici.bank_format == "icici"
    assert len(stmt_icici.transactions) == 2

    sbi_csv = """Txn Date,Description,Ref No./Cheque No.,Debit,Credit,Balance
2026-03-01,SALARY CR MAR,SBI001,,110000,310000
2026-03-05,BESCOM ELECTRICITY BILL,SBI002,2400,,307600
"""
    stmt_sbi = parse_bank_csv(sbi_csv)
    assert stmt_sbi.bank_format == "sbi"
    assert len(stmt_sbi.transactions) == 2


def test_parse_bank_pdf_with_ocr_fallback():
    pdf_text = """
    HDFC BANK STATEMENT - A/C 00112233445566 - PAN ABCDE1234F
    01/01/2026 NEFT SALARY CREDIT ACME LTD 150,000.00 Cr 450,000.00
    05/01/2026 HOUSE RENT TRANSFER 30,000.00 Dr 420,000.00
    10/01/2026 ZERODHA COIN SIP 20,000.00 Dr 400,000.00
    """
    stmt = parse_bank_pdf(pdf_text, ocr_provider=RegexLayoutOCR())
    assert len(stmt.transactions) == 3
    assert stmt.transactions[0].txn_type == "credit"
    assert stmt.transactions[0].amount_inr == 150000.0
    assert stmt.closing_balance_inr == 400000.0


def test_parse_cas_statement_json_and_text():
    cas_json = json.dumps(
        {
            "as_of": "2026-03-31",
            "pan": "ABCDE1234F",
            "holdings": [
                {
                    "folio": "998877665544",
                    "isin": "INF179K01UT0",
                    "scheme_name": "HDFC Flexi Cap Direct Growth",
                    "category": "Flexi Cap",
                    "units": 200.0,
                    "nav": 1500.0,
                    "market_value_inr": 300000.0,
                    "cost_value_inr": 220000.0,
                },
                {
                    "folio": "998877665545",
                    "isin": "INF200K01VA8",
                    "scheme_name": "SBI Liquid Fund Direct Growth",
                    "category": "Liquid",
                    "units": 50.0,
                    "nav": 4000.0,
                    "market_value_inr": 200000.0,
                    "cost_value_inr": 190000.0,
                },
            ],
            "active_sips": [
                {
                    "sip_id": "sip_1",
                    "instrument_id": "INF179K01UT0",
                    "scheme_name": "HDFC Flexi Cap Direct Growth",
                    "monthly_amount_inr": 25000.0,
                    "frequency": "monthly",
                    "active": True,
                }
            ],
        }
    )
    cas = parse_cas_statement(cas_json)
    assert len(cas.holdings) == 2
    assert len(cas.active_sips) == 1
    assert cas.active_sips[0].monthly_amount_inr == 25000.0

    cas_text = """
    CONSOLIDATED ACCOUNT STATEMENT
    INF179K01UT0 | HDFC Flexi Cap Direct Growth | Category: Flexi Cap | Units: 100.0 | NAV: 1500.0 | Value: 150,000.00
    SIP: HDFC Flexi Cap Direct Growth | Amount: 15,000.00 | Frequency: Monthly
    """
    cas2 = parse_cas_statement(cas_text)
    assert len(cas2.holdings) == 1
    assert cas2.holdings[0].market_value_inr == 150000.0
    assert len(cas2.active_sips) == 1
    assert cas2.active_sips[0].monthly_amount_inr == 15000.0


# ── Transaction Categorizer (Rules -> LLM fallback -> Confirmation Queue) ─


class SpyCategorizerLLM:
    def __init__(self, category: str = "discretionary", confidence: float = 0.55) -> None:
        self.prompts_seen: list[str] = []
        self._category = category
        self._confidence = confidence

    def classify_transaction(self, redacted_description: str, txn_type: str):
        self.prompts_seen.append(redacted_description)
        return self._category, self._confidence  # type: ignore[return-value]


def test_categorizer_rules_first_then_redacted_llm_fallback_and_confirmation_queue():
    csv_text = """Date,Narration,Withdrawal Amt.,Deposit Amt.,Closing Balance
01/01/2026,NEFT SALARY CREDIT ACME CORP,0,150000,300000
05/01/2026,CAMS SIP PURCHASE INF179K01UT0,25000,0,275000
08/01/2026,IMPS TO SHARMA ENTERPRISES PAN ABCDE1234F A/C 112233445566,12000,0,263000
"""
    stmt = parse_bank_csv(csv_text)
    spy_llm = SpyCategorizerLLM(category="discretionary", confidence=0.55)
    queue = ConfirmationQueue(":memory:")
    categorizer = TransactionCategorizer(
        llm_fallback=spy_llm,
        confirmation_queue=queue,
        confirmation_threshold=0.70,
    )

    results = categorizer.categorize_many(stmt.transactions, profile_id="user_m14")
    assert len(results) == 3

    # 1. Salary matched by rule
    assert results[0].category == "salary"
    assert results[0].source == "rule"
    assert results[0].needs_confirmation is False

    # 2. SIP matched by rule
    assert results[1].category == "sip"
    assert results[1].source == "rule"
    assert results[1].needs_confirmation is False

    # 3. Unmatched description fell back to LLM, with PII redacted before LLM saw it
    assert results[2].source == "llm"
    assert results[2].needs_confirmation is True
    assert len(spy_llm.prompts_seen) == 1
    assert "ABCDE1234F" not in spy_llm.prompts_seen[0]
    assert "112233445566" not in spy_llm.prompts_seen[0]

    # Queued in confirmation queue; user can confirm
    pending = queue.list_pending(profile_id="user_m14")
    assert len(pending) == 1
    confirmed = queue.confirm(pending[0].item_id, "rent")
    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_category == "rent"
    assert queue.list_pending(profile_id="user_m14") == []


# ── M14 Exit Criterion: 6-Month Synthetic Statement + CAS Updates Profile v1 -> v2 ─


def test_m14_exit_criterion_6_month_synthetic_statement_updates_profile_v1_to_v2_and_changes_risk_score():
    """Exit criterion:
    Uploading a 6-month synthetic bank statement + CAS updates the user profile from `v1`
    (assumed defaults) to `v2` (statement-derived) and changes `risk_score` / `allocation_band`
    accordingly; zero raw PAN or account numbers appear in `AuditLog` or LLM prompts.
    """
    conn = connect_sqlite(":memory:")
    ensure_schema(conn)
    audit_log = AuditLog(conn)
    profile_store = ProfileStore(conn)

    # Step 1: Create v1 profile using assumed defaults via ProfilingAgent
    goal = Goal(
        amount=1_500_000.0,
        horizon_years=7.0,
        risk_preference=RiskPreference.MODERATE,
        purpose="wealth creation",
    )
    v1_profile, _ = ProfilingAgent.build_profile(
        goal,
        profile_id="investor_m14",
        version=1,
    )
    profile_store.save(v1_profile)
    audit_log.append(
        run_id="run_m14_v1",
        agent="profiling",
        step="profile",
        payload={"profile_id": v1_profile.profile_id, "version": v1_profile.version},
    )
    assert v1_profile.version == 1
    assert len(v1_profile.inputs["assumed_defaults"]) > 0

    # Step 2: Build a 6-month synthetic bank statement + CAS with high savings rate & high emergency buffer
    # (and embedded raw PAN + account numbers to verify zero PII leakage)
    rows = ["Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance"]
    running_balance = 600_000.0
    for month in range(1, 7):
        running_balance += 200_000.0
        rows.append(
            f"01/0{month}/2026,NEFT SALARY CREDIT ACME INDIA PAN ABCDE1234F A/C 998877665544,SAL0{month},01/0{month}/2026,0.00,200000.00,{running_balance:.2f}"
        )
        running_balance -= 30_000.0
        rows.append(
            f"05/0{month}/2026,HOUSE RENT PAYMENT LANDLORD PAN ZYXWV9876K,RNT0{month},05/0{month}/2026,30000.00,0.00,{running_balance:.2f}"
        )
        running_balance -= 10_000.0
        rows.append(
            f"10/0{month}/2026,BESCOM ELECTRICITY AND BROADBAND BILL,UTL0{month},10/0{month}/2026,10000.00,0.00,{running_balance:.2f}"
        )
        running_balance -= 50_000.0
        rows.append(
            f"15/0{month}/2026,NACH-MF-CAMS SIP PURCHASE FOLIO 112233445566,SIP0{month},15/0{month}/2026,50000.00,0.00,{running_balance:.2f}"
        )

    six_month_csv = "\n".join(rows)
    cas_payload = json.dumps(
        {
            "as_of": "2026-06-30",
            "pan": "ABCDE1234F",
            "holdings": [
                {
                    "folio": "112233445566",
                    "isin": "INF179K01UT0",
                    "scheme_name": "HDFC Flexi Cap Direct Growth",
                    "category": "Flexi Cap",
                    "units": 250.0,
                    "nav": 1600.0,
                    "market_value_inr": 400_000.0,
                },
                {
                    "folio": "112233445567",
                    "isin": "INF200K01VA8",
                    "scheme_name": "SBI Liquid Fund Direct Growth",
                    "category": "Liquid",
                    "units": 100.0,
                    "nav": 3500.0,
                    "market_value_inr": 350_000.0,
                },
                {
                    "folio": "112233445568",
                    "isin": "INF846K01EW2",
                    "scheme_name": "Axis Midcap Direct Growth",
                    "category": "Mid Cap",
                    "units": 300.0,
                    "nav": 1000.0,
                    "market_value_inr": 300_000.0,
                },
            ],
            "active_sips": [
                {
                    "sip_id": "sip_hdfc",
                    "instrument_id": "INF179K01UT0",
                    "scheme_name": "HDFC Flexi Cap Direct Growth",
                    "monthly_amount_inr": 50_000.0,
                    "frequency": "monthly",
                    "active": True,
                }
            ],
        }
    )

    spy_llm = SpyCategorizerLLM()
    bank_stmt = parse_bank_csv(six_month_csv)
    cas_stmt = parse_cas_statement(cas_payload)
    categorizer = TransactionCategorizer(llm_fallback=spy_llm)
    categorized = categorizer.categorize_many(bank_stmt.transactions, profile_id="investor_m14")

    summary = derive_financial_summary(bank_stmt, categorized, cas_stmt)
    assert summary.months_observed == 6
    assert summary.monthly_income_inr == 200_000.0
    assert summary.monthly_expenses_inr == 40_000.0
    assert summary.savings_rate == 0.80  # (200k - 40k) / 200k
    assert summary.income_stability == IncomeStability.STABLE
    assert summary.existing_sips_monthly_inr == 50_000.0
    assert summary.emergency_months > 12.0

    v2_profile, _ = update_profile_from_ingestion(
        summary=summary,
        previous_profile=v1_profile,
        goal=goal,
        profile_id="investor_m14",
        profile_store=profile_store,
        audit_log=audit_log,
        run_id="run_m14_v2",
    )

    assert v2_profile.version == 2
    assert v2_profile.inputs["assumed_defaults"] == []
    # High savings rate (80%), stable income, >12m emergency fund, diversified CAS -> higher risk score than v1
    assert v2_profile.risk_score != v1_profile.risk_score
    assert v2_profile.risk_score > v1_profile.risk_score

    # Verify ProfileStore holds both v1 and v2
    assert profile_store.get("investor_m14", 1) is not None
    assert profile_store.get("investor_m14", 2) is not None
    assert profile_store.latest("investor_m14").version == 2  # type: ignore[union-attr]

    # Verify ZERO raw PAN or account numbers in AuditLog or LLM prompts
    for prompt in spy_llm.prompts_seen:
        assert "ABCDE1234F" not in prompt
        assert "ZYXWV9876K" not in prompt
        assert "998877665544" not in prompt
        assert "112233445566" not in prompt

    for run_id in ("run_m14_v1", "run_m14_v2"):
        for entry in audit_log.entries(run_id):
            serialized = json.dumps(entry.payload)
            assert "ABCDE1234F" not in serialized
            assert "ZYXWV9876K" not in serialized
            assert "998877665544" not in serialized
            assert "112233445566" not in serialized
