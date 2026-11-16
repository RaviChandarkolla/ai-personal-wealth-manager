# Progress Checkpoint: 360° Stock Analysis for Every Recommended Stock

**Timestamp:** 2026-11-15 21:41 IST (updated; first checkpoint 17:26 IST)
**Task:** Do a thorough analysis and write a plan so that every stock the app recommends (Advise, web picks, others) comes with the full "Master Prompt 360° Analysis".
**Plan:** `docs/plans/stock-360-analysis-plan.md` (**written 2026-11-15 21:48 IST**, Phases 0 and A–I, items 1–65)
**Status:** Plan complete; waiting for user review. No code changed.

---

## 1. Source template

- PDF: `docs/plans/THE MASTER PROMPT (Full 360° Analysis) - v2 by Gemini 4 Argon - Evernote.pdf`
  - The read tool can't open the PDF directly. Text was pulled out with a PDFKit Swift script into `/tmp/master_prompt.txt` (798 lines). Run it with `CLANG_MODULE_CACHE_PATH=/tmp/swiftcache swift -module-cache-path /tmp/swiftcache /tmp/pdf2txt.swift <pdf>`.
- The same content is already in Markdown at **`docs/plans/360-analysis-template.md`** (552 lines). Treat that file as the template.
- **Template structure:**
  - **Persona:** Buffett, Munger, Lynch, Graham, Fisher, Jhunjhunwala (RJ), Livermore/Weinstein.
  - **Rules:**
    - No numbers from training memory. Every number must be live and dated.
    - Check for demergers and other corporate actions.
    - Adapt to the sector: peak/mid/trough for cyclicals; NIM/GNPA/CASA/PCR/P-ABV for BFSI.
    - Technicals must be verified, or marked "Requires Live Chart Verification".
    - Be brutally honest.
  - **Step 0:** a data freshness audit table.
  - **Sections:**
    1. Market data
    2. Fundamentals and forensics (P&L, balance sheet, cash flow, returns, order book)
    3. Valuation versus own history, sector and peers
    4. Intrinsic value: reverse DCF, DCF, Lynch PEG, Graham Number
    5. Price surge versus fundamentals (return decomposition)
    6. Moat
    7. Management and governance
    8. Growth and FY27–29 projections
    9. Risk
    10. Technicals and Weinstein stage
    11. Sentiment and flows
    12. Buffett buy checklist and entry price
    13. Lynch category, PEG and entry prices
    14. Targets: 1–9 months (trading) and 1/2/3/5 years (investing), plus the trade plan
    15. Verdict and suitability
    16. Institutional market cycle phase
    17. Seven legends' sell analysis, consensus sell score, smart exit (STCG 20% / LTCG 12.5%)
  - **Output:** in 4 parts (Part 1 = Step 0 + sections 1–2; Part 2 = sections 3–7; Part 3 = sections 8–11 and 16; Part 4 = sections 12, 13, 17, 14, 15).

## 2. Where stocks reach the user today (all need the 360° report)

| Path | Code | Notes |
|---|---|---|
| Advise, "Include stocks" (screened) | `pipeline.py` ~L340-368 → `agents/stock_researcher.py` `StockResearcherAgent.run()` | Reads only the synced SQLite cache. Ranks by score, at most 2 per sector, up to `max_candidates`. Builds `DossierEntry` + `AnalystVerdict` with category `"Direct Equity"`. Stock entries are appended to `entries/verdicts` at `pipeline.py` ~L398. |
| Advise with AI-notes spec (`stock_count` / `trendy_stock_count`) | `pipeline.py` ~L314-339 → `agents/web_stock_picker.py` `select_stocks()` | Web trend picks come from `data/trend_view.json` (Google News RSS + LLM, confidence 0.7, **not fundamentally screened**). The rest are filled from the screened list. |
| Decision / sizing | `agents/decision.py` L160 (drops Direct Equity when `include_stocks` is off), L223-260 (splits equity bucket into stock sleeve vs MFs, `size_stock_sleeve`) | Hook point for the optional "drop AVOID/SELL" setting. |
| Advise response rows | `api/main.py` ~L566-580 | Stock rows carry `instrument_type="stock"`, `symbol`, `market_cap_cr`, `checks_passed`. `AdviseResponse` is at L178. |
| Analyse Stock | `POST /api/stocks/analyze` (L2398), `GET /api/stocks/analyze/{symbol}` (L2463) | `build_single_stock_analysis()` in `domain/stock_context.py` L349 (8 sections). `StockNarratorAgent.narrate_single_stock()` writes a 4-paragraph memo. Synchronous. Uses `BudgetedLLM(base_llm, s.max_llm_calls_per_run)`, `trace_context(run_id)`, `flush_langfuse()` and `AuditLog.append(rid, "stock_analyzer", "analyze", ...)`. |
| Compare Stocks | `POST /api/stocks/compare` (L2493) | `domain/stock_compare.py` + `narrate_comparison()` |
| Follow-up chat / monitoring | `agents/followup.py`, `agents/monitoring.py` | **Confirmed: no stock-specific logic**; both are fund-centric. Out of scope for v1. |
| Frontend Advise table | `frontend/src/App.tsx` ~L1531-1604 | `isStock` row: name button → `openAnalyzeStock(code)` (L511, `/analyze-stock?symbol=`), "STOCK" badge, `Checks: x/y`, `valuation_status` badge. **"360° Report" button goes here** (next to the badges). |
| Frontend views | `App.tsx` L170 `AppView = 'advisor' \| 'compare' \| 'explore' \| 'compare-stocks' \| 'analyze-stock'`; `switchView()` L192 | `StockAnalyzer.tsx` (794 lines) has collapsible sections (`showEicSection`, `showOverridesSection`), so a "360° Report" tab fits there. |

Other useful API helpers: `_validate_stock_symbol()` (main.py L2139), `StockOverridesInput` (L2160: FII %, pledge %, bank metrics) — extend it for 360° manual overrides.

## 3. Data available versus what the template needs

**Available today:**
- `data/stocks/provider.py`:
  - `StockSnapshot`: price, market cap, EV, P/E, P/B, P/S, EV/EBITDA, ROE/ROA/ROCE, D/E, current ratio, interest coverage, dividend yield, EPS TTM, BVPS, promoter %, pledge %, beta, 1-year return/volatility/drawdown.
  - `StockFinancialYear`: annual P&L, balance sheet and cash flow, plus bank metrics; there is a `period_type` field for quarterly data but it isn't filled.
  - `StockPricePoint`: price with volume.
  - `StockSectorMedian`
- `yfinance_provider.py`: raw HTTP to Yahoo chart API (`range=5y&interval=1wk` → **5 years of weekly closes**) and annual fundamentals timeseries (`annual*` types, about 4 years). **No quarterly data, no FII/DII, no analyst targets, no daily OHLC.** Adding `quarterly*` types and a `1d` chart call is straightforward.
- Domain:
  - `stock_ratios.py`: ratios and CAGR
  - `stock_rulesets.py`: Set 1 / Set 2 10-pointer and 6-pointer
  - `stock_valuation.py`: relative valuation with weights EV/EBITDA 0.3, P/S 0.3, P/E 0.3, P/B 0.1, and a 1.0–1.2× range
  - `stock_context.py`: EIC and macro snapshot
  - `momentum.py`: 3/6/12-month returns, above 200-DMA
- News: `data/news.py` (Google News RSS, sentiment and materiality tagging, fund-centric scopes) and `data/trend_view.py` (`_fetch_one` RSS helper).
- LLM: `llm/base.py` `LLMClient.complete(messages, schema)` with structured output and `BudgetedLLM`. `max_llm_calls_per_run=12` (config.py L213). Default provider `openai` via LangChain (`llm/factory.py`), on-disk `CachedLLM`. **No web-search tool is wired in.**
- Compliance: `guardrails/compliance.py` `scrub()` (bans guaranteed/risk-free/multibagger…) and `STOCK_DISCLOSURE` (not SEBI RA advice). Grounding: `guardrails/grounding.py` `check_text(text, facts, known_names, require_citation_ids)` — numbers must match a fact value, `[ids]` must be fact ids.
- Background-job pattern: stock sync uses module-level `_SYNC_LOCK = threading.Lock()` (`data/stocks/sync.py` L32), `get_job(job_id)`, `POST /api/stocks/sync` → 202 or 409, polling `/api/stocks/sync/{job_id}`.
- History: `history/store.py` has `advice_history` and `followup_message` tables (cascade delete). There is no per-stock report table.
- Tests: `tests/fakes.py` `ScriptedLLM` (schema-dispatching fake) — extend with 360° part schemas.
- Frontend dependencies: React 19, recharts, react-query, zustand, lucide. **No Markdown renderer** (not needed: structured UI chosen).

**Gaps the plan must cover (template vs data):**
- Quarterly results / TTM and QoQ/YoY → Yahoo `quarterly*` timeseries
- Shareholding trend (FII/DII/public), delivery % → web search / overrides
- Concall/investor presentation (order book, guidance) → web search
- Analyst consensus/targets → web search
- Daily OHLCV for EMA/RSI/MACD/ADX and support/resistance → Yahoo `1d` chart
- 3/5-year historical median multiples → derive from prices + annual EPS/BVPS
- Contingent liabilities, related-party transactions, executive pay → web search, else "Not available"
- Peers side by side → cached sector peers (`get_sector_peers`)

## 4. User decisions (answered 2026-11-15 19:57 IST)

| # | Question | Decision |
|---|---|---|
| 1 | When to generate | **On demand only**: a "360° Report" button on each recommended stock (and in Analyse Stock). No auto-generation during Advise. |
| 2 | Data beyond Yahoo | **LLM web-search tool** (OpenAI `web_search` / Anthropic `web_search`) that must cite URL + date for every number; Google News RSS as fallback; manual overrides; otherwise "Not available". |
| 3 | LLM depth | **4 parts from the template, one structured LLM call per part** (~4–6 calls + web searches per stock). Numeric tables from Python; LLM writes judgement sections (moat, management, growth, risk, legend verdicts, final verdict). |
| 4 | Effect on recommendation | **Informational by default** (verdict / red-flag badges). Optional setting, **off by default**, drops stocks rated AVOID/SELL or with ≥5/7 legend sell signals and returns weight to MFs. |
| 5 | Output format | **In-app structured report only** (drawer from Advise row + tab in Analyse Stock). No Markdown/PDF export. |

**Consequence to handle in the plan (from 1 + 4):** since reports are on-demand, the optional gating setting can only use an **already-cached** report (e.g. ≤ `stock_360_max_age_days` old). Advise rows show "No 360° report yet — Generate" when none exists; the gate ignores stocks without a report (and says so in warnings).

## 5. Design direction (final, to write into the plan)

1. **Hybrid engine.** Deterministic Python "quant core" computes every number it can: reverse DCF (12% WACC, 4.5% terminal), DCF, Lynch PEG 1.0/1.5/2.0, Graham √(22.5·EPS·BVPS), Buffett entry = 5Y fwd EPS × exit P/E ÷ 1.12⁵, return decomposition, mean-reversion price, EMA/RSI/MACD/ADX from daily prices, Weinstein stage, S/R pivots, consensus sell score rule (5–7 sell / 3–4 reduce / 1–2 hold / 0 add).
2. **Web-research step** (new `data/stocks/web_research.py`): structured LLM call with the provider's web-search tool, returning `WebFact{name, value, unit, as_of, source_url, quote}`; facts without URL+date are discarded. Fallback: RSS headlines. Results become `Fact`s with `Citation(source="web_search", url_or_doc_id=url, as_of=date)`.
3. **LLM parts** get only cited facts; anything missing is shown as "Not available / Requires live verification". Every part's text goes through `check_text()` (one retry, then template fallback) and `scrub()`.
4. **Step 0 freshness table** built from each fact's `Citation.as_of` and source.
5. **New modules:**
   - `domain/stock_360/` (`intrinsic.py`, `technicals.py`, `cycle.py`, `legends.py`, `freshness.py`)
   - `agents/stock_360_analyst.py`: 4 structured Pydantic part-schemas, own budget `stock_360_max_llm_calls`
   - `stock_360_report` table (id, symbol, run_id nullable link to advice run, data_mode, as_of, status, `report_json`, error, created_at), cached per (symbol, as_of date, data_mode)
   - Background job endpoints: `POST /api/stocks/{symbol}/report360` → 202 {job_id} (or cached report), `GET /api/stocks/report360/{job_id}`, `GET /api/stocks/{symbol}/report360/latest`; per-symbol lock, 409 if running
   - Advise response: per stock row `report360: {status, verdict, rating, sell_score, red_flags, report_id} | null`
   - Optional gate in `decision.py` (setting `stock_360_gate_enabled=False`)
   - Frontend: `Stock360Report.tsx` (drawer + tab, 4 part accordions, section components with recharts), button on Advise stock rows and in `StockAnalyzer.tsx`, polling via react-query
6. **Data extensions:** Yahoo daily chart (1d, ~2y), `quarterly*` timeseries, 360° overrides (shareholding, order book, consensus) extending `StockOverridesInput`, stock-scoped news queries.
7. **Web trend picks** get the same button; their report highlights "not fundamentally screened".
8. **Compliance:** template action labels shown as "Educational rating"; `STOCK_DISCLOSURE`; legend quotes labelled "simulated".
9. **Synthetic mode:** web-research is replaced by a deterministic fake so tests and demo work offline.
10. **Tests:** golden formula tests, indicator tests on synthetic series, every section present or marked unavailable, grounding test (no uncited numbers), web facts without URL/date rejected, API job lifecycle (202/409/poll/cached), budget cap, gate on/off in decision, `ScriptedLLM` extension.

## 6. How to resume

1. Read this file, `docs/plans/360-analysis-template.md` and `docs/plans/stock-fundamental-analysis-plan.md` (style reference).
2. No more questions needed — decisions are in §4.
3. Write `docs/plans/stock-360-analysis-plan.md` in the same style as the other plans: Goal → options and selections (§4) → current state (§2–3) → phased steps (A data + web research → B quant core → C 360° agent → D storage + job API → E Advise integration + optional gate → F frontend → G tests) → relevant files → verification → scope (in/out: follow-up chat & monitoring, auto-generation, export are out).
