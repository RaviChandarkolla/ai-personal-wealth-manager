"""AMFI NAVAll.txt ingestion with row validation. The loader is idempotent (upserts by code+date)."""

import re
import urllib.request
from datetime import date, datetime

import pandas as pd
from pydantic import BaseModel

from ..domain.categories import CATEGORIES
from .store import MetadataStore, SchemeMasterRecord

NAV_ALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
MAX_GAP_DAYS = 7

_SEBI_CATEGORY_ALIASES: dict[str, str] = {
    "large cap": "Large Cap",
    "large & mid cap": "Large & Mid Cap",
    "large and mid cap": "Large & Mid Cap",
    "flexi cap": "Flexi Cap",
    "mid cap": "Mid Cap",
    "small cap": "Small Cap",
    "value": "Value / Contra",
    "contra": "Value / Contra",
    "value / contra": "Value / Contra",
    "elss": "ELSS",
    "elss- tax saver": "ELSS",
    "elss - tax saver": "ELSS",
    "tax saver": "ELSS",
    "focused": "Focused",
    "index": "Index Fund",
    "index funds": "Index Fund",
    "index fund": "Index Fund",
    "conservative hybrid": "Conservative Hybrid",
    "balanced advantage": "Balanced Advantage",
    "dynamic asset allocation": "Balanced Advantage",
    "dynamic asset allocation or balanced advantage": "Balanced Advantage",
    "aggressive hybrid": "Aggressive Hybrid",
    "multi asset": "Multi Asset",
    "multi asset allocation": "Multi Asset",
    "arbitrage": "Arbitrage",
    "equity savings": "Equity Savings",
    "overnight": "Overnight",
    "liquid": "Liquid",
    "ultra short duration": "Ultra Short Duration",
    "low duration": "Low Duration",
    "money market": "Money Market",
    "short duration": "Short Duration",
    "short term": "Short Duration",
    "corporate bond": "Corporate Bond",
    "banking & psu": "Banking & PSU",
    "banking and psu": "Banking & PSU",
    "banking and psu debt": "Banking & PSU",
    "banking & psu debt": "Banking & PSU",
    "gilt": "Gilt",
    "gilt with 10 year constant duration": "Gilt",
    "gold etf": "Gold ETF",
    "gold etf / fof": "Gold ETF",
}

# Known real market (Direct TER %, AUM in Rs. Cr) for popular AMFI scheme_master funds
# not always present in the 60-row curated CSV.
KNOWN_SCHEME_TER_AUM: dict[str, tuple[float, float]] = {
    "147946": (0.41, 9480.0),    # BANDHAN Small Cap Fund - Direct Plan - Growth
    "147944": (1.73, 9480.0),    # BANDHAN Small Cap Fund - Regular Plan - Growth
    "120828": (0.64, 26500.0),   # Quant Small Cap Fund - Direct Plan - Growth
    "120847": (0.51, 17200.0),   # Kotak Small Cap Fund - Direct Plan - Growth
    "146130": (0.45, 12100.0),   # Canara Robeco Small Cap Fund - Direct Plan - Growth
    "145206": (0.35, 9200.0),    # Tata Small Cap Fund - Direct Plan - Growth
    "119212": (0.72, 15800.0),   # DSP Small Cap Fund - Direct Plan - Growth
    "118989": (0.88, 14500.0),   # Franklin India Smaller Companies Fund - Direct - Growth
    "120594": (0.66, 16900.0),   # HSBC Small Cap Fund - Direct Growth
    "145137": (0.42, 5800.0),    # Invesco India Smallcap Fund - Direct Plan - Growth
    "119598": (0.62, 58400.0),   # SBI BlueChip Fund - Direct Plan - Growth
    "118955": (0.82, 46200.0),   # HDFC Flexi Cap Fund - Direct Plan - Growth
    "120586": (0.78, 72500.0),   # ICICI Prudential Bluechip Fund - Direct Plan - Growth
    "118632": (0.74, 38900.0),   # Nippon India Large Cap Fund - Direct Plan - Growth
    "118759": (0.78, 36800.0),   # Nippon India Growth Fund - Direct Plan - Growth
    "120841": (0.42, 48200.0),   # Kotak Emerging Equity Scheme - Direct Plan - Growth
    "119062": (0.68, 41200.0),   # HDFC Balanced Advantage Fund - Direct Plan - Growth
    "120377": (0.58, 34600.0),   # ICICI Prudential Equity & Debt Fund - Direct Plan - Growth
}

# Category-median (Direct TER %, Regular TER %, Baseline AUM Cr) fallback when AMFI NAVAll.txt
# is ingested without an external TER/AUM portal export.
CATEGORY_DEFAULT_TER_AUM: dict[str, tuple[float, float, float]] = {
    "Large Cap": (0.62, 1.65, 8500.0),
    "Large & Mid Cap": (0.65, 1.75, 6500.0),
    "Flexi Cap": (0.68, 1.72, 9500.0),
    "Mid Cap": (0.65, 1.75, 7500.0),
    "Small Cap": (0.64, 1.78, 5200.0),
    "Value / Contra": (0.72, 1.80, 6000.0),
    "ELSS": (0.66, 1.75, 6800.0),
    "Focused": (0.68, 1.78, 5500.0),
    "Index Fund": (0.20, 0.65, 4500.0),
    "Index": (0.20, 0.65, 4500.0),
    "Aggressive Hybrid": (0.72, 1.80, 6200.0),
    "Balanced Advantage": (0.68, 1.75, 8000.0),
    "Multi Asset": (0.65, 1.70, 5000.0),
    "Conservative Hybrid": (0.55, 1.35, 2800.0),
    "Equity Savings": (0.55, 1.35, 2500.0),
    "Arbitrage": (0.38, 0.95, 5500.0),
    "Liquid": (0.18, 0.32, 12000.0),
    "Overnight": (0.10, 0.20, 8000.0),
    "Ultra Short Duration": (0.32, 0.75, 4500.0),
    "Low Duration": (0.35, 0.80, 4500.0),
    "Money Market": (0.22, 0.45, 6500.0),
    "Short Duration": (0.36, 0.85, 5200.0),
    "Corporate Bond": (0.34, 0.78, 6800.0),
    "Banking & PSU": (0.34, 0.75, 5800.0),
    "Gilt": (0.44, 0.95, 3500.0),
    "Gold ETF": (0.50, 0.80, 3200.0),
    "Gold ETF / FoF": (0.50, 0.80, 3200.0),
}


def default_ter_aum_for_master(code: str, category: str, plan: str = "direct") -> tuple[float, float]:
    """Return (expense_ratio, aum_cr) for a scheme_master record using known data or category medians."""
    if code in KNOWN_SCHEME_TER_AUM:
        return KNOWN_SCHEME_TER_AUM[code]
    dir_ter, reg_ter, base_aum = CATEGORY_DEFAULT_TER_AUM.get(category, (0.65, 1.65, 3500.0))
    ter = dir_ter if plan.lower() == "direct" else reg_ter
    return ter, base_aum


class AmfiRow(BaseModel):
    code: str
    isin_payout: str
    isin_reinvest: str
    name: str
    nav: float
    date: date
    amc: str = ""
    section: str = ""
    plan: str = ""
    option: str = ""


class LoadReport(BaseModel):
    rows_parsed: int = 0
    rows_loaded: int = 0
    rejected: list[str] = []
    isin_mismatch: list[str] = []
    gaps: list[str] = []


def fetch_nav_all(url: str = NAV_ALL_URL, timeout: int = 30) -> str:
    if not url.startswith("https://"):
        raise ValueError("only https URLs are allowed")
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_nav_all(text: str) -> tuple[list[AmfiRow], list[str]]:
    """Return (valid rows, rejection messages). Supports 6-col and 8-col AMFI NAVAll.txt formats."""
    rows: list[AmfiRow] = []
    rejected: list[str] = []
    section = amc = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("scheme code"):
            continue
        if ";" not in line:
            if line.startswith(("Open Ended", "Close Ended", "Interval")):
                section = line
            else:
                amc = line
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) >= 8:
            code, isin1, isin2, raw_name, plan_col, option_col, nav_s, date_s = parts[:8]
            suffix_parts = [
                p for p in (plan_col, option_col)
                if p and p.lower() not in raw_name.lower()
            ]
            name = " - ".join([raw_name, *suffix_parts]) if suffix_parts else raw_name
        elif len(parts) >= 6:
            code, isin1, isin2, name, nav_s, date_s = parts[:6]
            plan_col = option_col = ""
        else:
            rejected.append(f"malformed: {line[:60]}")
            continue
        try:
            nav = float(nav_s)
            when = datetime.strptime(date_s, "%d-%b-%Y").date()
        except ValueError:
            rejected.append(f"{code}: bad nav/date ({nav_s!r}, {date_s!r})")
            continue
        if nav <= 0:
            rejected.append(f"{code}: non-positive NAV {nav}")
            continue
        rows.append(
            AmfiRow(
                code=code, isin_payout=isin1, isin_reinvest=isin2, name=name, nav=nav,
                date=when, amc=amc, section=section, plan=plan_col, option=option_col,
            )
        )
    return rows, rejected


def _parse_sebi_category(section: str) -> tuple[str, str]:
    """Extract (sebi_category, mapped_category) from an AMFI section header."""
    m = re.search(r"\((.*)\)", section)
    inner = m.group(1).strip() if m else section.strip()
    if "-" in inner:
        raw = inner.split("-", 1)[1].strip()
    else:
        raw = inner
    cleaned = re.sub(r"\s+Funds?$", "", raw, flags=re.IGNORECASE).strip()
    sebi_cat = cleaned or raw or "Other"
    key = sebi_cat.lower()
    if key in _SEBI_CATEGORY_ALIASES:
        return sebi_cat, _SEBI_CATEGORY_ALIASES[key]
    for cat_name in CATEGORIES:
        if cat_name.lower() == key:
            return sebi_cat, cat_name
    return sebi_cat, sebi_cat


def build_scheme_master(rows: list[AmfiRow]) -> list[SchemeMasterRecord]:
    """Convert open-ended AmfiRows into SchemeMasterRecord entries."""
    out: dict[str, SchemeMasterRecord] = {}
    for r in rows:
        if not r.section.startswith("Open Ended"):
            continue
        sebi_cat, category = _parse_sebi_category(r.section)
        plan_probe = f"{r.plan} {r.name}".lower()
        option_probe = f"{r.option} {r.name}".lower()
        plan = "direct" if "direct" in plan_probe else "regular"
        option = (
            "idcw"
            if any(k in option_probe for k in ("idcw", "dividend", "payout", "reinvestment"))
            else "growth"
        )
        isin_g = "" if r.isin_payout in ("-", "") else r.isin_payout
        isin_r = "" if r.isin_reinvest in ("-", "") else r.isin_reinvest
        ter_val, aum_val = default_ter_aum_for_master(r.code, category, plan)
        out[r.code] = SchemeMasterRecord(
            code=r.code,
            isin_growth=isin_g,
            isin_reinvest=isin_r,
            name=r.name,
            amc=r.amc,
            sebi_category=sebi_cat,
            category=category,
            plan=plan,
            option=option,
            structure="open",
            latest_nav=r.nav,
            nav_date=r.date,
            expense_ratio=ter_val,
            ter_as_of=r.date,
            aum_cr=aum_val,
            aum_as_of=r.date,
            source="AMFI",
            as_of=r.date,
        )
    return list(out.values())


def enrich_master_metadata(store: MetadataStore) -> int:
    """Propagate curated scheme TER/AUM and known/category defaults into scheme_master."""
    # 1. Copy exact TER & AUM from curated `scheme` rows into matching `scheme_master` rows
    store.conn.execute(
        """
        UPDATE scheme_master
        SET expense_ratio = COALESCE(
                (SELECT s.expense_ratio FROM scheme s WHERE s.code = scheme_master.code),
                scheme_master.expense_ratio
            ),
            ter_as_of = COALESCE(
                scheme_master.ter_as_of,
                (SELECT s.as_of FROM scheme s WHERE s.code = scheme_master.code),
                scheme_master.as_of
            ),
            aum_cr = COALESCE(
                (SELECT s.aum_cr FROM scheme s WHERE s.code = scheme_master.code),
                scheme_master.aum_cr
            ),
            aum_as_of = COALESCE(
                scheme_master.aum_as_of,
                (SELECT s.as_of FROM scheme s WHERE s.code = scheme_master.code),
                scheme_master.as_of
            )
        WHERE EXISTS (SELECT 1 FROM scheme s WHERE s.code = scheme_master.code)
        """
    )
    # 2. Fill any remaining NULL expense_ratio or aum_cr in scheme_master
    missing_rows = store.conn.execute(
        "SELECT code, category, plan, as_of, expense_ratio, aum_cr "
        "FROM scheme_master WHERE expense_ratio IS NULL OR aum_cr IS NULL"
    ).fetchall()
    updated = 0
    for r in missing_rows:
        ter_def, aum_def = default_ter_aum_for_master(r["code"], r["category"], r["plan"])
        ter_val = r["expense_ratio"] if r["expense_ratio"] is not None else ter_def
        aum_val = r["aum_cr"] if r["aum_cr"] is not None else aum_def
        store.conn.execute(
            "UPDATE scheme_master SET expense_ratio = ?, ter_as_of = COALESCE(ter_as_of, ?), "
            "aum_cr = ?, aum_as_of = COALESCE(aum_as_of, ?) WHERE code = ?",
            (ter_val, r["as_of"], aum_val, r["as_of"], r["code"]),
        )
        updated += 1
    store.conn.commit()
    return updated


def sync_scheme_master(store: MetadataStore, text: str) -> tuple[int, list[str]]:
    """Parse NAVAll.txt text, build open-ended scheme_master rows, and upsert into store."""
    rows, rejected = parse_nav_all(text)
    master_rows = build_scheme_master(rows)
    count = store.upsert_master(master_rows)
    enrich_master_metadata(store)
    return count, rejected


def find_gaps(series: pd.Series, max_gap_days: int = MAX_GAP_DAYS) -> list[tuple[date, date]]:
    """Consecutive NAV dates more than `max_gap_days` apart."""
    idx = series.index
    return [
        (idx[i - 1].date(), idx[i].date())
        for i in range(1, len(idx))
        if (idx[i] - idx[i - 1]).days > max_gap_days
    ]


def load_nav_all(store: MetadataStore, text: str) -> LoadReport:
    """Store NAVs for schemes already in the curated metadata; flag ISIN mismatches and gaps."""
    rows, rejected = parse_nav_all(text)
    report = LoadReport(rows_parsed=len(rows) + len(rejected), rejected=rejected)
    touched: set[str] = set()
    for r in rows:
        fund = store.get_fund(r.code)
        if fund is None:
            continue
        if fund.isin and fund.isin not in (r.isin_payout, r.isin_reinvest):
            report.isin_mismatch.append(f"{r.code}: curated {fund.isin} vs AMFI {r.isin_payout}")
            continue
        store.conn.execute(
            "INSERT OR REPLACE INTO nav VALUES (?,?,?)", (r.code, r.date.isoformat(), r.nav)
        )
        report.rows_loaded += 1
        touched.add(r.code)
    store.conn.commit()
    for code in sorted(touched):
        for a, b in find_gaps(store.nav_series(code)):
            report.gaps.append(f"{code}: no NAV between {a} and {b}")
    return report
