from datetime import date

from wealth_advisor.data.amfi import build_scheme_master, parse_nav_all, sync_scheme_master
from wealth_advisor.data.amfi_aum import load_aum_into_master, parse_aum_text
from wealth_advisor.data.amfi_ter import load_ter_into_master, parse_ter_text
from wealth_advisor.data.store import MetadataStore
from wealth_advisor.db import open_db

SAMPLE_NAV_ALL = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Large Cap Fund)
Axis Mutual Fund
120503;INF846K01EW2;-;Axis Bluechip Fund - Direct Plan - Growth;58.4200;15-Oct-2026
120504;INF846K01EX0;-;Axis Bluechip Fund - Regular Plan - Growth;52.1100;15-Oct-2026
120505;INF846K01EY8;-;Axis Bluechip Fund - Direct Plan - IDCW;24.1000;15-Oct-2026

Open Ended Schemes(Debt Scheme - Liquid Fund)
HDFC Mutual Fund
119091;INF179KB1HP9;-;HDFC Liquid Fund - Direct Plan - Growth Option;4890.1234;15-Oct-2026

Close Ended Schemes(Income)
ICICI Prudential Mutual Fund
999001;INF109K01XX1;-;ICICI Closed Tenure Fund - Direct - Growth;12.3400;15-Oct-2026
"""


def test_build_and_sync_scheme_master_parses_open_ended_sections():
    rows, rejected = parse_nav_all(SAMPLE_NAV_ALL)
    assert rejected == []

    # Default: direct + growth only
    default_records = build_scheme_master(rows)
    assert {r.code for r in default_records} == {"120503", "119091"}

    # Configured: allow direct + regular, growth + idcw
    records = build_scheme_master(
        rows,
        allowed_plans=["direct", "regular"],
        allowed_options=["growth", "idcw"],
    )
    # Close-ended scheme 999001 should be skipped
    codes = {r.code for r in records}
    assert codes == {"120503", "120504", "120505", "119091"}

    by_code = {r.code: r for r in records}
    r_axis = by_code["120503"]
    assert r_axis.amc == "Axis Mutual Fund"
    assert r_axis.category == "Large Cap"
    assert r_axis.plan == "direct"
    assert r_axis.option == "growth"
    assert r_axis.latest_nav == 58.42
    assert r_axis.as_of == date(2026, 10, 15)

    r_reg = by_code["120504"]
    assert r_reg.plan == "regular"
    assert r_reg.option == "growth"

    r_idcw = by_code["120505"]
    assert r_idcw.plan == "direct"
    assert r_idcw.option == "idcw"

    r_hdfc = by_code["119091"]
    assert r_hdfc.category == "Liquid"


def test_search_master_filters_and_categories():
    conn = open_db(":memory:")
    store = MetadataStore(conn)
    count, rejected = sync_scheme_master(store, SAMPLE_NAV_ALL)
    assert count == 2
    assert rejected == []

    # Default filter: direct + growth -> 120503 and 119091
    default_hits = store.search_master()
    assert {r.code for r in default_hits} == {"120503", "119091"}

    # Sync again with all plans and options allowed
    count_all, _ = sync_scheme_master(
        store,
        SAMPLE_NAV_ALL,
        allowed_plans=["direct", "regular"],
        allowed_options=["growth", "idcw"],
        purge_disallowed=False,
    )
    assert count_all == 4

    # Filter by query + plan="all"
    axis_all = store.search_master(q="Axis Bluechip", plan="all", option="all")
    assert {r.code for r in axis_all} == {"120503", "120504", "120505"}

    # Filter by category
    debt_only = store.search_master(category="Liquid", plan="direct", option="growth")
    assert [r.code for r in debt_only] == ["119091"]

    # Categories list
    cats = store.master_categories()
    assert [c["category"] for c in cats] == ["Large Cap", "Liquid"]
    conn.close()


def test_ter_and_aum_parsers_and_master_enrichment():
    conn = open_db(":memory:")
    store = MetadataStore(conn)
    sync_scheme_master(store, SAMPLE_NAV_ALL)

    sample_ter_csv = """Scheme Code,Scheme Name,Direct Plan - Total TER (%),Regular Plan - Total TER (%),Date
120503,Axis Bluechip Fund - Direct Plan - Growth,0.48,1.55,15-Oct-2026
,HDFC Liquid Fund - Direct Plan - Growth Option,0.18,0.30,2026-10-15
"""
    ter_records, ter_rejected = parse_ter_text(sample_ter_csv)
    assert ter_rejected == []
    assert len(ter_records) >= 2

    ter_rep = load_ter_into_master(store, sample_ter_csv)
    assert ter_rep.rows_matched >= 2

    m_axis = store.get_master("120503")
    assert m_axis is not None
    assert m_axis.expense_ratio == 0.48

    m_hdfc = store.get_master("119091")
    assert m_hdfc is not None
    assert m_hdfc.expense_ratio == 0.18

    # Sample AUM export: one row in Crores, another with Lakhs header
    sample_aum_csv = """Scheme Code,Scheme Name,Average AUM (Rs. in Crores),Quarter Ended
120503,Axis Bluechip Fund - Direct Plan - Growth,34500.50,30-Sep-2026
"""
    aum_records, aum_rejected = parse_aum_text(sample_aum_csv)
    assert aum_rejected == []
    assert len(aum_records) == 1
    assert aum_records[0].aum_cr == 34500.50

    aum_rep = load_aum_into_master(store, sample_aum_csv)
    assert aum_rep.rows_matched == 1
    assert store.get_master("120503").aum_cr == 34500.50

    # Test Lakhs conversion (100 Lakhs = 1 Crore)
    sample_aum_lakhs = """Scheme Code,Scheme Name,AAUM (Rs. in Lakhs)
119091,HDFC Liquid Fund - Direct Plan - Growth Option,500000
"""
    load_aum_into_master(store, sample_aum_lakhs)
    assert store.get_master("119091").aum_cr == 5000.0
    conn.close()


def test_parse_and_sync_eight_column_amfi_format():
    sample_8col = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Small Cap Fund)
quant Mutual Fund
120828;INF966L01683;-;Quant Small Cap Fund;Direct Plan;Growth Option;265.4123;16-Oct-2026
100177;INF966L01176;-;Quant Small Cap Fund;Regular Plan;Growth Option;238.1100;16-Oct-2026
120827;INF966L01667;INF966L01675;Quant Small Cap Fund;Direct Plan;IDCW Option;98.5000;16-Oct-2026
"""
    conn = open_db(":memory:")
    store = MetadataStore(conn)
    # Default sync filters out Regular and IDCW
    count_default, rejected = sync_scheme_master(store, sample_8col)
    assert rejected == []
    assert count_default == 1

    # Configured sync accepts all
    count, rejected = sync_scheme_master(
        store,
        sample_8col,
        allowed_plans=["direct", "regular"],
        allowed_options=["growth", "idcw"],
        purge_disallowed=False,
    )
    assert rejected == []
    assert count == 3

    rec = store.get_master("120828")
    assert rec is not None
    assert rec.name == "Quant Small Cap Fund - Direct Plan - Growth Option"
    assert rec.category == "Small Cap"
    assert rec.plan == "direct"
    assert rec.option == "growth"
    assert rec.latest_nav == 265.4123

    hits = store.search_master(q="Quant Small", plan="direct", option="growth")
    assert [r.code for r in hits] == ["120828"]
    conn.close()


def test_scheme_master_enriched_and_queried_in_pipeline():
    import pandas as pd
    from wealth_advisor.agents.followup import explain_exclusion
    from wealth_advisor.data.stats import compute_all_stats
    from wealth_advisor.models.core import Goal

    sample_bandhan = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Small Cap Fund)
Bandhan Mutual Fund
147946;INF194KB1AL4;-;BANDHAN Small Cap Fund - Direct Plan - Growth;48.7500;15-Oct-2026
"""
    conn = open_db(":memory:")
    store = MetadataStore(conn)
    count, rejected = sync_scheme_master(store, sample_bandhan)
    assert count == 1
    assert rejected == []

    master = store.get_master("147946")
    assert master is not None
    assert master.expense_ratio == 0.41
    assert master.aum_cr == 9480.0

    # Save >30 NAV points so 147946 has full NAV history & stats
    dates = pd.bdate_range("2020-03-01", "2026-10-15")
    nav = pd.Series([10.0 * (1.0007 ** i) for i in range(len(dates))], index=dates)
    store.save_nav("147946", nav)
    compute_all_stats(store)

    # query_funds now includes 147946 from scheme_master with complete metadata
    small_caps = store.query_funds(category="Small Cap")
    assert len(small_caps) == 1
    bandhan = small_caps[0]
    assert bandhan.code == "147946"
    assert bandhan.expense_ratio == 0.41
    assert bandhan.aum_cr == 9480.0
    assert bandhan.launch_date is not None

    # Suitability check no longer flags missing_data on expense_ratio or aum_cr
    goal = Goal(purpose="wealth", horizon_years=10, risk_preference="aggressive", amount=500000)
    ctx = {
        "rule_set": "set1",
        "target": {"equity": 75.0, "debt": 15.0, "gold": 10.0},
        "shortlisted_by_bucket": {"equity": ["118778"]},
        "recommended_ids": ["118778"],
    }
    reasons = explain_exclusion(bandhan, ctx, goal, "set1")
    assert not any(r.get("rule") == "missing_data" for r in reasons)
    conn.close()


def test_amfi_ter_2026_json_and_hardened_amc_alias_matching(monkeypatch):
    import json
    from wealth_advisor.data import amfi_ter

    sample_nav = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Flexi Cap Fund)
Aditya Birla Sun Life Mutual Fund
120560;INF209K01YY1;-;Aditya Birla Sun Life Flexi Cap Fund - Growth - Direct Plan;1500.2500;15-Oct-2026
120561;INF209K01YY2;-;Aditya Birla Sun Life Flexi Cap Fund - Growth - Regular Plan;1350.1000;15-Oct-2026

Open Ended Schemes(Equity Scheme - Small Cap Fund)
Bandhan Mutual Fund
147946;INF194KB1AL4;-;BANDHAN Small Cap Fund - Direct Plan - Growth;48.7500;15-Oct-2026
"""
    conn = open_db(":memory:")
    store = MetadataStore(conn)
    sync_scheme_master(
        store,
        sample_nav,
        allowed_plans=["direct", "regular"],
        allowed_options=["growth", "idcw"],
        purge_disallowed=False,
    )

    # Simulate AMFI's 2026 TER API rows with daily timestamps and AMC alias differences
    ter_payload = {
        "data": [
            {
                "Scheme_Name": "Aditya Birla SL Flexicap Fund (Formerly known as ABSL Equity Fund)",
                "TER_Date": "2026-10-01T00:00:00.000Z",
                "R_BER": 1.45,
                "R_TER": 1.68,
                "D_BER": 0.72,
                "D_TER": 0.85,
            },
            {
                "Scheme_Name": "Aditya Birla SL Flexicap Fund (Formerly known as ABSL Equity Fund)",
                "TER_Date": "2026-10-15T00:00:00.000Z",
                "R_BER": 1.40,
                "R_TER": 1.62,
                "D_BER": 0.68,
                "D_TER": 0.79,
            },
            {
                "Scheme_Name": "Bandhan Small Cap Fund",
                "TER_Date": 46310,  # Excel serial date
                "R_BER": 1.49,
                "R_TER": 1.71,
                "D_BER": 0.34,
                "D_TER": 0.39,
            },
        ]
    }

    monkeypatch.setattr(amfi_ter, "fetch_latest_ter", lambda timeout=60: json.dumps(ter_payload))
    rep = amfi_ter.sync_latest_ter(store)
    assert rep.rows_matched >= 3

    absl_dir = store.get_master("120560")
    absl_reg = store.get_master("120561")
    bandhan = store.get_master("147946")
    # Latest date row (2026-10-15) wins over older daily row (2026-10-01)
    assert absl_dir.expense_ratio == 0.79
    assert absl_dir.ter_as_of == date(2026, 10, 15)
    assert absl_reg.expense_ratio == 1.62
    assert bandhan.expense_ratio == 0.39
    conn.close()


def test_amfi_aum_nested_json_and_sync_latest_aum(monkeypatch):
    import json
    from wealth_advisor.data import amfi_aum

    sample_nav = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Small Cap Fund)
Bandhan Mutual Fund
147946;INF194KB1AL4;-;BANDHAN Small Cap Fund - Direct Plan - Growth;48.7500;15-Oct-2026
"""
    conn = open_db(":memory:")
    store = MetadataStore(conn)
    sync_scheme_master(store, sample_nav)

    # Simulate AMFI's `/api/average-aum-schemewise` nested JSON payload (values in Rs. Lakhs)
    aum_payload = {
        "period": "July - September 2026",
        "data": [
            {
                "strMFId": "0",
                "Mfname": "Bandhan Mutual Fund",
                "SchemeCat_Desc": "Equity Scheme - Small Cap Fund",
                "schemes": [
                    {
                        "SchemeNAVName": "BANDHAN SMALL CAP FUND - DIRECT PLAN GROWTH",
                        "AMFI_Code": 147946,
                        "AverageAumForTheMonth": {
                            "ExcludingFundOfFundsDomesticButIncludingFundOfFundsOverseas": 756248.16,
                            "FundOfFundsDomestic": 0,
                        },
                    }
                ],
            }
        ],
    }

    monkeypatch.setattr(amfi_aum, "fetch_latest_aum", lambda timeout=45: json.dumps(aum_payload))
    rep = amfi_aum.sync_latest_aum(store)
    assert rep.rows_matched == 1

    bandhan = store.get_master("147946")
    assert bandhan.aum_cr == 7562.48
    assert bandhan.aum_as_of == date(2026, 9, 30)
    conn.close()



