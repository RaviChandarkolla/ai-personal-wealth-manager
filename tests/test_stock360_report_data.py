import sqlite3

from wealth_advisor.data.stocks.report_data import ReportDataStore, parse_chart_ohlcv
from wealth_advisor.data.stocks.synthetic import _STOCK_PROFILES, SyntheticStockProvider

PAYLOAD = {"chart": {"result": [{
    "timestamp": [1700000000, 1700086400, 1700172800],
    "indicators": {"quote": [{
        "open": [10, 11, None], "high": [12, 12, None], "low": [9, 10, None],
        "close": [11, 11.5, None], "volume": [100, 200, None]}]},
    "events": {"splits": {"1": {"date": 1700086400, "numerator": 2, "denominator": 1,
                                "splitRatio": "2:1"}},
               "dividends": {"1": {"date": 1700000000, "amount": 5.5}}},
}]}}


def test_parse_chart_skips_null_bars_and_reads_events():
    bars, acts = parse_chart_ohlcv(PAYLOAD, "X")
    assert len(bars) == 2 and bars[0].high == 12 and bars[1].close == 11.5
    assert {(a.type, a.value) for a in acts} == {("split", 2.0), ("dividend", 5.5)}
    assert parse_chart_ohlcv(None, "X") == ([], [])


def test_store_roundtrip_and_upsert():
    st = ReportDataStore(sqlite3.connect(":memory:"))
    bars, acts = parse_chart_ohlcv(PAYLOAD, "x")
    st.upsert_daily(bars)
    st.upsert_daily(bars)
    st.upsert_actions(acts)
    st.upsert_index("^NSEI", [("2024-01-01", 100.0)])
    assert len(st.get_daily("X")) == 2
    assert len(st.get_actions("X")) == 2
    assert st.get_index("^NSEI") == [("2024-01-01", 100.0)]
    assert st.last_fetched("X", "daily") is None
    st.mark_fetched("X", "daily")
    assert st.last_fetched("X", "daily") is not None


def test_synthetic_daily_is_deterministic_and_ends_at_cmp():
    p = SyntheticStockProvider()
    sym = "TCS"
    a, _ = p.fetch_daily_ohlcv(sym)
    b, _ = p.fetch_daily_ohlcv(sym)
    assert a == b and len(a) > 480
    assert a[-1].close == float(_STOCK_PROFILES[sym]["cmp"])
    assert all(x.low <= x.close <= x.high for x in a)
    idx = p.fetch_index_daily()
    assert len(idx) == len(a)
    assert any(p.fetch_daily_ohlcv(s)[1] for s in _STOCK_PROFILES)


def test_quarterly_parse_ttm_and_store():
    from wealth_advisor.data.stocks.report_data import (
        parse_timeseries,
        quarterly_from_timeseries,
        ttm,
    )

    def rec(t, vals):
        return {"meta": {"type": [t]}, t: [{"asOfDate": d, "reportedValue": {"raw": v}}
                                           for d, v in vals]}

    ds = [f"2025-{m:02d}-28" for m in (3, 6, 9, 12)]
    payload = {"timeseries": {"result": [
        rec("quarterlyTotalRevenue", [(d, 1e9) for d in ds]),
        rec("quarterlyNetIncome", [(d, 1e8) for d in ds[:3]] + [(ds[3], None)]),
    ]}}
    qs = quarterly_from_timeseries(parse_timeseries(payload), "X")
    assert len(qs) == 4 and qs[0].revenue_cr == 100.0
    assert ttm(qs, "revenue_cr") == (400.0, "TTM to 2025-12-28")
    assert ttm(qs, "pat_cr") == (None, None)  # one quarter missing -> never guess
    st = ReportDataStore(sqlite3.connect(":memory:"))
    st.upsert_quarterly(qs)
    assert [q.period_end for q in st.get_quarterly("x")] == ds


def test_synthetic_quarterly_has_eight_quarters():
    q = SyntheticStockProvider().fetch_quarterly("TCS")
    assert len(q) == 8 and q[0].period_end < q[-1].period_end
    assert all(x.period_type == "quarterly" for x in q)


def test_ensure_report_data_fetches_once_and_keeps_last_good():
    from datetime import UTC, datetime, timedelta

    from wealth_advisor.data.stocks.report_data import ensure_report_data

    class Counting(SyntheticStockProvider):
        calls = 0

        def fetch_daily_ohlcv(self, symbol, range_="2y"):
            Counting.calls += 1
            return super().fetch_daily_ohlcv(symbol, range_)

    st = ReportDataStore(sqlite3.connect(":memory:"))
    p = Counting()
    t0 = datetime(2026, 10, 9, tzinfo=UTC)
    assert ensure_report_data(st, p, "tcs", now=t0) == []
    ensure_report_data(st, p, "TCS", now=t0 + timedelta(hours=2))
    assert Counting.calls == 1  # fresh -> no refetch
    n = len(st.get_daily("TCS"))
    assert n > 480 and len(st.get_quarterly("TCS")) == 8 and st.get_index("^NSEI")

    class Broken(SyntheticStockProvider):
        def fetch_daily_ohlcv(self, symbol, range_="2y"):
            return [], []

    w = ensure_report_data(st, Broken(), "TCS", now=t0 + timedelta(days=3))
    assert any("daily prices unavailable" in x for x in w)
    assert len(st.get_daily("TCS")) == n  # last good data kept


def test_manual_inputs_roundtrip_requires_as_of():
    import pytest

    st = ReportDataStore(sqlite3.connect(":memory:"))
    st.set_manual("x", "order_book_cr", 5000, "2026-09-30", "http://x")
    assert st.get_manual("X")["order_book_cr"]["value"] == 5000
    with pytest.raises(ValueError):
        st.set_manual("x", "pledge_pct", 1, "")
