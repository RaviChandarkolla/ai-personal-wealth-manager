from datetime import date, timedelta
import pandas as pd
import pytest

from wealth_advisor.data.amfi_aum import load_aum_into_master
from wealth_advisor.data.amfi_ter import load_ter_into_master
from wealth_advisor.data.fund_refresh import refresh_fund
from wealth_advisor.data.news import FundNewsSummary
from wealth_advisor.data import news as news_mod
from wealth_advisor.data.store import (
    FundRecord,
    FundRefreshRecord,
    MetadataStore,
    SchemeMasterRecord,
)
from wealth_advisor.db import open_db
from wealth_advisor.models import Bucket


def _seed_two_schemes(store: MetadataStore) -> None:
    store.upsert_master(
        [
            SchemeMasterRecord(
                code="120503",
                name="Axis Bluechip Fund - Direct Plan - Growth",
                amc="Axis Mutual Fund",
                category="Large Cap",
                sebi_category="Equity Scheme - Large Cap Fund",
                plan="direct",
                option="growth",
                isin_growth="INF846K01DP8",
                expense_ratio=0.55,
                aum_cr=32000.0,
                latest_nav=50.0,
                nav_date=date(2026, 10, 10),
                source="amfi",
                as_of=date(2026, 10, 10),
            ),
            SchemeMasterRecord(
                code="120504",
                name="Axis Bluechip Fund - Regular Plan - Growth",
                amc="Axis Mutual Fund",
                category="Large Cap",
                sebi_category="Equity Scheme - Large Cap Fund",
                plan="regular",
                option="growth",
                isin_growth="INF846K01EW2",
                expense_ratio=1.65,
                aum_cr=32000.0,
                latest_nav=45.0,
                nav_date=date(2026, 10, 10),
                source="amfi",
                as_of=date(2026, 10, 10),
            ),
        ]
    )
    store.add_funds(
        [
            FundRecord(
                code="120503",
                name="Axis Bluechip Fund - Direct Plan - Growth",
                category="Large Cap",
                bucket=Bucket.EQUITY,
                expense_ratio=0.55,
                aum_cr=32000.0,
                source="amfi",
                as_of=date(2026, 10, 10),
            ),
            FundRecord(
                code="120504",
                name="Axis Bluechip Fund - Regular Plan - Growth",
                category="Large Cap",
                bucket=Bucket.EQUITY,
                expense_ratio=1.65,
                aum_cr=32000.0,
                source="amfi",
                as_of=date(2026, 10, 10),
            ),
        ]
    )


def test_store_log_refresh_and_refresh_history():
    conn = open_db(":memory:")
    store = MetadataStore(conn)

    rec1 = FundRefreshRecord(
        code="120503",
        refreshed_at="2026-10-20T10:00:00Z",
        data_mode="real",
        status="ok",
        sources=["mfapi", "google_news_rss"],
        nav_points_before=100,
        nav_points_after=105,
        nav_date_before=date(2026, 10, 15),
        nav_date_after=date(2026, 10, 20),
        latest_nav=51.25,
        ter_before=0.55,
        ter_after=0.55,
        aum_before=32000.0,
        aum_after=32000.0,
        news_score=62.0,
        sentiment_score=58.0,
        errors=[],
    )
    saved1 = store.log_refresh(rec1)
    assert saved1.id is not None and saved1.id > 0

    rec2 = FundRefreshRecord(
        code="120503",
        refreshed_at="2026-10-21T10:00:00Z",
        data_mode="real",
        status="partial",
        sources=["mfapi", "amfi_ter"],
        nav_points_before=105,
        nav_points_after=106,
        nav_date_before=date(2026, 10, 20),
        nav_date_after=date(2026, 10, 21),
        latest_nav=51.50,
        ter_before=0.55,
        ter_after=0.49,
        aum_before=32000.0,
        aum_after=32000.0,
        news_score=65.0,
        sentiment_score=60.0,
        errors=["amfi_aum: timeout"],
    )
    store.log_refresh(rec2)

    history = store.refresh_history("120503", limit=5)
    assert len(history) == 2
    assert history[0].refreshed_at == "2026-10-21T10:00:00Z"
    assert history[0].status == "partial"
    assert history[0].errors == ["amfi_aum: timeout"]
    assert history[1].refreshed_at == "2026-10-20T10:00:00Z"
    conn.close()


def test_ter_and_aum_only_codes_filtering():
    conn = open_db(":memory:")
    store = MetadataStore(conn)
    _seed_two_schemes(store)

    # 1. TER with only_codes={"120503"} should only update 120503, leaving 120504 untouched
    ter_csv = (
        "Scheme Name,Regular Plan - Total TER (%),Direct Plan - Total TER (%)\n"
        "Axis Bluechip Fund,1.50,0.48\n"
    )
    ter_report = load_ter_into_master(
        store, ter_csv, default_as_of=date(2026, 10, 20), only_codes={"120503"}
    )
    assert ter_report.rows_matched >= 1
    assert store.get_master("120503").expense_ratio == pytest.approx(0.48)
    assert store.get_master("120504").expense_ratio == pytest.approx(1.65)

    # 2. AUM with only_codes={"120503"} should still aggregate prefix total across Direct + Regular
    #    rows (20000 + 15000 = 35000 Cr), but only write 120503.
    aum_csv = (
        "AMFI Code,Scheme Name,Average AUM (Rs. in Crore)\n"
        "120503,Axis Bluechip Fund - Direct Plan - Growth,20000.0\n"
        "120504,Axis Bluechip Fund - Regular Plan - Growth,15000.0\n"
    )
    aum_report = load_aum_into_master(
        store, aum_csv, default_as_of=date(2026, 10, 20), only_codes={"120503"}
    )
    assert aum_report.rows_matched == 1
    assert store.get_master("120503").aum_cr == pytest.approx(35000.0)
    assert store.get_master("120504").aum_cr == pytest.approx(32000.0)
    conn.close()


def test_news_rss_refresh_bypasses_cache(monkeypatch):
    news_mod._RSS_CACHE.clear()
    calls = {"count": 0}

    class FakeUrlOpenResp:
        def __init__(self, title: str):
            self._bytes = f"""<?xml version="1.0" encoding="UTF-8"?>
            <rss version="2.0"><channel>
              <item>
                <title>{title} - Moneycontrol</title>
                <link>https://example.com/1</link>
                <pubDate>Tue, 20 Oct 2026 10:00:00 GMT</pubDate>
                <source>Moneycontrol</source>
              </item>
            </channel></rss>""".encode("utf-8")

        def read(self):
            return self._bytes

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def fake_urlopen(req, timeout=2.0):
        calls["count"] += 1
        return FakeUrlOpenResp(f"Headline #{calls['count']}")

    monkeypatch.setattr(news_mod.urllib.request, "urlopen", fake_urlopen)

    items1 = news_mod._fetch_google_news_rss("Axis Bluechip Fund", refresh=False)
    assert calls["count"] == 1
    assert items1[0]["title"] == "Headline #1"

    # Cached call does not hit urlopen
    items2 = news_mod._fetch_google_news_rss("Axis Bluechip Fund", refresh=False)
    assert calls["count"] == 1
    assert items2[0]["title"] == "Headline #1"

    # refresh=True bypasses cache and updates it
    items3 = news_mod._fetch_google_news_rss("Axis Bluechip Fund", refresh=True)
    assert calls["count"] == 2
    assert items3[0]["title"] == "Headline #2"


def test_refresh_fund_orchestration_and_partial_resilience(monkeypatch):
    conn = open_db(":memory:")
    store = MetadataStore(conn)
    _seed_two_schemes(store)

    # Seed initial 400 days of NAVs
    base_date = date(2025, 1, 1)
    idx_before = pd.DatetimeIndex([base_date + timedelta(days=i) for i in range(400)])
    series_before = pd.Series([40.0 + i * 0.02 for i in range(400)], index=idx_before)
    store.save_nav("120503", series_before)

    def fake_fetch_mfapi_json(code: str, **kwargs):
        return {
            "meta": {
                "scheme_code": int(code),
                "scheme_name": "Axis Bluechip Fund - Direct Plan - Growth",
                "fund_house": "Axis Mutual Fund",
                "scheme_type": "Open Ended Schemes",
                "scheme_category": "Equity Scheme - Large Cap Fund",
                "isin_growth": "INF846K01DP8",
            },
            "data": [
                {
                    "date": (base_date + timedelta(days=i)).strftime("%d-%m-%Y"),
                    "nav": f"{40.0 + i * 0.02:.4f}",
                }
                for i in range(402)
            ],
        }

    def fake_sync_ter(st: MetadataStore, *, only_codes=None):
        assert only_codes == {"120503"}
        st.conn.execute(
            "UPDATE scheme_master SET expense_ratio = 0.49 WHERE code = '120503'"
        )
        st.conn.execute(
            "UPDATE scheme SET expense_ratio = 0.49 WHERE code = '120503'"
        )
        st.conn.commit()

    def fake_sync_aum(st: MetadataStore, *, only_codes=None):
        raise RuntimeError("AMFI AUM endpoint 503")

    def fake_get_fund_news(*, code: str, name: str, category: str, amc: str = "", as_of=None, fetch_live=True, refresh=False):
        assert refresh is True
        return FundNewsSummary(
            code=code,
            name=name,
            category=category,
            amc=amc,
            news_score=68.0,
            sentiment_score=62.0,
            overall_sentiment="positive",
            trend="improving",
            sample_size=2,
            as_of=as_of or date.today(),
            feed_mode="hybrid",
        )

    monkeypatch.setattr("wealth_advisor.data.fund_refresh.fetch_mfapi_json", fake_fetch_mfapi_json)
    monkeypatch.setattr("wealth_advisor.data.fund_refresh.sync_latest_ter", fake_sync_ter)
    monkeypatch.setattr("wealth_advisor.data.fund_refresh.sync_latest_aum", fake_sync_aum)
    monkeypatch.setattr("wealth_advisor.data.fund_refresh.get_fund_news", fake_get_fund_news)

    report = refresh_fund(
        store,
        "120503",
        include_ter_aum=True,
        include_news=True,
        data_mode="real",
    )

    # Because AUM failed while mfapi + TER + news succeeded, status is "partial"
    assert report.status == "partial"
    assert "mfapi" in report.sources
    assert "amfi_ter" in report.sources
    assert "google_news_rss" in report.sources
    assert report.nav_points_before == 400
    assert report.nav_points_after == 402
    assert report.ter_before == pytest.approx(0.55)
    assert report.ter_after == pytest.approx(0.49)
    assert report.news_score == pytest.approx(68.0)
    assert "nav_points" in report.changed_fields
    assert "expense_ratio" in report.changed_fields
    assert any("amfi_aum" in e for e in report.errors)

    # Check refresh history was persisted
    logs = store.refresh_history("120503", limit=5)
    assert len(logs) == 1
    assert logs[0].status == "partial"
    assert logs[0].nav_points_after == 402
    conn.close()
