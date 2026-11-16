"""NSE equity universe loader and optional live NSE EQUITY_L.csv refresh."""

from __future__ import annotations

import csv
import io
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from .provider import StockMasterItem

BUNDLED_NSE_CSV = Path(__file__).parent / "nse_equity_list.csv"
NSE_EQUITY_L_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

_BANK_KEYWORDS = ("bank", "banking")


def infer_is_bank(sector: str | None, industry: str | None, name: str | None = None) -> bool:
    """Return True if sector, industry, or company name indicates a commercial bank."""
    combined = " ".join(
        part.lower() for part in (sector or "", industry or "", name or "") if part
    )
    if "non-banking" in combined or "nbfc" in combined:
        return False
    return any(kw in combined for kw in _BANK_KEYWORDS)


def parse_nse_csv_text(text: str, *, updated_at: str | None = None) -> list[StockMasterItem]:
    """Parse NSE EQUITY_L.csv (or bundled CSV with optional SECTOR/INDUSTRY/IS_BANK columns)."""
    ts = updated_at or datetime.now(UTC).isoformat()
    reader = csv.DictReader(io.StringIO(text))
    items: list[StockMasterItem] = []
    seen: set[str] = set()
    for raw_row in reader:
        row = {k.strip().upper(): (v.strip() if v else "") for k, v in raw_row.items() if k}
        symbol = row.get("SYMBOL", "").upper()
        series = row.get("SERIES", "EQ").upper()
        if not symbol or series != "EQ" or symbol in seen:
            continue
        seen.add(symbol)
        name = row.get("NAME OF COMPANY") or row.get("NAME") or symbol
        isin = row.get("ISIN NUMBER") or row.get("ISIN") or ""
        listing_date = row.get("DATE OF LISTING") or None
        sector = row.get("SECTOR") or "Unknown"
        industry = row.get("INDUSTRY") or "Unknown"
        is_bank_raw = row.get("IS_BANK", "")
        if is_bank_raw in ("1", "TRUE", "YES"):
            is_bank = True
        elif is_bank_raw in ("0", "FALSE", "NO") and sector != "Unknown":
            is_bank = infer_is_bank(sector, industry, name)
        else:
            is_bank = infer_is_bank(sector, industry, name)
        items.append(
            StockMasterItem(
                symbol=symbol,
                isin=isin,
                name=name,
                series=series,
                sector=sector,
                industry=industry,
                is_bank=is_bank,
                listing_date=listing_date,
                active=True,
                updated_at=ts,
            )
        )
    return items


def load_bundled_universe(csv_path: Path | None = None) -> list[StockMasterItem]:
    """Load the bundled NSE equity universe CSV."""
    path = csv_path or BUNDLED_NSE_CSV
    if not path.exists():
        return []
    return parse_nse_csv_text(path.read_text(encoding="utf-8"))


_INDEX_TAG_SYMBOLS: dict[str, set[str]] = {
    "NIFTY50": {
        "TCS",
        "INFY",
        "HCLTECH",
        "WIPRO",
        "HDFCBANK",
        "ICICIBANK",
        "KOTAKBANK",
        "SBIN",
        "AXISBANK",
        "HINDUNILVR",
        "ITC",
        "NESTLEIND",
        "MARUTI",
        "M&M",
        "TATAMOTORS",
        "SUNPHARMA",
        "DRREDDY",
        "CIPLA",
        "RELIANCE",
        "LT",
    },
    "NIFTY_BANK": {"HDFCBANK", "ICICIBANK", "KOTAKBANK", "SBIN", "AXISBANK"},
    "NIFTY_IT": {"TCS", "INFY", "HCLTECH", "WIPRO"},
}


def load_nse_universe(
    *,
    csv_path: Path | None = None,
    sector: str | None = None,
    index_tag: str | None = None,
) -> list[StockMasterItem]:
    """Load the bundled NSE universe with optional sector and index_tag filtering."""
    items = load_bundled_universe(csv_path=csv_path)
    if sector:
        items = [u for u in items if u.sector.lower() == sector.lower()]
    if index_tag:
        tag = index_tag.strip().upper().replace(" ", "_")
        allowed = _INDEX_TAG_SYMBOLS.get(tag)
        if allowed is not None:
            items = [u for u in items if u.symbol in allowed]
    return items


def list_nse_sectors(csv_path: Path | None = None) -> list[str]:
    """Return sorted unique sectors in the bundled NSE universe."""
    items = load_bundled_universe(csv_path=csv_path)
    return sorted({u.sector for u in items if u.sector and u.sector != "Unknown"})


def refresh_nse_universe(
    *,
    url: str = NSE_EQUITY_L_URL,
    save_to_path: Path | None = None,
    timeout: float = 15.0,
) -> list[StockMasterItem]:
    """Download the latest NSE EQUITY_L.csv, merge known sector/industry metadata, and return items."""
    bundled = {item.symbol: item for item in load_bundled_universe()}
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/csv,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw_text = resp.read().decode("utf-8", errors="replace")

    parsed = parse_nse_csv_text(raw_text)
    merged: list[StockMasterItem] = []
    for item in parsed:
        prev = bundled.get(item.symbol)
        if prev is not None:
            item = item.model_copy(
                update={
                    "sector": item.sector if item.sector != "Unknown" else prev.sector,
                    "industry": item.industry if item.industry != "Unknown" else prev.industry,
                    "is_bank": item.is_bank or prev.is_bank,
                }
            )
        merged.append(item)

    if save_to_path is not None and merged:
        save_to_path.parent.mkdir(parents=True, exist_ok=True)
        save_to_path.write_text(raw_text, encoding="utf-8")
    return merged
