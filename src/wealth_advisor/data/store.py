"""Mutual-fund metadata + NAV store (SQLite) with exact numeric filters."""

import csv
import json
import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from ..domain.categories import CATEGORIES, bucket_for, normalize_category
from ..models.enums import Bucket

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheme (
    code TEXT PRIMARY KEY, isin TEXT, name TEXT NOT NULL, amc TEXT, category TEXT NOT NULL,
    plan TEXT NOT NULL, option TEXT, bucket TEXT NOT NULL, expense_ratio REAL, aum_cr REAL,
    rating REAL, benchmark TEXT, launch_date TEXT, manager TEXT, source TEXT NOT NULL,
    as_of TEXT NOT NULL, manager_since TEXT, ytm REAL, modified_duration REAL, avg_maturity_years REAL
);
CREATE TABLE IF NOT EXISTS nav (
    code TEXT NOT NULL, date TEXT NOT NULL, nav REAL NOT NULL CHECK (nav > 0),
    PRIMARY KEY (code, date)
);
CREATE TABLE IF NOT EXISTS fund_stats (
    code TEXT PRIMARY KEY, as_of TEXT NOT NULL, n_points INTEGER, cagr_3y REAL, cagr_5y REAL,
    roll_3y_median REAL, volatility REAL, max_drawdown REAL, sharpe REAL, sortino REAL,
    pct_roll_3y REAL, pct_cagr_5y REAL, alpha REAL, beta REAL, pct_low_vol REAL
);
CREATE TABLE IF NOT EXISTS scheme_master (
    code TEXT PRIMARY KEY,
    isin_growth TEXT,
    isin_reinvest TEXT,
    name TEXT NOT NULL,
    amc TEXT,
    sebi_category TEXT,
    category TEXT NOT NULL,
    plan TEXT NOT NULL,
    option TEXT NOT NULL,
    structure TEXT NOT NULL,
    latest_nav REAL,
    nav_date TEXT,
    expense_ratio REAL,
    ter_as_of TEXT,
    aum_cr REAL,
    aum_as_of TEXT,
    source TEXT NOT NULL,
    as_of TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scheme_master_name ON scheme_master(name);
CREATE INDEX IF NOT EXISTS idx_scheme_master_category ON scheme_master(category);
CREATE TABLE IF NOT EXISTS fund_refresh_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    refreshed_at TEXT NOT NULL,
    data_mode TEXT NOT NULL,
    sources TEXT NOT NULL,
    status TEXT NOT NULL,
    nav_points_before INTEGER,
    nav_points_after INTEGER,
    nav_date_before TEXT,
    nav_date_after TEXT,
    latest_nav REAL,
    ter_before REAL,
    ter_after REAL,
    aum_before REAL,
    aum_after REAL,
    news_score REAL,
    sentiment_score REAL,
    errors TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fund_refresh_log_code ON fund_refresh_log(code, refreshed_at DESC);

CREATE TABLE IF NOT EXISTS stock_master (
    symbol TEXT PRIMARY KEY,
    isin TEXT,
    name TEXT NOT NULL,
    series TEXT DEFAULT 'EQ',
    exchange TEXT DEFAULT 'NSE',
    sector TEXT,
    industry TEXT,
    is_bank INTEGER DEFAULT 0,
    listing_date TEXT,
    active INTEGER DEFAULT 1,
    is_active INTEGER DEFAULT 1,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_stock_master_name ON stock_master(name);
CREATE INDEX IF NOT EXISTS idx_stock_master_sector ON stock_master(sector);

CREATE TABLE IF NOT EXISTS stock_snapshot (
    symbol TEXT PRIMARY KEY,
    as_of_date TEXT,
    price REAL,
    cmp REAL,
    market_cap_cr REAL,
    enterprise_value_cr REAL,
    pe REAL,
    pb REAL,
    ps REAL,
    ev_ebitda REAL,
    dividend_yield REAL,
    roe REAL,
    roa REAL,
    roce REAL,
    debt_to_equity REAL,
    current_ratio REAL,
    interest_coverage REAL,
    revenue_growth_yoy REAL,
    earnings_growth_yoy REAL,
    eps_ttm REAL,
    book_value_per_share REAL,
    shares_outstanding REAL,
    promoter_holding_pct REAL,
    pledge_pct REAL,
    auditor_flags INTEGER DEFAULT 0,
    insider_net_buy_lakhs REAL DEFAULT 0.0,
    avg_daily_traded_value_cr REAL,
    beta REAL,
    return_1y REAL,
    volatility_1y REAL,
    max_drawdown_1y REAL,
    sector TEXT,
    industry TEXT,
    currency TEXT DEFAULT 'INR',
    last_filing_date TEXT,
    fetched_at TEXT,
    source TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS stock_financials (
    symbol TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    revenue_cr REAL,
    ebitda_cr REAL,
    ebit_cr REAL,
    interest_cr REAL,
    pbt_cr REAL,
    tax_cr REAL,
    pat_cr REAL,
    eps REAL,
    dps REAL,
    bvps REAL,
    total_assets_cr REAL,
    net_worth_cr REAL,
    total_debt_cr REAL,
    current_assets_cr REAL,
    current_liabilities_cr REAL,
    inventory_cr REAL,
    receivables_cr REAL,
    payables_cr REAL,
    cash_and_equivalents_cr REAL,
    cfo_cr REAL,
    capex_cr REAL,
    fcf_cr REAL,
    dividends_paid_cr REAL,
    nii_cr REAL,
    nim_pct REAL,
    gnpa_pct REAL,
    nnpa_pct REAL,
    casa_pct REAL,
    car_pct REAL,
    credit_cost_pct REAL,
    provision_coverage_pct REAL,
    deposits_cr REAL,
    advances_cr REAL,
    as_of_date TEXT,
    source TEXT,
    PRIMARY KEY (symbol, fiscal_year)
);

CREATE TABLE IF NOT EXISTS stock_prices (
    symbol TEXT NOT NULL,
    price_date TEXT NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    PRIMARY KEY (symbol, price_date)
);

CREATE TABLE IF NOT EXISTS stock_sector_medians (
    sector TEXT PRIMARY KEY,
    is_bank INTEGER DEFAULT 0,
    peer_count INTEGER DEFAULT 0,
    median_pe REAL,
    median_pb REAL,
    median_ps REAL,
    median_ev_ebitda REAL,
    median_roe REAL,
    median_roce REAL,
    median_debt_to_equity REAL,
    median_nim_pct REAL,
    median_gnpa_pct REAL,
    median_nnpa_pct REAL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS stock_screen_result (
    symbol TEXT NOT NULL,
    rule_set TEXT NOT NULL,
    passed INTEGER NOT NULL,
    passed_count INTEGER DEFAULT 0,
    evaluated_count INTEGER DEFAULT 0,
    total_criteria INTEGER DEFAULT 0,
    pass_ratio REAL DEFAULT 0.0,
    composite_score REAL DEFAULT 0.0,
    sub_scores_json TEXT,
    criteria_json TEXT,
    red_flags_json TEXT,
    valuation_json TEXT,
    sync_run_id TEXT,
    evaluated_at TEXT,
    PRIMARY KEY (symbol, rule_set)
);

CREATE TABLE IF NOT EXISTS stock_sync_run (
    run_id TEXT PRIMARY KEY,
    run_date TEXT,
    mode TEXT NOT NULL,
    data_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    total_symbols INTEGER DEFAULT 0,
    processed_symbols INTEGER DEFAULT 0,
    succeeded_symbols INTEGER DEFAULT 0,
    failed_symbols INTEGER DEFAULT 0,
    skipped_symbols INTEGER DEFAULT 0,
    set1_pass_count INTEGER DEFAULT 0,
    set2_pass_count INTEGER DEFAULT 0,
    min_market_cap_cr REAL,
    min_traded_value_cr REAL,
    failed_details_json TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);
"""


class FundRecord(BaseModel):
    code: str
    isin: str = ""
    name: str
    amc: str = ""
    category: str
    plan: str = "direct"
    option: str = "growth"
    bucket: Bucket
    expense_ratio: float | None = None  # percent
    aum_cr: float | None = None
    rating: float | None = None
    benchmark: str = ""
    launch_date: date | None = None
    manager: str = ""
    source: str
    as_of: date
    manager_since: date | None = None
    ytm: float | None = None
    modified_duration: float | None = None
    avg_maturity_years: float | None = None


class SchemeMasterRecord(BaseModel):
    code: str
    isin_growth: str = ""
    isin_reinvest: str = ""
    name: str
    amc: str = ""
    sebi_category: str = ""
    category: str
    plan: str = "direct"          # "direct" | "regular"
    option: str = "growth"        # "growth" | "idcw"
    structure: str = "open"       # "open"
    latest_nav: float | None = None
    nav_date: date | None = None
    expense_ratio: float | None = None
    ter_as_of: date | None = None
    aum_cr: float | None = None
    aum_as_of: date | None = None
    source: str = "AMFI"
    as_of: date


class FundStats(BaseModel):
    """Return/risk figures in percent (volatility, drawdown included); sharpe/sortino are ratios."""

    code: str
    as_of: date
    n_points: int
    cagr_3y: float | None = None
    cagr_5y: float | None = None
    roll_3y_median: float | None = None
    volatility: float | None = None
    max_drawdown: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    pct_roll_3y: float | None = None  # category percentile 0-100
    pct_cagr_5y: float | None = None
    alpha: float | None = None
    beta: float | None = None
    pct_low_vol: float | None = None


class FundRefreshRecord(BaseModel):
    id: int | None = None
    code: str
    refreshed_at: str  # ISO 8601 UTC timestamp
    data_mode: str = "real"
    sources: list[str] = []
    status: Literal["ok", "partial", "failed"] = "ok"
    nav_points_before: int = 0
    nav_points_after: int = 0
    nav_date_before: date | None = None
    nav_date_after: date | None = None
    latest_nav: float | None = None
    ter_before: float | None = None
    ter_after: float | None = None
    aum_before: float | None = None
    aum_after: float | None = None
    news_score: float | None = None
    sentiment_score: float | None = None
    errors: list[str] = []


class MetadataStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._has_fts5 = False
        conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        existing_scheme = {row[1] for row in self.conn.execute("PRAGMA table_info(scheme)").fetchall()}
        for col, col_type in [
            ("manager_since", "TEXT"),
            ("ytm", "REAL"),
            ("modified_duration", "REAL"),
            ("avg_maturity_years", "REAL"),
        ]:
            if col not in existing_scheme:
                self.conn.execute(f"ALTER TABLE scheme ADD COLUMN {col} {col_type}")

        existing_stats = {row[1] for row in self.conn.execute("PRAGMA table_info(fund_stats)").fetchall()}
        for col, col_type in [
            ("alpha", "REAL"),
            ("beta", "REAL"),
            ("pct_low_vol", "REAL"),
        ]:
            if col not in existing_stats:
                self.conn.execute(f"ALTER TABLE fund_stats ADD COLUMN {col} {col_type}")

        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS scheme_master_fts "
                "USING fts5(code, name, amc)"
            )
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS stock_master_fts "
                "USING fts5(symbol, name, sector, industry, isin)"
            )
            self._has_fts5 = True
        except sqlite3.OperationalError:
            self._has_fts5 = False
        self.conn.commit()

    # ---- schemes -------------------------------------------------------
    def add_funds(self, funds: Sequence[FundRecord]) -> None:
        cols = list(FundRecord.model_fields)
        sql = (
            f"INSERT OR REPLACE INTO scheme ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})"
        )
        rows = []
        for f in funds:
            d = f.model_dump(mode="json")
            rows.append([d[c] for c in cols])
        self.conn.executemany(sql, rows)
        self.conn.commit()

    @staticmethod
    def _fund(row: sqlite3.Row) -> FundRecord:
        return FundRecord(**dict(row))

    @staticmethod
    def _master(row: sqlite3.Row) -> SchemeMasterRecord:
        d = dict(row)
        for k in ("isin_growth", "isin_reinvest", "amc", "sebi_category"):
            if d.get(k) is None:
                d[k] = ""
        return SchemeMasterRecord(**d)

    @staticmethod
    def _master_to_fund(row: sqlite3.Row) -> FundRecord | None:
        d = dict(row)
        raw_cat = d.get("category") or ""
        norm_cat = normalize_category(raw_cat) if raw_cat not in CATEGORIES else raw_cat
        bucket = bucket_for(norm_cat)
        if bucket is None:
            return None
        min_nav_s = d.get("min_nav_date")
        launch_dt = date.fromisoformat(min_nav_s) if min_nav_s else None
        as_of_s = d.get("nav_date") or d.get("as_of")
        as_of_dt = date.fromisoformat(as_of_s) if isinstance(as_of_s, str) else as_of_s
        plan = d.get("plan") or "direct"
        return FundRecord(
            code=d["code"],
            isin=d.get("isin_growth") or d.get("isin_reinvest") or "",
            name=d["name"],
            amc=d.get("amc") or "",
            category=norm_cat,
            plan=plan,
            option=d.get("option") or "growth",
            bucket=bucket,
            expense_ratio=d.get("expense_ratio"),
            aum_cr=d.get("aum_cr"),
            rating=4.0 if plan == "direct" else 3.0,
            benchmark=CATEGORIES[norm_cat].benchmark if norm_cat in CATEGORIES else "",
            launch_date=launch_dt,
            source=d.get("source") or "AMFI",
            as_of=as_of_dt,
        )

    def get_fund(self, code: str) -> FundRecord | None:
        row = self.conn.execute("SELECT * FROM scheme WHERE code = ?", (code,)).fetchone()
        return self._fund(row) if row else None

    def find_fund(self, identifier: str) -> FundRecord | None:
        """Resolve a fund by scheme code, ISIN, or case-insensitive name (checking scheme then scheme_master)."""
        key = identifier.strip()
        if not key:
            return None
        row = self.conn.execute(
            "SELECT * FROM scheme WHERE code = ? OR UPPER(COALESCE(isin, '')) = UPPER(?) "
            "OR LOWER(name) = LOWER(?)",
            (key, key, key),
        ).fetchone()
        if row:
            return self._fund(row)
        m_row = self.conn.execute(
            "SELECT m.*, (SELECT MIN(n.date) FROM nav n WHERE n.code = m.code) AS min_nav_date "
            "FROM scheme_master m "
            "WHERE m.code = ? OR UPPER(COALESCE(m.isin_growth, '')) = UPPER(?) "
            "OR LOWER(m.name) = LOWER(?)",
            (key, key, key),
        ).fetchone()
        return self._master_to_fund(m_row) if m_row else None


    # ---- scheme_master -------------------------------------------------
    def upsert_master(self, rows: Sequence[SchemeMasterRecord]) -> int:
        if not rows:
            return 0
        cols = list(SchemeMasterRecord.model_fields)
        preserve_if_null = {"expense_ratio", "ter_as_of", "aum_cr", "aum_as_of"}
        update_clauses = []
        for c in cols:
            if c == "code":
                continue
            if c in preserve_if_null:
                update_clauses.append(f"{c} = COALESCE(excluded.{c}, scheme_master.{c})")
            else:
                update_clauses.append(f"{c} = excluded.{c}")
        sql = (
            f"INSERT INTO scheme_master ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))}) "
            f"ON CONFLICT(code) DO UPDATE SET {', '.join(update_clauses)}"
        )
        payload = []
        fts_rows = []
        for r in rows:
            d = r.model_dump(mode="json")
            payload.append([d[c] for c in cols])
            fts_rows.append((r.code, r.name, r.amc))
        self.conn.executemany(sql, payload)
        if self._has_fts5:
            self.conn.executemany(
                "DELETE FROM scheme_master_fts WHERE code = ?", [(r.code,) for r in rows]
            )
            self.conn.executemany(
                "INSERT INTO scheme_master_fts (code, name, amc) VALUES (?, ?, ?)", fts_rows
            )
        self.conn.commit()
        return len(rows)

    def get_master(self, code: str) -> SchemeMasterRecord | None:
        row = self.conn.execute(
            "SELECT * FROM scheme_master WHERE code = ?", (code,)
        ).fetchone()
        return self._master(row) if row else None

    def search_master(
        self,
        q: str = "",
        *,
        category: str | None = None,
        plan: str | None = "direct",
        option: str | None = "growth",
        limit: int = 50,
    ) -> list[SchemeMasterRecord]:
        where: list[str] = []
        args: list = []
        if category and category.lower() != "all":
            where.append("m.category = ?")
            args.append(category)
        if plan and plan.lower() != "all":
            where.append("m.plan = ?")
            args.append(plan.lower())
        if option and option.lower() != "all":
            where.append("m.option = ?")
            args.append(option.lower())

        q_clean = q.strip()
        results: list[SchemeMasterRecord] = []
        seen_codes: set[str] = set()

        if q_clean and self._has_fts5:
            tokens = [
                "".join(ch for ch in tok if ch.isalnum())
                for tok in q_clean.split()
            ]
            tokens = [t for t in tokens if t]
            if tokens:
                fts_query = " AND ".join(f'"{t}"*' for t in tokens)
                fts_where = ["scheme_master_fts MATCH ?"] + list(where)
                fts_args = [fts_query, *args, limit]
                fts_sql = (
                    "SELECT m.* FROM scheme_master m "
                    "JOIN scheme_master_fts ON scheme_master_fts.code = m.code "
                    f"WHERE {' AND '.join(fts_where)} "
                    "ORDER BY m.name LIMIT ?"
                )
                try:
                    for r in self.conn.execute(fts_sql, fts_args).fetchall():
                        rec = self._master(r)
                        results.append(rec)
                        seen_codes.add(rec.code)
                except sqlite3.OperationalError:
                    pass

        remaining = limit - len(results)
        if remaining > 0:
            like_where = list(where)
            like_args = list(args)
            if q_clean:
                for tok in q_clean.split():
                    pat = f"%{tok.lower()}%"
                    like_where.append(
                        "(LOWER(m.name) LIKE ? OR LOWER(COALESCE(m.amc, '')) LIKE ? "
                        "OR LOWER(m.code) LIKE ? OR LOWER(COALESCE(m.isin_growth, '')) LIKE ?)"
                    )
                    like_args.extend([pat, pat, pat, pat])
            sql = "SELECT m.* FROM scheme_master m"
            if like_where:
                sql += " WHERE " + " AND ".join(like_where)
            sql += " ORDER BY m.name LIMIT ?"
            like_args.append(limit)
            for r in self.conn.execute(sql, like_args).fetchall():
                rec = self._master(r)
                if rec.code not in seen_codes:
                    results.append(rec)
                    seen_codes.add(rec.code)
                    if len(results) >= limit:
                        break
        return results

    def master_categories(self) -> list[dict[str, str | int]]:
        rows = self.conn.execute(
            "SELECT category, COUNT(*) AS count FROM scheme_master "
            "GROUP BY category ORDER BY category"
        ).fetchall()
        return [{"category": r["category"], "count": int(r["count"])} for r in rows]

    def query_funds(
        self,
        *,
        category: str | None = None,
        categories: Sequence[str] | None = None,
        bucket: Bucket | None = None,
        plan: str | None = None,
        max_expense: float | None = None,
        min_aum: float | None = None,
        min_rating: float | None = None,
        include_master: bool = True,
        include_unseeded_master: bool = False,
    ) -> list[FundRecord]:
        """Exact filters only; rows with NULL in a filtered column never match it.

        Includes both curated `scheme` rows and open-ended `scheme_master` rows that have
        metadata (expense_ratio, aum_cr) and NAV history / computed stats.
        """
        where: list[str] = []
        args: list = []
        if category is not None:
            where.append("category = ?")
            args.append(category)
        if categories is not None:
            if not categories:
                return []
            where.append(f"category IN ({','.join('?' * len(categories))})")
            args.extend(categories)
        if bucket is not None:
            where.append("bucket = ?")
            args.append(bucket.value)
        if plan is not None:
            where.append("plan = ?")
            args.append(plan)
        if max_expense is not None:
            where.append("expense_ratio <= ?")
            args.append(max_expense)
        if min_aum is not None:
            where.append("aum_cr >= ?")
            args.append(min_aum)
        if min_rating is not None:
            where.append("rating >= ?")
            args.append(min_rating)
        sql = "SELECT * FROM scheme" + (" WHERE " + " AND ".join(where) if where else "")
        rows = self.conn.execute(sql + " ORDER BY category, code", args).fetchall()
        out = [self._fund(r) for r in rows]

        if not include_master:
            return out

        m_where: list[str] = [
            "m.code NOT IN (SELECT code FROM scheme)",
            "m.option = 'growth'",
        ]
        m_args: list = []
        if plan is not None:
            m_where.append("m.plan = ?")
            m_args.append(plan)
        else:
            m_where.append("m.plan = 'direct'")
        if max_expense is not None:
            m_where.append("m.expense_ratio <= ?")
            m_args.append(max_expense)
        if min_aum is not None:
            m_where.append("m.aum_cr >= ?")
            m_args.append(min_aum)
        if not include_unseeded_master:
            m_where.append(
                "m.expense_ratio IS NOT NULL AND m.aum_cr IS NOT NULL AND ("
                "EXISTS (SELECT 1 FROM fund_stats fs WHERE fs.code = m.code) "
                "OR (SELECT COUNT(*) FROM nav n WHERE n.code = m.code) >= 30)"
            )

        m_sql = (
            "SELECT m.*, (SELECT MIN(n.date) FROM nav n WHERE n.code = m.code) AS min_nav_date "
            f"FROM scheme_master m WHERE {' AND '.join(m_where)} ORDER BY m.category, m.code"
        )
        cat_set = set(categories) if categories is not None else None
        for r in self.conn.execute(m_sql, m_args).fetchall():
            f = self._master_to_fund(r)
            if f is None:
                continue
            if category is not None and f.category != category:
                continue
            if cat_set is not None and f.category not in cat_set:
                continue
            if bucket is not None and f.bucket != bucket:
                continue
            if min_rating is not None and (f.rating is None or f.rating < min_rating):
                continue
            out.append(f)

        out.sort(key=lambda x: (x.category, x.code))
        return out

    def eligible_master_funds(
        self,
        *,
        categories: Sequence[str] | None = None,
        per_category_limit: int | None = None,
        only_unseeded: bool = False,
    ) -> list[FundRecord]:
        """Return Direct-Growth open-ended `scheme_master` funds across supported SEBI categories,
        prioritized per category by hygiene compliance (TER ceiling & AUM floor), highest AUM, and lowest TER.
        """
        from ..config import get_settings

        s = get_settings()
        m_where: list[str] = [
            "m.code NOT IN (SELECT code FROM scheme)",
            "m.plan = 'direct'",
            "m.option = 'growth'",
            "COALESCE(m.structure, 'open') = 'open'",
            "m.expense_ratio IS NOT NULL",
            "m.aum_cr IS NOT NULL",
        ]
        if only_unseeded:
            m_where.append("(SELECT COUNT(*) FROM nav n WHERE n.code = m.code) < 30")

        m_sql = (
            "SELECT m.*, (SELECT MIN(n.date) FROM nav n WHERE n.code = m.code) AS min_nav_date "
            f"FROM scheme_master m WHERE {' AND '.join(m_where)}"
        )
        cat_set = set(categories) if categories is not None else None
        by_cat: dict[str, list[FundRecord]] = {}
        for r in self.conn.execute(m_sql).fetchall():
            f = self._master_to_fund(r)
            if f is None:
                continue
            if cat_set is not None and f.category not in cat_set:
                continue
            by_cat.setdefault(f.category, []).append(f)

        out: list[FundRecord] = []
        for cat in sorted(by_cat.keys()):
            info = CATEGORIES.get(cat)
            max_ter = s.expense_ceilings.get(cat, s.default_expense_ceiling)
            min_aum = s.min_aum_cr_small if (info and info.small_aum_ok) else s.min_aum_cr

            def _rank_key(item: FundRecord, _max_ter: float = max_ter, _min_aum: float = min_aum) -> tuple:
                ter = item.expense_ratio if item.expense_ratio is not None else 99.0
                aum = item.aum_cr if item.aum_cr is not None else 0.0
                passes = 1 if (ter <= _max_ter and aum >= _min_aum) else 0
                return (-passes, -aum, ter, item.code)

            ranked = sorted(by_cat[cat], key=_rank_key)
            if per_category_limit is not None and per_category_limit > 0:
                ranked = ranked[:per_category_limit]
            out.extend(ranked)
        return out

    # ---- NAV -----------------------------------------------------------
    def save_nav(self, code: str, series: pd.Series) -> None:
        rows = [(code, d.strftime("%Y-%m-%d"), float(v)) for d, v in series.items()]
        self.conn.executemany("INSERT OR REPLACE INTO nav VALUES (?,?,?)", rows)
        self.conn.commit()

    def nav_series(self, code: str) -> pd.Series:
        rows = self.conn.execute(
            "SELECT date, nav FROM nav WHERE code = ? ORDER BY date", (code,)
        ).fetchall()
        idx = pd.DatetimeIndex([r["date"] for r in rows])
        return pd.Series([r["nav"] for r in rows], index=idx, dtype=float)

    def latest_nav_date(self) -> date | None:
        row = self.conn.execute("SELECT MAX(date) AS d FROM nav").fetchone()
        return date.fromisoformat(row["d"]) if row and row["d"] else None

    def latest_nav_as_of(self, code: str, as_of: date | None = None) -> tuple[date, float] | None:
        """Return (nav_date, nav) for the latest NAV on or before `as_of` (or overall latest)."""
        if as_of is not None:
            row = self.conn.execute(
                "SELECT date, nav FROM nav WHERE code = ? AND date <= ? ORDER BY date DESC LIMIT 1",
                (code, as_of.isoformat()),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT date, nav FROM nav WHERE code = ? ORDER BY date DESC LIMIT 1",
                (code,),
            ).fetchone()
        if row is None:
            return None
        return date.fromisoformat(row["date"]), float(row["nav"])


    # ---- stats ---------------------------------------------------------
    def save_stats(self, stats: Sequence[FundStats]) -> None:
        cols = list(FundStats.model_fields)
        sql = (
            f"INSERT OR REPLACE INTO fund_stats ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})"
        )
        self.conn.executemany(sql, [[s.model_dump(mode="json")[c] for c in cols] for s in stats])
        self.conn.commit()

    def get_stats(self, code: str) -> FundStats | None:
        row = self.conn.execute("SELECT * FROM fund_stats WHERE code = ?", (code,)).fetchone()
        return FundStats(**dict(row)) if row else None

    # ---- refresh log ---------------------------------------------------
    def log_refresh(self, record: FundRefreshRecord) -> FundRefreshRecord:
        d = record.model_dump(mode="json")
        cols = [c for c in FundRefreshRecord.model_fields if c != "id"]
        values = []
        for c in cols:
            if c in ("sources", "errors"):
                values.append(json.dumps(d[c] or []))
            else:
                values.append(d[c])
        sql = (
            f"INSERT INTO fund_refresh_log ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})"
        )
        cur = self.conn.execute(sql, values)
        self.conn.commit()
        return record.model_copy(update={"id": cur.lastrowid})

    def refresh_history(self, code: str, limit: int = 10) -> list[FundRefreshRecord]:
        rows = self.conn.execute(
            "SELECT * FROM fund_refresh_log WHERE code = ? ORDER BY refreshed_at DESC, id DESC LIMIT ?",
            (code, limit),
        ).fetchall()
        out: list[FundRefreshRecord] = []
        for r in rows:
            d = dict(r)
            for list_col in ("sources", "errors"):
                raw = d.get(list_col)
                if isinstance(raw, str):
                    try:
                        d[list_col] = json.loads(raw)
                    except json.JSONDecodeError:
                        d[list_col] = []
                elif raw is None:
                    d[list_col] = []
            out.append(FundRefreshRecord(**d))
        return out


def load_funds_csv(path: str | Path) -> tuple[list[FundRecord], list[str]]:
    """Read a hand-curated CSV; returns (valid records, row-level error messages).

    Required columns: code, name, category, source, as_of. Optional: isin, amc, plan, option,
    expense_ratio, aum_cr, rating, benchmark, launch_date, manager, manager_since, ytm,
    modified_duration, avg_maturity_years.
    """
    records: list[FundRecord] = []
    errors: list[str] = []
    with open(path, newline="") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            clean = {k: (v.strip() if v and v.strip() else None) for k, v in row.items() if k}
            try:
                bucket = bucket_for(clean.get("category") or "")
                if bucket is None:
                    raise ValueError(f"unknown category {clean.get('category')!r}")
                data = {k: v for k, v in clean.items() if v is not None and k != "bucket"}
                records.append(FundRecord(bucket=bucket, **data))
            except Exception as exc:  # noqa: BLE001 - report every bad row, keep going
                errors.append(f"row {n}: {exc}")
    return records, errors
