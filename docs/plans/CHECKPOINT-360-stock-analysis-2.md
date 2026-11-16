# Progress Checkpoint 2: 360° Stock Analysis for Every Recommended Stock

**Timestamp:** 2026-11-15 20:28 IST
**Previous checkpoint:** [CHECKPOINT-360-stock-analysis.md](CHECKPOINT-360-stock-analysis.md) (research and the first design draft; still valid except where this file overrides it)
**Template:** [360-analysis-template.md](360-analysis-template.md) (Markdown copy of the Master Prompt PDF)
**Plan to be written at:** `docs/plans/stock-360-analysis-plan.md` (**not created yet**)
**Status:** Research is done and the user has answered all the open questions. No plan written yet, no code changed. **Next step: write the plan.**

---

## 1. User decisions (answers to the questions in §5 of checkpoint 1)

| # | Question | Selected |
|---|---|---|
| 1 | When reports are generated | **On demand only.** A "360° Report" button on each stock; this is the cheapest option. The report is **not** generated automatically during Advise. |
| 2 | Data sources beyond Yahoo | **LLM web-search tool** (OpenAI `web_search` / Anthropic `web_search`). Every number it returns must cite a URL and an "as of" date. **Fallbacks:** Google News RSS, then manual overrides; anything still missing is shown as "Not available". No scraping of Screener or Trendlyne. |
| 3 | How much the LLM writes | **The template's 4 parts, one structured LLM call per part** (about 4–6 calls plus web searches per stock). **Python** computes the numeric tables. **The LLM** writes the judgement sections: moat, management, growth, risk, legend verdicts and the final verdict. |
| 4 | Whether the verdict changes the recommendation | **Informational by default.** Verdict and red-flag badges are shown next to the stock. An **optional setting, off by default**, drops stocks rated AVOID/SELL or with ≥ 5 of 7 legend sell signals, and gives their weight back to mutual funds. |
| 5 | Output formats | **In-app structured report only**: a drawer, plus a tab in Analyse Stock. No Markdown download or PDF export. |

### What these answers change in the checkpoint 1 design
- **Drop:** Advise no longer queues reports automatically. Remove the "Advise auto-queues reports" item from checkpoint 1 §4.4.
- **Still needed:** the background job (202 + polling) and the report cache, because one report takes 4–6 LLM calls plus web searches.
  - Reports are cached per (symbol, as-of date), so pressing the button again the same day returns the cached report.
- **Optional verdict gate:** it can only use reports that already exist.
  - A recommended stock with no cached report is never dropped. It is shown with a "No 360° report yet — generate" prompt.
  - The plan must say this explicitly.
- **Remove:** the export items from checkpoint 1 §4.4. The frontend still needs a Markdown renderer, or structured section components, for the LLM text.

---

## 2. New findings since checkpoint 1

- **Follow-up chat and monitoring are confirmed fund-only.** `grep stock` in `agents/followup.py` and `agents/monitoring.py` finds nothing. **Decision:** the plan doesn't change them. This is listed under Scope → Excluded, and could be added later.
- **Decision step** (`agents/decision.py`):
  - The stock sleeve is split out by `category == "Direct Equity"` (~L160, L223–255).
  - It is sized with `size_stock_sleeve` / `spec_stock_sleeve`.
  - **Hook for the optional verdict gate:** filter `stock_items` here, before sizing. Unused sleeve weight already flows back to mutual funds.
- **Pipeline** (`pipeline.py` ~L398): `stock_entries` and `stock_verdicts` are appended to `entries` and `verdicts` after the MF analysts run. The verdict gate could also go here instead, before `decide()`.
- **Grounding** (`guardrails/grounding.py` `check_text(text, facts, known_names, require_citation_ids)`):
  - Every number must match a fact value; `[ids]` must be fact ids; ISO dates and period labels are ignored.
  - **Reuse:** validate the LLM part outputs against the report's fact list. Web-search facts become `Fact`s with `Citation(source="web_search", url_or_doc_id=url, as_of=date)`.
- **Test double** (`tests/fakes.py` `ScriptedLLM`): it branches on the `schema` passed in. Add branches for the 4 new part schemas and a fake web-search client.
- **Concurrency pattern:**
  - `data/stocks/sync.py` uses a module-level `_SYNC_LOCK = threading.Lock()`.
  - **Reuse it for the 360° jobs:** one job per symbol at a time, plus a global concurrency cap.
- **API helpers to reuse** (`api/main.py`):
  - `_validate_stock_symbol()` (L2139)
  - `StockOverridesInput` (L2160): FII %, pledge %, bank metrics. Extend it with DII %, promoter %, order book (₹ Cr), and analyst consensus target, rating counts and number of analysts.
- **Config** (`config.py`):
  - `llm_provider` is `openai` or `anthropic`; the default model is `gpt-6-luna`.
  - `max_llm_calls_per_run=12`.
  - **Add:** a separate `stock360_max_llm_calls` (about 10) and `stock360_max_web_searches` (about 15).
  - **Add:** `stock360_verdict_gate: bool = False`.
  - **Add:** `stock360_report_ttl_hours` (24).
  - **Add:** the fixed assumptions (WACC 12%, terminal growth 4.5%, Buffett hurdle 12%, exit P/E cap 18–20×) as settings.
- **Web search isn't wired in.** `llm/langchain_client.py` wraps a LangChain chat model.
  - **Plan:** add `llm/web_search.py` with a `WebSearchClient` protocol, an OpenAI and an Anthropic implementation (provider-native tool binding), and a fake for tests.
  - Results are normalised into `WebFact(name, value, unit, as_of, url, source_title)`.
  - A fact with no URL or no date is thrown away.

---

## 3. Final design outline for the plan (to write next)

1. **Data layer**
   - Yahoo daily OHLCV (`interval=1d`, range 2y) for the technicals.
   - Yahoo `quarterly*` timeseries for TTM and QoQ/YoY.
   - Stock-scoped Google News RSS.
   - The web-search fact collector covers: Step 0 items, shareholding trend, concall/order book, consensus, corporate actions/demerger, contingent liabilities, RPTs and pledges.
   - Manual overrides take priority over web facts, and web facts over "Not available".
2. **Quant core** in `domain/stock_360/`, pure functions with golden tests:
   - **Intrinsic value:** reverse DCF, DCF, Lynch PEG at 1.0/1.5/2.0, Graham Number, Buffett entry price.
   - **History-based:** historical median P/E from prices and EPS, the mean-reversion price, return decomposition (EPS growth vs re-rating).
   - **Forensics:** cash conversion (CFO/EBITDA < 70% flag), other income as % of PBT, CCC days.
   - **Technicals:** EMA 20/50/100/200, RSI-14, MACD, ADX, pivots S1–S3/R1–R3, Weinstein stage, golden/death cross.
   - **Classification:** Lynch category heuristics and cyclical peak/mid/trough heuristics.
   - **BFSI:** metric swap.
   - **Legend sell checks:** the mechanical checks per legend (Graham D/E > 1, current ratio < 1.5, premium to Graham Number, and similar).
   - **Consensus rule:** 5–7 sell / 3–4 reduce / 1–2 hold / 0 add.
   - **Tax note:** STCG 20% / LTCG 12.5% above ₹1.25L.
3. **Agent** `agents/stock_360_analyst.py`:
   - One structured LLM call for each of the template's 4 parts, each with its own Pydantic schema.
   - The prompt contains only the fact table plus the quant outputs.
   - Each part goes through `check_text` grounding: one retry, then a deterministic fallback with "Requires live verification".
   - `scrub()` runs on all text.
   - Ratings are labelled "educational rating", legend quotes are labelled "simulated", and `STOCK_DISCLOSURE` is added.
4. **Storage:** a `stock_360_report` table (id, symbol, as_of, data_mode, status, `report_json`, `facts_json`, `llm_calls`, `web_searches`, created_at, error), with an optional `advise_run_id` link.
5. **API:**
   - `POST /api/stocks/{symbol}/report360` → 202 `{job_id}`, or 200 with the cached report. Body: `{force?, overrides?}`.
   - `GET /api/stocks/report360/jobs/{job_id}` → progress by part (Step 0 / Part 1–4).
   - `GET /api/stocks/{symbol}/report360/latest`
   - Advise stock rows gain `report360: {status, rating, final_action, sell_score, red_flags, as_of} | null` from the cache.
   - Logging: audit-log entry and `trace_context`, as the analyze endpoint does.
6. **Pipeline (optional gate):** when `stock360_verdict_gate` is on, stocks with a cached report rated AVOID/SELL or with sell score ≥ 5 are dropped before sizing. The reason goes into the research log and `stock_warnings`.
7. **Frontend:**
   - `Stock360Report.tsx` drawer: 17 section cards, the freshness table, "Not available" chips, citation links.
   - A "360° Report" button on Advise stock rows (`App.tsx` ~L1551–1604), on Compare Stocks, and as a tab in `StockAnalyzer.tsx`.
   - Progress polling with react-query.
   - Verdict and sell-score badges on rows that have a cached report.
   - The gate toggle in settings.
8. **Tests:**
   - formula goldens and indicator tests on synthetic series
   - section completeness (every section is present or explicitly marked unavailable)
   - grounding (no number without a fact)
   - the web-search fake drops facts with no URL or date
   - job lifecycle: 202 → done, cache hit, 409 for a duplicate job
   - the gate: off by default, never drops a stock with no report, gives freed weight back to mutual funds
   - `ScriptedLLM` branches for the new schemas

---

## 4. How to resume

1. Read this file, [CHECKPOINT-360-stock-analysis.md](CHECKPOINT-360-stock-analysis.md), [360-analysis-template.md](360-analysis-template.md) and [stock-fundamental-analysis-plan.md](stock-fundamental-analysis-plan.md). The last one is the style reference.
2. Write `docs/plans/stock-360-analysis-plan.md` in this order:
   1. Goal
   2. Options presented and selections made (table from §1)
   3. What exists today
   4. Phased steps from §3: data → quant core → agent → storage and API → optional gate → frontend → tests
   5. Relevant files
   6. Verification
   7. Scope
3. **Scope → Excluded:** automatic generation in Advise, Markdown/PDF export, follow-up chat and monitoring changes, scraping.
