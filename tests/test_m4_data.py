from datetime import date

import pandas as pd

from wealth_advisor.cli import main
from wealth_advisor.data.amfi import find_gaps, load_nav_all, parse_nav_all
from wealth_advisor.data.stats import _percentile
from wealth_advisor.data.store import FundRecord, MetadataStore, load_funds_csv
from wealth_advisor.db import open_db
from wealth_advisor.models import Bucket

AMFI = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Debt Scheme - Liquid Fund)

Test Mutual Fund
100001;INF000A01011;-;Test Liquid Fund - Direct;1234.5678;09-Oct-2026
100002;INF000A01022;-;Test Bad Fund;N.A.;09-Oct-2026
100003;INF000A01033;-;Test Zero Fund;0;09-Oct-2026
100004;INF999A09999;-;Test Mismatch Fund;10.5;09-Oct-2026
"""


def _store():
    return MetadataStore(open_db(":memory:"))


def _rec(code, isin, **kw):
    base = dict(code=code, isin=isin, name=f"Fund {code}", category="Liquid", bucket=Bucket.DEBT,
                source="test", as_of=date(2026, 10, 9))
    return FundRecord(**{**base, **kw})


def test_amfi_parse_validates_rows():
    rows, rejected = parse_nav_all(AMFI)
    assert [r.code for r in rows] == ["100001", "100004"]
    assert rows[0].amc == "Test Mutual Fund" and rows[0].date == date(2026, 10, 9)
    assert len(rejected) == 2


def test_amfi_load_is_idempotent_and_flags_isin_mismatch():
    store = _store()
    store.add_funds([_rec("100001", "INF000A01011"), _rec("100004", "INF111A01111")])
    r1 = load_nav_all(store, AMFI)
    r2 = load_nav_all(store, AMFI)
    assert r1.rows_loaded == r2.rows_loaded == 1
    assert len(r1.isin_mismatch) == 1
    assert len(store.nav_series("100001")) == 1


def test_gap_detection():
    s = pd.Series([1.0, 1.1, 1.2], index=pd.to_datetime(["2026-01-01", "2026-01-03", "2026-02-01"]))
    assert find_gaps(s) == [(date(2026, 1, 3), date(2026, 2, 1))]


def test_exact_numeric_filters():
    store = _store()
    store.add_funds([
        _rec("A", "I1", expense_ratio=0.5, aum_cr=1000, rating=5),
        _rec("B", "I2", expense_ratio=0.8, aum_cr=400, rating=3),
        _rec("C", "I3", expense_ratio=None, aum_cr=None, rating=None, category="Gold ETF",
             bucket=Bucket.GOLD),
    ])
    assert [f.code for f in store.query_funds(max_expense=0.5)] == ["A"]
    assert [f.code for f in store.query_funds(min_aum=400)] == ["A", "B"]
    assert [f.code for f in store.query_funds(min_rating=4)] == ["A"]
    assert [f.code for f in store.query_funds(category="Gold ETF")] == ["C"]
    assert [f.code for f in store.query_funds(categories=["Liquid"], plan="direct")] == ["A", "B"]
    assert store.query_funds(categories=[]) == []


def test_csv_loader_reports_row_errors(tmp_path):
    p = tmp_path / "funds.csv"
    p.write_text(
        "code,isin,name,category,plan,expense_ratio,aum_cr,rating,source,as_of\n"
        "X1,INF1,Good Fund,Short Duration,direct,0.4,900,4,factsheet,2026-09-30\n"
        "X2,INF2,Odd Fund,Weird Category,direct,0.4,900,4,factsheet,2026-09-30\n"
        "X3,INF3,No Source,Liquid,direct,0.2,900,4,,2026-09-30\n"
    )
    records, errors = load_funds_csv(p)
    assert [r.code for r in records] == ["X1"] and len(errors) == 2
    assert records[0].bucket == Bucket.DEBT


def test_percentile():
    assert _percentile(4, [1, 2, 3, 4]) == 87.5
    assert _percentile(5, [5]) == 50.0


def test_demo_universe_and_stats(demo_conn):
    store = MetadataStore(demo_conn)
    assert len(store.query_funds()) == 45
    liquid = store.query_funds(category="Short Duration", max_expense=0.8)
    assert liquid and all(f.source == "synthetic_demo" for f in liquid)
    stats = store.get_stats("DEMO010")
    assert stats and stats.cagr_3y and stats.pct_roll_3y is not None
    young = store.get_stats("DEMO012")  # ~2.5y history: no 3y/5y figures
    assert young and young.cagr_3y is None and young.cagr_5y is None


def test_cli_funds_command(tmp_path, capsys):
    db = tmp_path / "w.db"
    assert main(["--db", str(db), "seed-demo"]) == 0
    assert main(["--db", str(db), "funds", "--category", "Short Duration", "--max-expense", "0.8"]) == 0
    out = capsys.readouterr().out
    assert "synthetic_demo" in out and "Short Duration" in out
    assert "fund(s)" in out


def test_settings_active_db_path_switches_by_data_mode(tmp_path):
    from pathlib import Path
    from wealth_advisor.config import PROJECT_ROOT, Settings

    s_syn = Settings(data_mode="synthetic")
    assert s_syn.active_db_path() == PROJECT_ROOT / "data/wealth_synthetic.db"
    assert s_syn.active_db_path("real") == PROJECT_ROOT / "data/wealth_real.db"

    s_real = Settings(data_mode="real")
    assert s_real.active_db_path() == PROJECT_ROOT / "data/wealth_real.db"

    custom = tmp_path / "custom.db"
    s_override = Settings(data_mode="real", db_path=custom)
    assert s_override.active_db_path() == custom
    assert s_override.active_db_path("synthetic") == custom


def test_curated_real_funds_csv_loads_cleanly():
    from pathlib import Path
    from wealth_advisor.domain.categories import CATEGORIES

    csv_path = Path("data/curated_funds.csv")
    if not csv_path.exists():
        csv_path = Path(__file__).resolve().parents[1] / "data/curated_funds.csv"
    records, errors = load_funds_csv(csv_path)
    assert errors == []
    assert len(records) == 60
    assert {r.category for r in records} == set(CATEGORIES.keys())


def test_mfapi_parser_and_bootstrap_real(monkeypatch, tmp_path):
    from wealth_advisor.bootstrap import bootstrap_real
    from wealth_advisor.data import real_fetcher

    days = pd.bdate_range("2020-01-01", "2026-10-09")
    sample_data = [
        {"date": d.strftime("%d-%m-%Y"), "nav": f"{10.0 * (1.0004 ** i):.4f}"}
        for i, d in enumerate(days)
    ]

    def fake_fetch_mfapi_json(code: str, base_url: str | None = None, timeout: int = 25) -> dict:
        return {
            "meta": {
                "fund_house": "Test AMC",
                "scheme_code": int(code) if code.isdigit() else 100001,
                "scheme_name": f"Scheme {code}",
                "isin_growth": f"INF{code.zfill(9)}",
            },
            "data": list(reversed(sample_data)),
            "status": "SUCCESS",
        }

    monkeypatch.setattr(real_fetcher, "fetch_mfapi_json", fake_fetch_mfapi_json)
    csv_path = tmp_path / "mini_curated.csv"
    csv_path.write_text(
        "code,isin,name,amc,category,plan,option,expense_ratio,aum_cr,rating,benchmark,launch_date,manager,source,as_of\n"
        "122639,INF879O01027,Parag Parikh Flexi Cap Fund - Direct Plan - Growth,PPFAS Mutual Fund,Flexi Cap,direct,growth,0.63,82500,5,NIFTY 500 TRI,2013-05-24,Rajeev Thakkar,amfi_mfapi,2026-10-01\n"
    )
    conn = open_db(":memory:")
    report = bootstrap_real(conn, csv_path=csv_path, provider_name="mfapi_amfi")
    assert report.funds_loaded == 1
    assert report.schemes_synced == 1
    assert report.nav_points_saved == len(days)
    assert report.latest_nav_date == date(2026, 10, 9)

    store = MetadataStore(conn)
    stats = store.get_stats("122639")
    assert stats is not None and stats.cagr_5y is not None and stats.cagr_5y > 0
