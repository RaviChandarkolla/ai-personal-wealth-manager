"""Web research layer for the 360° report (plan Phase C).

Every numeric fact must carry a source_url that is one of the citations actually returned by the
search AND an as_of date; anything else is dropped. Web text is untrusted: evidence containing
prompt-injection phrases is discarded and the rest is only ever passed on as quoted evidence.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

PREFERRED_DOMAINS = ("nseindia.com", "bseindia.com", "screener.in", "moneycontrol.com", "trendlyne.com")
_INJECTION = re.compile(
    r"(ignore\s+(all\s+|any\s+)?(previous|prior|above|system)\s+(instructions?|prompts?|rules?)"
    r"|disregard\s+(all\s+|any\s+)?(previous|prior|above|system)"
    r"|reveal\s+(your\s+|the\s+)?system\s+prompt"
    r"|you\s+are\s+now\s+(in\s+)?(developer|jailbreak|unrestricted|dan)\b)",
    re.IGNORECASE,
)


def is_injection(text: str) -> bool:
    return bool(_INJECTION.search(text or ""))


class WebEvidence(BaseModel):
    url: str
    title: str = ""
    publisher: str = ""
    published_at: date | None = None
    snippet: str = ""
    fetched_at: datetime | None = None
    query: str = ""
    topic: str = ""


class RawWebFact(BaseModel):
    name: str
    value: float | str | None = None
    unit: str = ""
    as_of: str = ""
    source_url: str = ""


class WebFact(BaseModel):
    topic: str
    name: str
    value: float | str
    unit: str = ""
    as_of: date
    source_url: str
    publisher: str = ""


class SearchResult(BaseModel):
    evidence: list[WebEvidence] = Field(default_factory=list)
    facts: list[RawWebFact] = Field(default_factory=list)


@dataclass(frozen=True)
class Topic:
    id: str
    query: str  # {name} {symbol} placeholders
    fields: str  # what numeric facts to return
    numeric: bool = True


TOPICS: tuple[Topic, ...] = (
    Topic("price", "{name} {symbol} share price 52 week high low market cap NSE",
          "cmp, high_52w, low_52w, market_cap_cr"),
    Topic("results", "{name} {symbol} latest quarterly results TTM revenue net profit",
          "quarter_end, revenue_cr, pat_cr, ebitda_cr for the latest declared quarter"),
    Topic("shareholding", "{name} {symbol} shareholding pattern promoter FII DII public pledge latest quarter",
          "promoter_pct, fii_pct, dii_pct, public_pct, pledge_pct"),
    Topic("concall", "{name} {symbol} investor presentation earnings call order book guidance",
          "order_book_cr, guidance (text), book_to_bill"),
    Topic("analyst", "{name} {symbol} analyst consensus target price buy hold sell ratings",
          "buy_count, hold_count, sell_count, target_price_mean, consensus_eps_fy1, consensus_eps_fy2"),
    Topic("corporate_action", "{name} {symbol} demerger split bonus merger restructuring", "", False),
    Topic("news", "{name} {symbol} stock news last 60 days", "", False),
    Topic("governance", "{name} {symbol} auditor resignation SEBI order pledge related party transactions", "", False),
    Topic("industry", "{name} {symbol} industry market size CAGR outlook", "industry_cagr_pct, tam_cr"),
    Topic("competitors", "{name} {symbol} listed competitors peers market share", "", False),
    Topic("index", "{name} {symbol} Nifty 50 Sensex F&O index inclusion", "", False),
    Topic("deals", "{name} {symbol} bulk block deals institutional", "", False),
)

_SYSTEM = (
    "You are a research assistant for an Indian equity report. Use web search. Return ONLY JSON: "
    '{"facts":[{"name":str,"value":number|string,"unit":str,"as_of":"YYYY-MM-DD","source_url":str}]}. '
    "Every fact needs the exact page URL you read and the date the figure is as of. "
    "Omit anything you cannot source. Never use memory for numbers."
)


def _parse_facts(text: str) -> list[RawWebFact]:
    m = re.search(r"\{.*\}", text or "", flags=re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return [RawWebFact.model_validate(f) for f in data.get("facts", []) if isinstance(f, dict)]
    except (ValueError, TypeError):
        return []


class WebResearcher(Protocol):
    name: str

    def search(self, topic: Topic, symbol: str, company: str) -> SearchResult: ...


class NullWebResearcher:
    """Synthetic mode and tests: nothing found, so web items show 'Not available'."""

    name = "none"

    def search(self, topic: Topic, symbol: str, company: str) -> SearchResult:
        return SearchResult()


class RssWebResearcher:
    """Google News RSS: headlines only, never numbers."""

    name = "rss"

    def __init__(self, limit: int = 8, timeout: float = 10.0) -> None:
        self.limit, self.timeout = limit, timeout

    def search(self, topic: Topic, symbol: str, company: str) -> SearchResult:
        from .trend_view import _fetch_one

        q = topic.query.format(name=company, symbol=symbol)
        try:
            items = _fetch_one(q, self.limit, self.timeout)
        except Exception as exc:  # noqa: BLE001
            log.warning("RSS search failed for %s/%s: %s", symbol, topic.id, exc)
            return SearchResult()
        now = datetime.now(UTC)
        ev = []
        for it in items:
            try:
                pub = date.fromisoformat(it["published_at"]) if it.get("published_at") else None
            except ValueError:
                pub = None
            ev.append(WebEvidence(url=it["url"], title=it["title"], publisher=it.get("publisher", ""),
                                  published_at=pub, snippet=it["title"], fetched_at=now, query=q,
                                  topic=topic.id))
        return SearchResult(evidence=ev)


def _prompt(topic: Topic, symbol: str, company: str) -> list[dict[str, str]]:
    q = topic.query.format(name=company, symbol=symbol)
    want = f" Return facts: {topic.fields}." if topic.numeric and topic.fields else " Return facts: [] (no numbers needed)."
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": f"Search: {q}.{want}"}]


class OpenAIWebResearcher:
    """OpenAI Responses API with the built-in web_search tool (via LangChain)."""

    name = "openai"

    def __init__(self, model: str, chat: Any | None = None) -> None:
        if chat is None:
            from langchain_openai import ChatOpenAI

            chat = ChatOpenAI(model=model, use_responses_api=True).bind_tools([{"type": "web_search_preview"}])
        self._chat = chat

    def search(self, topic: Topic, symbol: str, company: str) -> SearchResult:
        msgs = [(m["role"], m["content"]) for m in _prompt(topic, symbol, company)]
        try:
            resp = self._chat.invoke(msgs)
        except Exception as exc:  # noqa: BLE001
            log.warning("OpenAI web search failed for %s/%s: %s", symbol, topic.id, exc)
            return SearchResult()
        return parse_openai_response(resp.content, topic, _prompt(topic, symbol, company)[1]["content"])


def parse_openai_response(content: Any, topic: Topic, query: str) -> SearchResult:
    blocks = content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
    text, seen, ev = "", set(), []
    now = datetime.now(UTC)
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") != "text":
            continue
        text += b.get("text", "")
        for a in b.get("annotations") or []:
            url = a.get("url")
            if a.get("type") == "url_citation" and url and url not in seen:
                seen.add(url)
                ev.append(WebEvidence(url=url, title=a.get("title", ""), publisher=urlparse(url).netloc,
                                      snippet="", fetched_at=now, query=query, topic=topic.id))
    return SearchResult(evidence=ev, facts=_parse_facts(text))


class AnthropicWebResearcher:
    """Anthropic server-side web_search tool (via LangChain)."""

    name = "anthropic"

    def __init__(self, model: str, max_uses: int = 3, chat: Any | None = None) -> None:
        if chat is None:
            from langchain_anthropic import ChatAnthropic

            chat = ChatAnthropic(model=model).bind_tools(
                [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}]
            )
        self._chat = chat

    def search(self, topic: Topic, symbol: str, company: str) -> SearchResult:
        msgs = [(m["role"], m["content"]) for m in _prompt(topic, symbol, company)]
        try:
            resp = self._chat.invoke(msgs)
        except Exception as exc:  # noqa: BLE001
            log.warning("Anthropic web search failed for %s/%s: %s", symbol, topic.id, exc)
            return SearchResult()
        return parse_anthropic_response(resp.content, topic, _prompt(topic, symbol, company)[1]["content"])


def parse_anthropic_response(content: Any, topic: Topic, query: str) -> SearchResult:
    blocks = content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
    text, ev, seen = "", [], set()
    now = datetime.now(UTC)

    def add(url: str, title: str = "", snippet: str = "", age: str = "") -> None:
        if not url or url in seen:
            return
        seen.add(url)
        pub = None
        try:
            pub = date.fromisoformat(age[:10]) if age else None
        except ValueError:
            pub = None
        ev.append(WebEvidence(url=url, title=title, publisher=urlparse(url).netloc, published_at=pub,
                              snippet=snippet[:300], fetched_at=now, query=query, topic=topic.id))

    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            text += b.get("text", "")
            for c in b.get("citations") or []:
                add(c.get("url", ""), c.get("title", ""), c.get("cited_text", ""))
        elif b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            for r in b["content"]:
                if isinstance(r, dict):
                    add(r.get("url", ""), r.get("title", ""), "", r.get("page_age") or "")
    return SearchResult(evidence=ev, facts=_parse_facts(text))


def get_researcher(provider: str, llm_provider: str, model: str, data_mode: str) -> WebResearcher:
    """auto follows llm_provider; synthetic data mode never touches the web."""
    if data_mode == "synthetic" or provider == "none":
        return NullWebResearcher()
    p = llm_provider if provider == "auto" else provider
    try:
        if p == "openai":
            return OpenAIWebResearcher(model)
        if p == "anthropic":
            return AnthropicWebResearcher(model)
    except Exception as exc:  # noqa: BLE001
        log.warning("web search tool unavailable (%s); falling back to RSS", exc)
    return RssWebResearcher()


# ---------------- acceptance, ranking, cross-check ----------------

def rank_evidence(ev: list[WebEvidence]) -> list[WebEvidence]:
    def key(e: WebEvidence) -> int:
        host = urlparse(e.url).netloc.lower()
        return 0 if any(host.endswith(d) for d in PREFERRED_DOMAINS) else 1

    return sorted(ev, key=key)


def clean_evidence(ev: list[WebEvidence]) -> list[WebEvidence]:
    """Drop anything that looks like a prompt injection; rank preferred domains first."""
    return rank_evidence([e for e in ev if not is_injection(f"{e.title} {e.snippet}")])


def accept_facts(topic: str, raw: list[RawWebFact], evidence: list[WebEvidence]) -> tuple[list[WebFact], list[str]]:
    """Keep a fact only if value present, as_of parses, and source_url is a returned citation."""
    urls = {e.url: e for e in evidence}
    ok: list[WebFact] = []
    dropped: list[str] = []
    for f in raw:
        if f.value is None or f.value == "":
            continue
        if is_injection(f"{f.name} {f.value}"):
            dropped.append(f"{topic}:{f.name} (injection)")
            continue
        try:
            as_of = date.fromisoformat(f.as_of[:10])
        except ValueError:
            dropped.append(f"{topic}:{f.name} (no as_of)")
            continue
        if f.source_url not in urls:
            dropped.append(f"{topic}:{f.name} (source_url not among citations)")
            continue
        ok.append(WebFact(topic=topic, name=f.name, value=f.value, unit=f.unit, as_of=as_of,
                          source_url=f.source_url, publisher=urls[f.source_url].publisher))
    return ok, dropped


def cross_check_cmp(yahoo_cmp: float | None, web_cmp: float | None, tol_pct: float = 3.0) -> str | None:
    """Warning text when web and Yahoo CMP differ by more than tol; Yahoo value is always kept."""
    if not yahoo_cmp or not web_cmp or yahoo_cmp <= 0:
        return None
    diff = abs(web_cmp / yahoo_cmp - 1) * 100
    if diff > tol_pct:
        return f"Web CMP {web_cmp:g} differs from Yahoo CMP {yahoo_cmp:g} by {diff:.1f}% (> {tol_pct:g}%); Yahoo value kept"
    return None


# ---------------- cache ----------------

class WebEvidenceCache:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS stock_web_evidence (symbol TEXT NOT NULL, topic TEXT NOT NULL, "
            "query TEXT, json TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY (symbol, topic))"
        )

    def get(self, symbol: str, topic: str, ttl_hours: int, now: datetime | None = None) -> SearchResult | None:
        now = now or datetime.now(UTC)
        r = self.conn.execute("SELECT json, fetched_at FROM stock_web_evidence WHERE symbol=? AND topic=?",
                              (symbol.upper(), topic)).fetchone()
        if not r or now - datetime.fromisoformat(r[1]) > timedelta(hours=ttl_hours):
            return None
        return SearchResult.model_validate_json(r[0])

    def put(self, symbol: str, topic: str, query: str, res: SearchResult, now: datetime | None = None) -> None:
        self.conn.execute(
            "INSERT INTO stock_web_evidence(symbol,topic,query,json,fetched_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(symbol,topic) DO UPDATE SET query=excluded.query, json=excluded.json, "
            "fetched_at=excluded.fetched_at",
            (symbol.upper(), topic, query, res.model_dump_json(), (now or datetime.now(UTC)).isoformat()),
        )
        self.conn.commit()


# ---------------- orchestration ----------------

class WebResearchResult(BaseModel):
    evidence: dict[str, list[WebEvidence]] = Field(default_factory=dict)
    facts: list[WebFact] = Field(default_factory=list)
    searches: int = 0
    warnings: list[str] = Field(default_factory=list)


def research(
    researcher: WebResearcher, symbol: str, company: str, *, max_searches: int = 14,
    cache: WebEvidenceCache | None = None, ttl_hours: int = 24,
    topics: tuple[Topic, ...] = TOPICS, yahoo_cmp: float | None = None,
) -> WebResearchResult:
    out = WebResearchResult()
    if researcher.name == "none":
        return out
    for t in topics:
        res = cache.get(symbol, t.id, ttl_hours) if cache else None
        if res is None:
            if out.searches >= max_searches:
                out.warnings.append(f"web search budget ({max_searches}) reached; skipped topic '{t.id}'")
                continue
            res = researcher.search(t, symbol, company)
            out.searches += 1
            res = SearchResult(evidence=clean_evidence(res.evidence), facts=res.facts)
            if cache:
                cache.put(symbol, t.id, t.query, res)
        out.evidence[t.id] = res.evidence
        facts, dropped = accept_facts(t.id, res.facts, res.evidence)
        out.facts.extend(facts)
        out.warnings.extend(f"dropped web fact {d}" for d in dropped)
    web_cmp = next((f.value for f in out.facts if f.topic == "price" and f.name == "cmp"
                    and isinstance(f.value, int | float)), None)
    w = cross_check_cmp(yahoo_cmp, float(web_cmp) if web_cmp is not None else None)
    if w:
        out.warnings.append(w)
    return out
