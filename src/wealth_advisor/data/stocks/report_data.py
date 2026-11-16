"""Storage and parsing for 360° report data: daily OHLCV, corporate actions, index series.

Kept separate from the daily sync: only the 360° job calls these (plan step 9).
"""

from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from .provider import CorporateAction, StockFinancialYear, StockOHLCV

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_prices_daily (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL NOT NULL, volume REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS stock_corporate_actions (
    symbol TEXT NOT NULL, date TEXT NOT NULL, type TEXT NOT NULL,
    value REAL, detail TEXT, source TEXT,
    PRIMARY KEY (symbol, date, type)
);
CREATE TABLE IF NOT EXISTS index_prices_daily (
    index_symbol TEXT NOT NULL, date TEXT NOT NULL, close REAL NOT NULL,
    PRIMARY KEY (index_symbol, date)
);
CREATE TABLE IF NOT EXISTS stock_quarterly (
    symbol TEXT NOT NULL, period_end TEXT NOT NULL, row_json TEXT NOT NULL,
    PRIMARY KEY (symbol, period_end)
);
CREATE TABLE IF NOT EXISTS stock_manual_inputs (
    symbol TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL,
    as_of TEXT NOT NULL, source_url TEXT DEFAULT '', entered_at TEXT NOT NULL,
    PRIMARY KEY (symbol, field)
);
CREATE TABLE IF NOT EXISTS stock_report_data_log (
    symbol TEXT NOT NULL, kind TEXT NOT NULL, fetched_at TEXT NOT NULL,
    PRIMARY KEY (symbol, kind)
);
"""


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def parse_chart_ohlcv(
    payload: dict[str, Any] | None, symbol: str
) -> tuple[list[StockOHLCV], list[CorporateAction]]:
    """Parse a Yahoo v8 chart payload (interval=1d, events=div,split)."""
    if not payload:
        return [], []
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        return [], []
    res = results[0] or {}
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    o, h, lo, c, v = (q.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
    bars: list[StockOHLCV] = []
    for i, t in enumerate(ts):
        if t is None or i >= len(c):
            continue
        close = _num(c[i])
        op = _num(o[i]) if i < len(o) else None
        hi = _num(h[i]) if i < len(h) else None
        low = _num(lo[i]) if i < len(lo) else None
        if close is None or close <= 0:
            continue
        op, hi, low = op or close, hi or close, low or close
        bars.append(
            StockOHLCV(
                symbol=symbol,
                date=datetime.fromtimestamp(int(t), tz=UTC).date().isoformat(),
                open=op, high=max(hi, close), low=min(low, close), close=close,
                volume=_num(v[i]) if i < len(v) else None,
            )
        )
    actions: list[CorporateAction] = []
    events = res.get("events") or {}
    for e in (events.get("splits") or {}).values():
        num, den = _num(e.get("numerator")), _num(e.get("denominator"))
        if not num or not den:
            continue
        d = datetime.fromtimestamp(int(e["date"]), tz=UTC).date().isoformat()
        actions.append(CorporateAction(symbol=symbol, date=d, type="split", value=num / den,
                                       detail=str(e.get("splitRatio") or f"{num:g}:{den:g}")))
    for e in (events.get("dividends") or {}).values():
        amt = _num(e.get("amount"))
        if amt is None:
            continue
        d = datetime.fromtimestamp(int(e["date"]), tz=UTC).date().isoformat()
        actions.append(CorporateAction(symbol=symbol, date=d, type="dividend", value=amt))
    return bars, sorted(actions, key=lambda a: a.date)


QUARTERLY_TYPES = (
    "quarterlyTotalRevenue", "quarterlyOperatingRevenue", "quarterlyEBITDA",
    "quarterlyNormalizedEBITDA", "quarterlyNetIncome", "quarterlyNetIncomeCommonStockholders",
    "quarterlyPretaxIncome", "quarterlyOtherIncomeExpense", "quarterlyDilutedEPS",
    "quarterlyBasicEPS", "quarterlyInterestExpense", "quarterlyReconciledDepreciation",
)


def parse_timeseries(payload: dict[str, Any] | None) -> dict[str, dict[str, float]]:
    """Yahoo fundamentals-timeseries payload -> {asOfDate: {type: value}}."""
    out: dict[str, dict[str, float]] = {}
    for item in ((payload or {}).get("timeseries") or {}).get("result") or []:
        types_list = (item.get("meta") or {}).get("type") or []
        if not types_list:
            continue
        key = str(types_list[0])
        for rec in item.get(key) or []:
            if not isinstance(rec, dict):
                continue
            as_of = str(rec.get("asOfDate") or "").strip()
            val = _num((rec.get("reportedValue") or {}).get("raw"))
            if as_of and val is not None:
                out.setdefault(as_of, {})[key] = val
    return out


def _cr(v: float | None) -> float | None:
    return None if v is None else round(v / 1e7, 2)


def _first(*vals: float | None) -> float | None:
    return next((v for v in vals if v is not None), None)


def quarterly_from_timeseries(
    by_date: dict[str, dict[str, float]], symbol: str, keep: int = 8
) -> list[StockFinancialYear]:
    """Latest `keep` quarters as StockFinancialYear(period_type='quarterly'), ₹ Cr."""
    rows: list[StockFinancialYear] = []
    for d in sorted(by_date)[-keep:]:
        r = by_date[d]
        ebitda = _first(_cr(r.get("quarterlyEBITDA")), _cr(r.get("quarterlyNormalizedEBITDA")))
        dep = _cr(r.get("quarterlyReconciledDepreciation"))
        rows.append(StockFinancialYear(
            symbol=symbol, fiscal_year=int(d[:4]), period_end=d, period_type="quarterly",
            revenue_cr=_first(_cr(r.get("quarterlyTotalRevenue")),
                              _cr(r.get("quarterlyOperatingRevenue"))),
            other_income_cr=_cr(r.get("quarterlyOtherIncomeExpense")),
            ebitda_cr=ebitda, depreciation_cr=dep,
            ebit_cr=round(ebitda - dep, 2) if ebitda is not None and dep is not None else None,
            interest_cr=_cr(r.get("quarterlyInterestExpense")),
            pbt_cr=_cr(r.get("quarterlyPretaxIncome")),
            pat_cr=_first(_cr(r.get("quarterlyNetIncome")),
                          _cr(r.get("quarterlyNetIncomeCommonStockholders"))),
            eps=_first(r.get("quarterlyDilutedEPS"), r.get("quarterlyBasicEPS")),
            as_of_date=d, source="yahoo_timeseries",
        ))
    return rows


def ttm(quarters: list[StockFinancialYear], field: str) -> tuple[float | None, str | None]:
    """Sum of the last 4 quarters of a flow item; (None, None) unless 4 values exist."""
    last4 = sorted(quarters, key=lambda q: q.period_end)[-4:]
    vals = [getattr(q, field) for q in last4]
    if len(last4) < 4 or any(v is None for v in vals):
        return None, None
    return round(sum(vals), 2), f"TTM to {last4[-1].period_end}"


class ReportDataStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        conn.executescript(_SCHEMA)

    def upsert_daily(self, bars: list[StockOHLCV]) -> None:
        self.conn.executemany(
            "INSERT INTO stock_prices_daily(symbol,date,open,high,low,close,volume) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol,date) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, "
            "close=excluded.close, volume=excluded.volume",
            [(b.symbol.upper(), b.date, b.open, b.high, b.low, b.close, b.volume) for b in bars],
        )
        self.conn.commit()

    def get_daily(self, symbol: str) -> list[StockOHLCV]:
        rows = self.conn.execute(
            "SELECT * FROM stock_prices_daily WHERE symbol=? ORDER BY date", (symbol.upper(),)
        ).fetchall()
        return [StockOHLCV(symbol=r[0], date=r[1], open=r[2], high=r[3], low=r[4],
                           close=r[5], volume=r[6]) for r in rows]

    def upsert_actions(self, actions: list[CorporateAction]) -> None:
        self.conn.executemany(
            "INSERT INTO stock_corporate_actions(symbol,date,type,value,detail,source) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(symbol,date,type) DO UPDATE SET "
            "value=excluded.value, detail=excluded.detail, source=excluded.source",
            [(a.symbol.upper(), a.date, a.type, a.value, a.detail, a.source) for a in actions],
        )
        self.conn.commit()

    def get_actions(self, symbol: str) -> list[CorporateAction]:
        rows = self.conn.execute(
            "SELECT symbol,date,type,value,detail,source FROM stock_corporate_actions "
            "WHERE symbol=? ORDER BY date", (symbol.upper(),)
        ).fetchall()
        return [CorporateAction(symbol=r[0], date=r[1], type=r[2], value=r[3] or 0.0,
                                detail=r[4] or "", source=r[5] or "") for r in rows]

    def upsert_index(self, index_symbol: str, series: list[tuple[str, float]]) -> None:
        self.conn.executemany(
            "INSERT INTO index_prices_daily(index_symbol,date,close) VALUES (?,?,?) "
            "ON CONFLICT(index_symbol,date) DO UPDATE SET close=excluded.close",
            [(index_symbol, d, c) for d, c in series],
        )
        self.conn.commit()

    def get_index(self, index_symbol: str) -> list[tuple[str, float]]:
        rows = self.conn.execute(
            "SELECT date, close FROM index_prices_daily WHERE index_symbol=? ORDER BY date",
            (index_symbol,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def upsert_quarterly(self, rows: list[StockFinancialYear]) -> None:
        self.conn.executemany(
            "INSERT INTO stock_quarterly(symbol,period_end,row_json) VALUES (?,?,?) "
            "ON CONFLICT(symbol,period_end) DO UPDATE SET row_json=excluded.row_json",
            [(r.symbol.upper(), r.period_end, r.model_dump_json()) for r in rows],
        )
        self.conn.commit()

    def get_quarterly(self, symbol: str) -> list[StockFinancialYear]:
        rows = self.conn.execute(
            "SELECT row_json FROM stock_quarterly WHERE symbol=? ORDER BY period_end",
            (symbol.upper(),),
        ).fetchall()
        return [StockFinancialYear.model_validate_json(r[0]) for r in rows]

    def set_manual(self, symbol: str, field: str, value: Any, as_of: str, source_url: str = "") -> None:
        import json

        if not as_of:
            raise ValueError("manual input requires as_of")
        self.conn.execute(
            "INSERT INTO stock_manual_inputs(symbol,field,value,as_of,source_url,entered_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(symbol,field) DO UPDATE SET value=excluded.value, "
            "as_of=excluded.as_of, source_url=excluded.source_url, entered_at=excluded.entered_at",
            (symbol.upper(), field, json.dumps(value), as_of, source_url,
             datetime.now(UTC).isoformat()),
        )
        self.conn.commit()

    def get_manual(self, symbol: str) -> dict[str, dict[str, Any]]:
        import json

        rows = self.conn.execute(
            "SELECT field,value,as_of,source_url FROM stock_manual_inputs WHERE symbol=?",
            (symbol.upper(),),
        ).fetchall()
        return {r[0]: {"value": json.loads(r[1]), "as_of": r[2], "source_url": r[3]} for r in rows}

    def mark_fetched(self, symbol: str, kind: str, at: datetime | None = None) -> None:
        at = at or datetime.now(UTC)
        self.conn.execute(
            "INSERT INTO stock_report_data_log(symbol,kind,fetched_at) VALUES (?,?,?) "
            "ON CONFLICT(symbol,kind) DO UPDATE SET fetched_at=excluded.fetched_at",
            (symbol.upper(), kind, at.isoformat()),
        )
        self.conn.commit()

    def last_fetched(self, symbol: str, kind: str) -> datetime | None:
        r = self.conn.execute(
            "SELECT fetched_at FROM stock_report_data_log WHERE symbol=? AND kind=?",
            (symbol.upper(), kind),
        ).fetchone()
        return datetime.fromisoformat(r[0]) if r else None


def ensure_report_data(
    store: ReportDataStore,
    provider: Any,
    symbol: str,
    *,
    now: datetime | None = None,
    quarterly_ttl_days: int = 7,
    index_symbol: str = "^NSEI",
) -> list[str]:
    """Refresh daily prices (>1 day old), quarterly data (> ttl) and the index series.

    Keeps the last good data when a fetch returns nothing. Returns warnings. Called only by
    the 360° job, never by the daily sync.
    """
    now = now or datetime.now(UTC)
    sym = symbol.upper()
    warnings: list[str] = []

    def stale(kind: str, ttl: timedelta, key: str = sym) -> bool:
        last = store.last_fetched(key, kind)
        return last is None or now - last >= ttl

    if hasattr(provider, "fetch_daily_ohlcv") and stale("daily", timedelta(days=1)):
        bars, actions = provider.fetch_daily_ohlcv(sym)
        if bars:
            store.upsert_daily(bars)
            store.upsert_actions(actions)
            store.mark_fetched(sym, "daily", now)
        else:
            warnings.append(f"{sym}: daily prices unavailable; using last stored data")
    if hasattr(provider, "fetch_quarterly") and stale("quarterly", timedelta(days=quarterly_ttl_days)):
        qs = provider.fetch_quarterly(sym)
        if qs:
            store.upsert_quarterly(qs)
            store.mark_fetched(sym, "quarterly", now)
        else:
            warnings.append(f"{sym}: quarterly results unavailable")
    if hasattr(provider, "fetch_index_daily") and stale("daily", timedelta(days=1), index_symbol):
        series = provider.fetch_index_daily(index_symbol)
        if series:
            store.upsert_index(index_symbol, series)
            store.mark_fetched(index_symbol, "daily", now)
        else:
            warnings.append(f"{index_symbol}: index series unavailable")
    if not store.get_daily(sym):
        warnings.append(f"{sym}: no daily price history; technicals will be unavailable")
    return warnings
