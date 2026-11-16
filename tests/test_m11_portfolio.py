from datetime import date

import pytest
from fastapi.testclient import TestClient
from tests.conftest import AS_OF
from tests.fakes import ScriptedLLM

from wealth_advisor.api.main import app, get_db_conn
from wealth_advisor.audit.log import AuditLog, ProfileStore
from wealth_advisor.data.store import MetadataStore
from wealth_advisor.db import open_db
from wealth_advisor.models import Action, Bucket, Goal, RiskPreference
from wealth_advisor.pipeline import advise
from wealth_advisor.portfolio import (
    PortfolioStore,
    analyze_portfolio_drift,
    build_sell_warnings,
    parse_portfolio_csv,
    update_profile_from_portfolio,
    valuate_portfolio,
)


@pytest.fixture
def conn(demo_conn):
    dst = open_db(":memory:")
    demo_conn.backup(dst)
    return dst


def test_parse_and_valuate_portfolio_by_code_isin_and_name(conn):
    store = MetadataStore(conn)
    nav_eq = store.latest_nav_as_of("DEMO040", AS_OF)[1]
    nav_debt = store.latest_nav_as_of("DEMO010", AS_OF)[1]
    nav_gold = store.latest_nav_as_of("DEMO080", AS_OF)[1]

    # Lookup by code (DEMO040), ISIN (XSDEMO01000 -> DEMO010), and scheme name (Orchid Gold ETF - Direct Plan -> DEMO080)
    csv_text = "\n".join(
        [
            "scheme,units,avg_cost,as_of",
            f"DEMO040,{60_000 / nav_eq:.6f},10.00,2025-01-15",
            f"XSDEMO01000,{30_000 / nav_debt:.6f},10.00,2026-06-01",
            f"Orchid Gold ETF - Direct Plan,{10_000 / nav_gold:.6f},10.00,2024-05-10",
        ]
    )

    parsed = parse_portfolio_csv(csv_text, store, max_as_of=AS_OF)
    assert parsed.valid
    assert not parsed.errors
    assert len(parsed.holdings) == 3
    assert [r.code for r in parsed.holdings] == ["DEMO040", "DEMO010", "DEMO080"]

    val = valuate_portfolio(parsed.holdings, store, as_of=AS_OF)
    assert val.total_value == pytest.approx(100_000.0, abs=1.0)
    assert val.bucket_weights[Bucket.EQUITY] == pytest.approx(60.0, abs=0.1)
    assert val.bucket_weights[Bucket.DEBT] == pytest.approx(30.0, abs=0.1)
    assert val.bucket_weights[Bucket.GOLD] == pytest.approx(10.0, abs=0.1)
    assert val.bucket_weights[Bucket.ALTERNATIVES] == 0.0
    # HHI = 0.6^2 + 0.3^2 + 0.1^2 = 0.36 + 0.09 + 0.01 = 0.46
    assert val.hhi == pytest.approx(0.46, abs=1e-3)


def test_csv_validator_reports_row_level_errors(conn):
    store = MetadataStore(conn)
    bad_csv = "\n".join(
        [
            "scheme,units,avg_cost,as_of",
            ",100,10.0,2025-01-01",  # Row 2: missing scheme
            "UNKNOWN_SCHEME_999,100,10.0,2025-01-01",  # Row 3: unknown scheme
            "DEMO040,-5,10.0,2025-01-01",  # Row 4: negative units
            "DEMO040,100,0,2025-01-01",  # Row 5: non-positive avg_cost
            "DEMO040,100,10.0,not-a-date",  # Row 6: invalid ISO date
            "DEMO040,100,10.0,2027-01-01",  # Row 7: future date relative to AS_OF
            "DEMO040,100,10.0,2025-06-01",  # Row 8: valid row
        ]
    )

    parsed = parse_portfolio_csv(bad_csv, store, max_as_of=AS_OF)
    assert not parsed.valid
    assert len(parsed.holdings) == 1
    assert parsed.holdings[0].row == 8
    assert len(parsed.errors) == 6
    error_rows = [(e.row, e.field) for e in parsed.errors]
    assert error_rows == [
        (2, "scheme"),
        (3, "scheme"),
        (4, "units"),
        (5, "avg_cost"),
        (6, "as_of"),
        (7, "as_of"),
    ]


def test_csv_validator_missing_headers(conn):
    store = MetadataStore(conn)
    parsed = parse_portfolio_csv("scheme,units\nDEMO040,100\n", store, max_as_of=AS_OF)
    assert len(parsed.holdings) == 0
    assert len(parsed.errors) == 1
    assert parsed.errors[0].field == "header"
    assert "avg_cost" in parsed.errors[0].message


def test_drift_5_25_rule_triggers_and_suppresses_correctly(conn):
    store = MetadataStore(conn)
    nav_eq = store.latest_nav_as_of("DEMO040", AS_OF)[1]
    nav_debt = store.latest_nav_as_of("DEMO010", AS_OF)[1]
    nav_gold = store.latest_nav_as_of("DEMO080", AS_OF)[1]

    target = {
        Bucket.EQUITY: 50.0,
        Bucket.DEBT: 40.0,
        Bucket.GOLD: 10.0,
        Bucket.ALTERNATIVES: 0.0,
    }

    # Case 1: Within 5/25 band (Equity 53% vs 50% [+3pp, +6% rel], Debt 38% vs 40% [-2pp, -5% rel], Gold 9% vs 10% [-1pp, -10% rel])
    csv_within = "\n".join(
        [
            "code,units,avg_cost,as_of",
            f"DEMO040,{53_000 / nav_eq:.6f},10.0,2024-01-10",
            f"DEMO010,{38_000 / nav_debt:.6f},10.0,2024-01-10",
            f"DEMO080,{9_000 / nav_gold:.6f},10.0,2024-01-10",
        ]
    )
    parsed_within = parse_portfolio_csv(csv_within, store, max_as_of=AS_OF)
    val_within = valuate_portfolio(parsed_within.holdings, store, as_of=AS_OF)
    drift_within = analyze_portfolio_drift(val_within, target)
    assert not drift_within.breached
    assert [d.bucket for d in drift_within.drift_items if d.breached] == []
    assert drift_within.trades == []

    # Case 2: Absolute drift > 5pp triggers (Equity 58% vs 50% [+8pp], Debt 32% vs 40% [-8pp], Gold 10% vs 10%)
    csv_abs_breach = "\n".join(
        [
            "code,units,avg_cost,as_of",
            f"DEMO040,{58_000 / nav_eq:.6f},10.0,2026-04-01",  # held < 365 days -> exit load + STCG warning
            f"DEMO010,{32_000 / nav_debt:.6f},10.0,2024-01-10",
            f"DEMO080,{10_000 / nav_gold:.6f},10.0,2024-01-10",
        ]
    )
    parsed_abs = parse_portfolio_csv(csv_abs_breach, store, max_as_of=AS_OF)
    val_abs = valuate_portfolio(parsed_abs.holdings, store, as_of=AS_OF)
    drift_abs = analyze_portfolio_drift(val_abs, target)
    assert drift_abs.breached
    breached_abs = [d.bucket for d in drift_abs.drift_items if d.breached]
    assert Bucket.EQUITY in breached_abs
    assert Bucket.DEBT in breached_abs

    # Verify rupee deltas and exit-load / tax-impact warnings before suggesting a sell
    eq_sell = next(t for t in drift_abs.trades if t.instrument_id == "DEMO040")
    assert eq_sell.action == Action.DECREASE
    assert eq_sell.amount_delta == pytest.approx(-8_000.0, abs=10.0)
    assert eq_sell.exit_load_warning is not None and "Exit-load warning" in eq_sell.exit_load_warning
    assert eq_sell.tax_warning is not None and "STCG" in eq_sell.tax_warning
    assert drift_abs.exit_load_warnings

    debt_buy = next(t for t in drift_abs.trades if t.instrument_id == "DEMO010")
    assert debt_buy.action == Action.INCREASE
    assert debt_buy.amount_delta == pytest.approx(8_000.0, abs=10.0)

    # Case 3: Relative drift > 25% on small bucket triggers even when absolute drift <= 5pp
    # Gold is 13% vs 10% target (+3pp abs, +30% relative > 25%), Equity 47% vs 50% (-3pp abs, -6% relative)
    csv_rel_breach = "\n".join(
        [
            "code,units,avg_cost,as_of",
            f"DEMO040,{47_000 / nav_eq:.6f},10.0,2024-01-10",
            f"DEMO010,{40_000 / nav_debt:.6f},10.0,2024-01-10",
            f"DEMO080,{13_000 / nav_gold:.6f},10.0,2024-01-10",
        ]
    )
    parsed_rel = parse_portfolio_csv(csv_rel_breach, store, max_as_of=AS_OF)
    val_rel = valuate_portfolio(parsed_rel.holdings, store, as_of=AS_OF)
    drift_rel = analyze_portfolio_drift(val_rel, target)
    assert drift_rel.breached
    breached_rel = [d.bucket for d in drift_rel.drift_items if d.breached]
    assert breached_rel == [Bucket.GOLD]
    gold_sell = next(t for t in drift_rel.trades if t.instrument_id == "DEMO080")
    assert gold_sell.action == Action.DECREASE
    assert gold_sell.amount_delta == pytest.approx(-3_000.0, abs=10.0)
    assert gold_sell.tax_warning is not None and "LTCG" in gold_sell.tax_warning


def test_elss_lock_in_warning_on_sell():
    exit_w, tax_w, warnings = build_sell_warnings(
        name="Orchid ELSS Tax Saver - Direct Plan",
        category="ELSS",
        bucket=Bucket.EQUITY,
        sell_amount=15_000.0,
        holding_days=500,  # < 3 years (1095 days)
        unrealized_gain=3_000.0,
        current_value=25_000.0,
    )
    assert exit_w is not None and "LOCK-IN WARNING" in exit_w
    assert tax_w is not None and "LTCG" in tax_w
    assert len(warnings) == 2


def test_update_profile_from_portfolio_increments_version_and_updates_hhi(conn):
    store = MetadataStore(conn)
    goal = Goal(
        amount=100_000,
        horizon_years=5,
        risk_preference=RiskPreference.MODERATE,
    )
    res = advise(goal, ScriptedLLM(), conn, as_of=AS_OF)
    prof_v1 = res.profile
    assert prof_v1.version == 1
    assert res.assumptions.hhi is None

    # Upload a concentrated single-fund portfolio (HHI = 1.0 -> diversification sub-score = 0.0)
    csv_concentrated = "code,units,avg_cost,as_of\nDEMO040,1000,10.0,2024-06-01\n"
    parsed = parse_portfolio_csv(csv_concentrated, store, max_as_of=AS_OF)
    val = valuate_portfolio(parsed.holdings, store, as_of=AS_OF)
    assert val.hhi == pytest.approx(1.0, abs=1e-4)

    prof_v2, assumptions_v2 = update_profile_from_portfolio(
        conn,
        val,
        goal,
        prev_profile=prof_v1,
        assumptions=res.assumptions,
    )
    assert prof_v2.profile_id == prof_v1.profile_id
    assert prof_v2.version == 2
    assert assumptions_v2.hhi == pytest.approx(1.0, abs=1e-4)
    assert prof_v2.inputs["hhi"] == pytest.approx(1.0, abs=1e-4)
    # Concentrated existing allocation lowers risk score relative to default 0.5 diversification factor
    assert prof_v2.risk_score < prof_v1.risk_score

    pstore = ProfileStore(conn)
    assert pstore.get(prof_v1.profile_id, 1) == prof_v1
    assert pstore.get(prof_v1.profile_id, 2) == prof_v2
    assert pstore.latest(prof_v1.profile_id) == prof_v2
    assert AuditLog(conn).verify() == (True, None)

    # Also verify PortfolioStore snapshot persistence
    PortfolioStore(conn).save(prof_v1.profile_id, parsed.holdings, val)
    loaded_snap = PortfolioStore(conn).get_valuation(prof_v1.profile_id)
    assert loaded_snap is not None
    assert loaded_snap.total_value == pytest.approx(val.total_value, abs=1e-2)


def test_api_upload_portfolio_endpoint(conn):
    goal = Goal(
        amount=100_000,
        horizon_years=5,
        risk_preference=RiskPreference.MODERATE,
    )
    res = advise(goal, ScriptedLLM(), conn, as_of=AS_OF)

    app.dependency_overrides[get_db_conn] = lambda: conn
    try:
        client = TestClient(app)
        csv_bytes = (
            "scheme,units,avg_cost,as_of\n"
            "DEMO040,2500,10.0,2025-01-15\n"
            "DEMO010,500,10.0,2025-01-15\n"
        ).encode("utf-8")
        resp = client.post(
            f"/api/portfolio/upload?profile_id={res.profile.profile_id}&as_of={AS_OF.isoformat()}",
            files={"file": ("holdings.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["filename"] == "holdings.csv"
        assert body["holdings_count"] == 2
        assert body["total_value"] > 0
        assert body["errors"] == []
        assert body["drift"] is not None
        assert body["profile_version"] == 2
    finally:
        app.dependency_overrides.pop(get_db_conn, None)
