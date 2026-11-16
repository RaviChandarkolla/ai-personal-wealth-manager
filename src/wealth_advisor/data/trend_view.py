"""Saved web research view: which sectors and stocks are in the news and expected to do well.

The view is built from Google News headlines plus one LLM call. Every sector and stock must cite
headlines we actually fetched, and every stock symbol must exist on NSE, so the LLM cannot invent
either. Selection from the view never looks at prices.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ..config import Settings, get_settings
from ..llm.base import LLMClient
from .stocks.universe import load_bundled_universe, parse_nse_csv_text

log = logging.getLogger(__name__)

PROMPT_VERSION = "trend-view.v1"

OUTLOOK_SCORE = {"positive": 75.0, "neutral": 50.0, "negative": 25.0}
SEARCH_QUERIES = (
    "India stock market sectors outlook next 12 months",
    "NSE sectors expected to outperform brokerages",
    "India stocks in focus rally earnings growth",
    "top stock picks India brokerages sector tailwinds",
    "India sector rotation best performing sectors",
    "India government policy boost sector stocks",
    "India manufacturing defence railways power capex stocks outlook",
    "India banking IT pharma consumption sector outlook stocks",
)


class TrendSource(BaseModel):
    title: str
    url: str = ""
    publisher: str = ""
    published_at: str = ""


class TrendSector(BaseModel):
    name: str
    outlook: Literal["positive", "neutral", "negative"]
    score: float
    rationale: str
    sources: list[TrendSource]


class TrendStock(BaseModel):
    symbol: str
    name: str
    sector: str
    score: float
    thesis: str
    sources: list[TrendSource]


class TrendView(BaseModel):
    as_of: date
    generated_by: str
    sectors: list[TrendSector]
    stocks: list[TrendStock]
    notes: str = ""


class Headline(BaseModel):
    id: str
    title: str
    url: str = ""
    publisher: str = ""
    published_at: str = ""


class DraftSector(BaseModel):
    name: str
    outlook: Literal["positive", "neutral", "negative"]
    score: float
    rationale: str
    headline_ids: list[str]


class DraftStock(BaseModel):
    symbol: str
    sector: str
    conviction: float
    thesis: str
    headline_ids: list[str]


class TrendDraft(BaseModel):
    sectors: list[DraftSector]
    stocks: list[DraftStock]


def load_trend_view(path: Path | None = None) -> TrendView | None:
    p = Path(path or get_settings().trend_view_path)
    if not p.exists():
        return None
    try:
        return TrendView.model_validate_json(p.read_text())
    except ValueError as exc:
        log.warning("Ignoring unreadable trend view %s: %s", p, exc)
        return None


def save_trend_view(view: TrendView, path: Path | None = None) -> None:
    p = Path(path or get_settings().trend_view_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(view.model_dump_json(indent=2))


def trend_view_status(view: TrendView | None, s: Settings | None = None, today: date | None = None) -> dict:
    s = s or get_settings()
    if view is None:
        return {"available": False, "stale": True, "as_of": None, "age_days": None,
                "sector_count": 0, "stock_count": 0}
    age = ((today or date.today()) - view.as_of).days
    return {
        "available": True,
        "as_of": view.as_of.isoformat(),
        "age_days": age,
        "stale": age > s.trend_view_stale_days,
        "stale_after_days": s.trend_view_stale_days,
        "sector_count": len(view.sectors),
        "stock_count": len(view.stocks),
        "generated_by": view.generated_by,
    }


def known_nse_symbols(s: Settings | None = None) -> dict[str, tuple[str, str]]:
    """symbol -> (company name, sector or 'Unknown') from the bundled list and the full NSE list."""
    s = s or get_settings()
    out: dict[str, tuple[str, str]] = {}
    full = Path(s.nse_full_universe_path)
    if full.exists():
        for u in parse_nse_csv_text(full.read_text(encoding="utf-8", errors="replace")):
            out[u.symbol] = (u.name, u.sector)
    for u in load_bundled_universe():
        out[u.symbol] = (u.name, u.sector)
    return out


def _fetch_one(query: str, limit: int, timeout: float) -> list[dict]:
    url = (
        "https://news.google.com/rss/search?q="
        f"{urllib.parse.quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; WealthAdvisorBot/1.0)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        root = ET.fromstring(resp.read())
    channel = root.find("channel")
    items: list[dict] = []
    for el in (channel.findall("item") if channel is not None else [])[:limit]:
        raw = (el.findtext("title") or "").strip()
        src = el.find("source")
        publisher = src.text.strip() if src is not None and src.text else ""
        title = re.sub(r"\s+-\s+[^-]+$", "", raw).strip() or raw
        pub = ""
        if el.findtext("pubDate"):
            try:
                pub = parsedate_to_datetime(el.findtext("pubDate") or "").date().isoformat()
            except (TypeError, ValueError):
                pub = ""
        if title:
            items.append({"title": title, "url": (el.findtext("link") or "").strip(),
                          "publisher": publisher, "published_at": pub})
    return items


def fetch_headlines(
    queries: tuple[str, ...] = SEARCH_QUERIES, per_query: int = 8, timeout: float = 10.0
) -> list[Headline]:
    """Fetch and de-duplicate headlines; a failing query is skipped."""
    seen: set[str] = set()
    out: list[Headline] = []
    for q in queries:
        try:
            items = _fetch_one(q, per_query, timeout)
        except Exception as exc:  # noqa: BLE001 - network/parse errors only skip that query
            log.warning("Trend view: query %r failed: %s", q, exc)
            continue
        for it in items:
            key = it["title"].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(Headline(id=f"H{len(out) + 1}", **it))
    return out


def _sources(ids: list[str], by_id: dict[str, Headline]) -> list[TrendSource]:
    seen: set[str] = set()
    out: list[TrendSource] = []
    for i in ids:
        h = by_id.get(i)
        if h and h.id not in seen:
            seen.add(h.id)
            out.append(TrendSource(title=h.title, url=h.url, publisher=h.publisher,
                                   published_at=h.published_at))
    return out


def validate_draft(
    draft: TrendDraft, headlines: list[Headline], symbols: dict[str, tuple[str, str]],
    *, as_of: date, generated_by: str,
) -> TrendView:
    """Keep only sectors/stocks that cite real headlines; stocks must be real NSE symbols."""
    by_id = {h.id: h for h in headlines}
    sectors: dict[str, TrendSector] = {}
    for d in draft.sectors:
        src = _sources(d.headline_ids, by_id)
        if not src or not d.name.strip():
            continue
        score = max(0.0, min(100.0, d.score)) if d.score else OUTLOOK_SCORE[d.outlook]
        sectors[d.name.strip().lower()] = TrendSector(
            name=d.name.strip(), outlook=d.outlook, score=round(score, 1),
            rationale=d.rationale.strip(), sources=src,
        )
    stocks: dict[str, TrendStock] = {}
    for d in draft.stocks:
        sym = d.symbol.strip().upper()
        src = _sources(d.headline_ids, by_id)
        if sym not in symbols or not src or sym in stocks:
            continue
        name, known_sector = symbols[sym]
        sector_name = known_sector if known_sector != "Unknown" else d.sector.strip()
        sec = sectors.get(d.sector.strip().lower()) or sectors.get(sector_name.lower())
        sector_score = sec.score if sec else OUTLOOK_SCORE["neutral"]
        conviction = max(0.0, min(100.0, d.conviction))
        if len(src) < 2:
            conviction = min(conviction, 70.0)  # a single headline cannot give top conviction
        stocks[sym] = TrendStock(
            symbol=sym, name=name, sector=sec.name if sec else sector_name,
            score=round(0.6 * sector_score + 0.4 * conviction, 1),
            thesis=d.thesis.strip(), sources=src,
        )
    ranked = sorted(stocks.values(), key=lambda x: (-x.score, x.symbol))
    return TrendView(
        as_of=as_of, generated_by=generated_by,
        sectors=sorted(sectors.values(), key=lambda x: -x.score), stocks=ranked,
        notes=f"{len(headlines)} headlines read; {len(ranked)} stocks passed validation.",
    )


def refresh_trend_view(
    llm: LLMClient,
    s: Settings | None = None,
    *,
    fetch: Callable[[], list[Headline]] = fetch_headlines,
    save: bool = True,
    today: date | None = None,
) -> TrendView:
    """Rebuild the saved view from fresh headlines. Raises ValueError if too little validates."""
    s = s or get_settings()
    headlines = fetch()
    if len(headlines) < 10:
        raise ValueError(f"Only {len(headlines)} headlines could be fetched; keeping the previous view.")
    table = "\n".join(
        f"[{h.id}] {h.title} ({h.publisher}, {h.published_at})" for h in headlines
    )
    messages = [
        {"role": "system", "content": (
            "You are an Indian equity research assistant. Using ONLY the numbered news headlines "
            "given, identify (1) sectors that look set to perform well in the next 12 months "
            "(outlook positive/neutral/negative, score 0-100) and (2) NSE-listed stocks named or "
            "clearly implied by those headlines that are likely beneficiaries. Do not use or quote "
            "share prices or past returns. Every sector and stock must cite the supporting "
            "headline ids (e.g. H3) in headline_ids. Use NSE trading symbols (e.g. TCS, "
            "HDFCBANK). conviction is 0-100. Give 5-12 sectors and 15-40 stocks."
        )},
        {"role": "user", "content": f"Headlines:\n{table}"},
    ]
    from ..llm.langchain_client import trace_context

    with trace_context(step="trend_view", metadata={"prompt_version": PROMPT_VERSION}):
        draft = llm.complete(messages, TrendDraft)
    view = validate_draft(
        draft, headlines, known_nse_symbols(s),
        as_of=today or date.today(), generated_by="app: Google News RSS + LLM",
    )
    if len(view.stocks) < 3:
        raise ValueError("Web research produced too few validated stocks; keeping the previous view.")
    if save:
        save_trend_view(view, s.trend_view_path)
    return view
