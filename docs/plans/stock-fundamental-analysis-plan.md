# Plan: Stock Fundamental Analysis (Advise + Compare Stocks + Analyse Stock)

Source rules: [FA-Course-Notes.md](FA-Course-Notes.md) (Fundamental Analysis — CA Rachana Ranade).

## Goal

The FA course notes become a new **Stock Rule Set 2**. Today's equity heuristics become **Stock Rule Set 1**.

The work adds:
- a stock data layer: yfinance in real mode, a synthetic catalog in synthetic mode
- a **Sync stocks** job (at most once a day) that fetches the **entire NSE `EQ` series**, stores it in SQLite, and scores every stock under both stock rule sets
- a deterministic ratio and rule engine
- two new tabs: **Compare Stocks** and **Analyse Stock**
- an **Include stocks** option in Advise, which sizes a capped stock sleeve inside the equity bucket, using only the synced cache
- **no market-cap limit by default**: the user can optionally enter a minimum market cap (and a minimum traded value) in Sync, Advise, Analyse and Compare; an empty field means the filter is ignored

The mutual fund rule sets (`set1`, `set2`, `combined`) stay as they are.

---

## Options presented and selections made

### 1. Stock data source
Today stocks exist only as a hardcoded catalog of 4 records.

| Option | Notes | Selected |
|---|---|---|
| **yfinance (NSE `.NS` tickers) for real mode + expanded synthetic catalog for synthetic mode** *(recommended)* | Free. Provides P&L, balance sheet, cash flow, price history, market cap, P/E and P/B. FII holding is not available and is shown as "no data". | ✅ |
| Screener.in scraping | Richer Indian data (FII holding, 10-year history), but carries terms-of-service risk and breaks easily. | |
| Synthetic catalog only (no live data) | | |

### 2. Rule set scope ("existing rules → Set 1, FA notes → new set")
| Option | Selected |
|---|---|
| **Separate stock rule sets.** Stock Set 1 = current equity heuristics (P/E vs sector, earnings growth, D/E ≤ 2, pledge ≤ 25%, insider activity). Stock Set 2 = FA notes (10-pointer, 6-pointer for banks, ratio analysis). MF set1/set2/combined untouched. *(recommended)* | ✅ |
| Same as above, plus a Stock Combined set (merged with the existing `rule_merge` engine) | |
| Add the FA rules as a new set (`set3`) in the existing MF rule-set selector | |

### 3. Stock universe
| Option | Selected |
|---|---|
| Screen the Nifty 100 in Advise; Analyse and Compare accept any NSE ticker *(recommended at the time)* | ~~✅~~ superseded |
| Nifty 50 only | |
| Nifty 500 (slow first fetch, needs caching) | |
| **Entire NSE `EQ` series, fetched by a daily sync job, with no market-cap limit unless the user enters one** *(follow-up decision; see below)* | ✅ |

### 4. Advise integration (when the user ticks "Include stocks")
| Option | Selected |
|---|---|
| **A capped sleeve carved out of the equity bucket (e.g. max 30% of equity, max 10% per stock), sized and shown in the plan alongside the MF picks** *(recommended)* | ✅ |
| A separate "Fundamentally strong stocks" list, for information only (no sizing or allocation) | |
| Both: a sized sleeve plus a watchlist of runners-up | |

### 5. Compare Stocks — number of stocks
| Option | Selected |
|---|---|
| **2 (same as Compare Funds)** *(recommended)* | ✅ |
| Up to 4 | |

### 6. Analyse Stock tab — depth (multi-select)
| Option | Selected |
|---|---|
| Ratio analysis by category (activity, liquidity, solvency, profitability, valuation, per-share, dividend/growth) *(recommended)* | ✅ |
| 10-pointer / 6-pointer (banks) pass/fail checklist *(recommended)* | ✅ |
| Management efficiency (CFO/PAT conversion over the years) and 5-year CAGR table *(recommended)* | ✅ |
| Relative valuation (weighted EV/EBITDA, P/S, P/E, P/B vs sector peers → fair value range) | ✅ |
| LLM narrative grounded in cited facts, including economy and industry context (cyclical/defensive, macro indicators) *(recommended)* | ✅ |
| Bank-specific metrics (NIM, CASA, GNPA, PCR, CAR), entered by hand because yfinance lacks them | ✅ |

### Defaults chosen by me (not asked; adjustable in settings)
- **Default stock rule set:** `set2` (FA notes). Switch to `set1` for today's heuristic behaviour.
- **Stock sleeve as a share of the equity bucket:** Conservative 0%, Moderate 20%, Aggressive 30%.
- **Per-stock cap:** 10% of the whole portfolio.
- **Minimum horizon for stocks:** 5 years.
- **"Fundamentally good" rule:** at least 80% of the checks that have data must pass.
  - With a market-cap minimum: at least 7 of 10 checks must have data (4 of 6 for banks).
  - Without one: at least 6 of 9 (4 of 5 for banks).
- **Per-sector cap:** at most 40% of the stock sleeve in one sector.

### Follow-up decisions (after the initial review)

#### 7. Widening the Advise universe beyond the Nifty 100
Concern raised: screening only the Nifty 100 makes Advise miss good stocks.

| Option presented | Selected |
|---|---|
| 1. Make the universe a setting (`nifty100 / nifty200 / nifty500 / custom`) | Replaced by the full NSE list (#9) |
| 2. Fetch data in a background job, not during Advise; Advise reads only the cache | ✅ (as the daily sync, #8) |
| 3. Two-stage screen: cheap filter on snapshot data, full statements only for survivors | ✅ (stage 1 runs only when the user gives a minimum) |
| 4. Stock list depends on risk profile (Moderate → Nifty 100, Aggressive → Nifty 500) | ❌ |
| 5. Users add up to 5 tickers of their own in Advise | Not decided; kept as a possible addition |
| 6. At most 40% of the stock sleeve in one sector | ✅ |

#### 8. Daily "Sync stocks" button
User proposal: a sync button, pressed at most once a day, that fetches fundamentals and stores the good stocks in the database. Accepted, with these changes:
- **Store all stocks, not just those that pass.** Every company's data and its Set 1 and Set 2 results are stored. This keeps Set 1 switchable, lets Analyse and Compare use the cache, explains rejections, and allows rule changes to be re-applied with no new fetch ("Re-screen").
- **Prices are refreshed on every sync; financial statements only when stale.** Prices come from one batched download. Statements are refetched when older than `stock_financials_ttl_days` (30), spread across days.
- **The sync runs as a background job.** It returns a job id, reports progress, takes a lock, allows one run per day unless `force=true`, keeps the last good data when a ticker fails, and only switches to the new run when it finishes.

#### 9. Universe size and the market-cap rule
- The Nifty 500 still misses recent IPOs, companies excluded by free-float rules, and companies failing other index eligibility rules. **Decision: scan the entire NSE `EQ` series** (about 2,000+ companies). The SME platform and trade-for-trade `BE`/`BZ` series are left out.
- Options presented for the market-cap rule: keep the FA notes' fixed ₹15,000 Cr minimum, or relax it per risk profile (₹15,000 / ₹5,000 / ₹1,000 Cr). **Both rejected.**
- **Decision: no market-cap limit by default.**
  - The user may enter an optional "Min market cap (₹ Cr)" in Sync, Advise, Analyse and Compare.
  - If the field is empty, market cap is ignored everywhere: in filtering, in the Set 2 check, and in scoring.
  - If a value is given, it replaces the ₹15,000 Cr in the Set 2 check.
  - Market cap is always still displayed.
- **Also recommended, same rule (empty = ignored):** an optional "Min avg daily traded value (₹ Cr)" field, so the advice never includes a stock that can't be bought or sold easily.

### Open considerations (not yet decided)
1. Confirm the sleeve percentages (0 / 20 / 30% of equity), the 80% threshold and the 40% per-sector cap.
2. **FII holding:** yfinance only exposes institutional holding in general. The options are:
   - **A (planned):** show FII holding as "no data".
   - **B:** use institutional % as a stand-in for FII %, labelled as such.
3. Confirm the optional minimum traded-value field (planned; empty = ignored).
4. Whether to add user-supplied tickers to Advise (up to 5, fetched live during the request).

---

## What stock support exists today

- **Data:** stocks exist only as a hardcoded `EQUITY_CATALOG` in [multi_asset.py](../../src/wealth_advisor/data/multi_asset.py). It holds 4 records: TCS, HDFC Bank, Reliance and a dummy "High Pledge Infra".
  - There is no live data.
  - `YFinanceProvider` in [real_fetcher.py](../../src/wealth_advisor/data/real_fetcher.py) is an empty placeholder for mutual funds.
  - yfinance is not listed as a dependency.
- **Scoring:** one heuristic in [subscores.py](../../src/wealth_advisor/domain/subscores.py). It compares P/E with sector P/E and adjusts for earnings growth, debt/equity, promoter pledge and insider activity.
- **Hard rules:** [suitability.py](../../src/wealth_advisor/guardrails/suitability.py) rejects promoter pledge above 25% and debt/equity above 2.
  - `check_fund()` also requires fields that only mutual funds have (expense ratio, AUM, track record, direct plan).
  - The catalog therefore fills those fields with fake values.
- **Where stocks show up:** only through `EQUITY_RESEARCH_CONFIG` in `ResearchSupervisor` ([supervisor.py](../../src/wealth_advisor/orchestration/supervisor.py)).
  - The `/api/advise` route calls `advise()` without a supervisor.
  - As a result, the product never recommends stocks.
- **Frontend and API:** no stock UI, endpoints or search exist.

---

## Steps

### Phase A — Data layer (blocks everything else)

1. Create `data/stocks/provider.py` with a `StockDataProvider` protocol.
   - **Methods:** `search`, `snapshot`, `financials` (multi-year annual P&L, balance sheet and cash flow), `price_history` and `shareholding`.
   - **Models:** `StockProfile`, `StockFinancialYear` and `StockSnapshot`.
   - **Fields:** revenue, COGS, EBITDA, EBIT, PBT, PAT, interest, D&A, inventory, receivables, payables, current assets and liabilities, total debt, equity, net fixed assets, total assets, CFO, capex, FCF, dividends, shares, EPS, price, market cap (Cr), EV, sector, industry, `is_bank`, and FII/institutional holding.
2. Add `YFinanceStockProvider` using NSE `.NS` tickers.
   - It maps yfinance statement rows to the models and converts values to ₹ Cr.
   - Data yfinance lacks (FII %, promoter pledge, insider activity) is returned as `None` and reported as "no data".
   - yfinance has about 4 annual years, so CAGR uses the years available and labels the period.
3. Add `SyntheticStockProvider` with about 20 stocks across sectors.
   - Sectors: IT, FMCG, banks, auto, pharma, plus one deliberately weak stock.
   - Each stock has multi-year statements.
   - The existing 4 catalog names are included with consistent values.
   - Tests use this provider.
4. Add a bundled universe file `data/stocks/nse_equity_list.csv` (from NSE `EQUITY_L.csv`, filtered to the `EQ` series) and a loader `data/stocks/universe.py`.
   - Columns: symbol, name, series, listing date and ISIN.
   - Sector and industry come from yfinance during sync. A small override map sets `is_bank` and the cyclical/defensive tag where yfinance's sector is ambiguous.
   - Optional index tags (Nifty 50/100/500) are used only as a search filter, never to limit the universe.
   - The list is bundled because NSE blocks script downloads. The CLI command `wealth-advisor stocks refresh-universe` attempts an update and keeps the bundled copy if that fails.
5. Add SQLite tables to the store:

   | Table | Contents |
   |---|---|
   | `stock_master` | symbol, name, sector, industry, `is_bank`, cyclical flag, index tags, `in_universe` |
   | `stock_snapshot` | price, shares, market cap (₹ Cr), EV, 20-day average traded value (₹ Cr), institutional %, beta, `fetched_at` |
   | `stock_financials` | symbol, fiscal year, statement values as JSON, `fetched_at` |
   | `stock_prices` | symbol, date, close (about 5 years, for charts) |
   | `stock_sector_medians` | sector, peer P/E, P/S, P/B and EV/EBITDA medians, computed during sync |
   | `stock_screen_result` | `sync_run_id`, symbol, rule set, `passed`, score, `checks_json`, `data_status` (`ok` / `insufficient_data` / `fetch_failed`) |
   | `stock_sync_run` | id, started, finished, status, `min_market_cap_cr` and `min_traded_value_cr` used (or null), counts (total / fetched / failed / passed set1 / passed set2) |

   - Cache lifetimes are set by `stock_snapshot_ttl_hours` (24) and `stock_financials_ttl_days` (30).
   - The provider is chosen from `data_mode`.
6. Add `yfinance` to [pyproject.toml](../../pyproject.toml).

### Phase A2 — Stock sync job (depends on Phase A; Phase B's ratio and rule code is needed for steps A2.3–A2.4)

A2.1 Add a `StockSyncService` in `data/stocks/sync.py`. Its entry point is `run_sync(min_market_cap_cr: float | None, min_traded_value_cr: float | None, force: bool)`.

A2.2 **Prices and size** (every run):
   - One batched `yf.download` of the latest prices for the whole universe.
   - Shares outstanding are refreshed weekly, with a lightweight call per ticker.
   - Compute market cap and the 20-day average traded value.

A2.3 **Optional stage-1 filter:**
   - Applied only for the inputs the user filled in. If both fields are empty, every company in the universe goes on to stage 2.
   - Companies below a given minimum are marked `filtered_out`. They keep any data cached earlier.

A2.4 **Stage 2:**
   - Fetch financial statements only when missing or older than the cache lifetime, with throttling (`stock_sync_max_rps`, default 2) and up to 2 retries with backoff.
   - Compute ratios and sector medians, score every stock under Set 1 and Set 2, and write `stock_screen_result` rows under the new `sync_run_id`.

A2.5 **Run safety:**
   - A process lock prevents two syncs at once.
   - Only one run per calendar day unless `force=true`.
   - The "latest run" pointer moves only when a run completes. A ticker that fails keeps its last good data and is marked stale.
   - A "retry failed" mode reruns only the failed tickers.

A2.6 **Synthetic mode:** loads the bundled synthetic catalog through the same code path; it finishes instantly.

A2.7 **Logging:**
   - An audit-log entry per run, with its counts and the minimums used.
   - A Langfuse span per run.

A2.8 **Re-screen:** a mode that only re-applies the rules to cached data, with no fetch. It is used after rule or threshold changes.

### Phase B — Domain logic (depends on Phase A's models; steps 7–9 can run in parallel)

7. **Ratios** in `domain/stock_ratios.py`. Every formula table in the FA notes is covered. Where the notes say "average", the average of opening and closing balances is used.
   - **Activity:** inventory, receivables, payables, working capital, fixed asset and total asset turnover.
   - **Liquidity:** current, quick, cash and defensive interval.
   - **Solvency:** debt-to-assets, debt-to-capital, debt/equity, financial leverage, debt/EBITDA, interest coverage and fixed charge coverage (lease data is `None`).
   - **Profitability:** gross, PBT and PAT margins, ROA, ROCE and ROE.
   - **Valuation:** P/E, P/CF, P/S, P/BV and EV/EBITDA.
   - **Per share:** EPS, cash flow per share, EBITDA per share and dividend per share.
   - **Dividend and growth:** payout, retention and sustainable growth rate (retention × ROE).
   - **Management efficiency:** CFO/PAT by year, plus the average.
   - **CAGR table:** revenue, EBITDA, PAT and FCF.
   - Each ratio returns its value, formula, inputs and `fact_id`. The `fact_id` lets narratives cite the number.

8. **Stock rule sets** in `domain/stock_rulesets.py`.
   - This mirrors the structure of [rulesets.py](../../src/wealth_advisor/domain/rulesets.py) but is kept separate.
   - It defines `STOCK_SET1_RULES`, `STOCK_SET2_RULES`, `AVAILABLE_STOCK_RULE_SETS` and a request-scoped `use_stock_rule_set()`.
   - Add `stock_rule_set: Literal["set1","set2"] = "set2"` to `Settings` in [config.py](../../src/wealth_advisor/config.py).

   **Stock Set 1 (existing behaviour):**
   - the P/E-vs-sector score formula from `subscores.py`
   - hard limit: promoter pledge ≤ 25%
   - hard limit: debt/equity ≤ 2

   **Stock Set 2 (FA notes):**
   - **Market-cap check (both lists below):** applied only when the user gives a minimum, and that value replaces the notes' ₹15,000 Cr. With no value the check is marked "not applied", which makes the lists a 9-pointer and a 5-pointer.
   - **10-pointer (non-banks):**
     - market cap > user minimum (optional)
     - revenue growth over 3 years > 10%
     - profit growth over 3 years > 5%
     - ROE > 15%
     - ROCE > 15%
     - debt/equity < 2
     - current ratio > 1.5
     - CFO > 0
     - FCF higher than the previous year
     - FII holding > 10%
   - **6-pointer (banks):**
     - market cap > user minimum (optional)
     - sales growth over 3 years > 10%
     - profit growth over 3 years > 5%
     - ROE > 12%
     - current ratio > 1.5
     - FII holding > 10%
     - ROCE, debt/equity, CFO and FCF are skipped for banks.
   - **"Fundamentally good" rule:** at least 80% of the checks that have data must pass.
     - With a market-cap minimum: at least 7 of 10 checks must have data (4 of 6 for banks).
     - Without one: at least 6 of 9 (4 of 5 for banks).
   - **Rule evaluation is a pure function.** `evaluate(profile, rule_set, min_market_cap_cr=None)` takes no other inputs. The sync stores results computed with no market-cap minimum, and Advise, Analyse and Compare re-apply the user's minimum when they read the results; this is cheap.
   - **Hard fails:** debt/equity ≥ 2, and negative CFO (non-banks only).
   - **Score weights:** profitability 30%, growth 25%, solvency/liquidity 20%, management efficiency 15%, valuation 10%.
   - **Bank metrics** count only when entered by hand: NIM, CASA, GNPA, NNPA, PCR, CAR, loan-to-deposit ratio, cost of funds and operating cost ratio.
     - Each metric is scored in the direction the notes give (higher NIM is better, lower GNPA is better, CAR must be ≥ 9%, and so on).

9. **Relative valuation** in `domain/stock_valuation.py`.
   - Peers are the other synced stocks in the same sector with `data_status = ok`. The medians come from `stock_sector_medians`, which the sync computes. Market cap is not used to choose peers.
   - If a sector has fewer than 5 peers, the valuation is marked "insufficient peers".
   - Weights follow the Infosys example: EV/EBITDA 30%, P/S 30%, P/E 30%, P/B 10%.
   - Banks use P/B and P/E only.
   - The fair value range runs from the weighted value to 1.2 × that value.
   - Expected return is measured against the current market price.

10. **Stock compare** in `domain/stock_compare.py`, modelled on `compare_funds()` in [fund_compare.py](../../src/wealth_advisor/domain/fund_compare.py).
    - It scores criteria from the chosen stock rule set.
    - It returns a winner, per-criterion rows and a checklist, in the same `ComparisonResult` shape.
    - If the two stocks are in different sectors, it shows a warning, as Compare Funds does for different categories.

11. **Industry and economy context** in `domain/stock_context.py`.
    - A sector → cyclical/defensive map, and a PLI-sector flag.
    - A dated, editable macro snapshot in `data/macro_snapshot.json`: GDP, IIP, PMI, CPI, CRR, SLR, repo, reverse repo and unemployment.
    - These values are shown as facts with their "as of" date. They are not fetched live.

### Phase C — Agents (depends on Phase B)

12. `agents/stock_narrator.py` (`StockNarrator`).
    - Writes the Analyse narrative and the Compare verdict.
    - Every claim cites a `[fact_id]` from the ratios, checklist, valuation, macro or industry facts.
    - It reuses the existing citation check: one retry, then a template fallback, as `CompareNarrator` does in [compare_narrator.py](../../src/wealth_advisor/agents/compare_narrator.py).
13. `agents/stock_researcher.py` for Advise.
    - **Research step:** reads the latest sync results from the cache, never from yfinance.
      - It applies the user's optional market-cap and traded-value minimums.
      - It keeps stocks that pass under the active rule set with `data_status = ok`, ranks them by score, and keeps the top N.
      - It adds an optional one-line LLM reason for each, which goes through the citation check.
      - **No sync yet:** the stock sleeve is skipped with the reason "stock data not synced", and the response asks the user to run a sync.
      - **Last sync older than `stock_stale_after_days` (3):** the result carries a staleness warning.
    - **Analyst step:** builds a `DossierEntry` and `AnalystVerdict` for each stock, using a new category `"Direct Equity"` in `MULTI_ASSET_CATEGORIES`.
    - **Sub-scores:**
      - fundamental: the rule-set score
      - fit: cyclical vs. defensive against the risk profile, plus beta
      - news and sentiment: the existing modules
      - liquidity: 20-day average traded value (market cap is not used)

### Phase D — Pipeline and decision (depends on Phase C)

14. Add `include_stocks`, `stock_rule_set`, `min_market_cap_cr: float | None` and `min_traded_value_cr: float | None` parameters to `advise()` in [pipeline.py](../../src/wealth_advisor/pipeline.py).
    - `None` means the filter is ignored.
    - The values used are recorded in the audit log and the response.
    - **Sync minimum higher than Advise minimum:** if the latest sync ran with a higher market-cap minimum than Advise asks for, add the warning: "Stocks were synced with a ₹X Cr minimum; smaller companies are unavailable. Run a sync without a minimum to include them."
    - When `include_stocks` is on, the stock researcher and analyst run next to the per-bucket MF researchers.
    - Stocks are skipped when the horizon is shorter than `stock_min_horizon_years` (5), or when the stock sleeve for the user's risk category is 0%. The reason is recorded in the research log.
15. **Sleeve sizing** in `decide()` ([decision.py](../../src/wealth_advisor/agents/decision.py)):
    - Split the equity-bucket candidates into stocks and mutual funds.
    - Stock sleeve = equity weight × `stock_sleeve_pct_of_equity` (Conservative 0%, Moderate 20%, Aggressive 30%).
    - Size the stocks with `size_positions()` ([sizing.py](../../src/wealth_advisor/domain/sizing.py)), capping each stock at `max_weight_per_stock` = 10% of the portfolio.
    - Limit each sector to `max_sector_pct_of_stock_sleeve` (40%) of the sleeve.
    - Any unused sleeve weight goes back to mutual funds.
    - The MF rule set's equity caps still limit total equity.
16. Add `check_stock()` in [suitability.py](../../src/wealth_advisor/guardrails/suitability.py).
    - It applies the stock rule set's hard fails instead of the MF-only fields for `"Direct Equity"`.
    - `check_fund()` routes `"Direct Equity"` to it.
    - The existing `"Large Cap Equity"` branch stays, so the M13 catalog tests are unchanged.
17. Compliance:
    - Add a disclosure that direct equity carries concentration risk and that the output is not a recommendation from a SEBI-registered research analyst.
    - Run the existing `scrub()` (removes promotional wording) on all stock text.

### Phase E — API (depends on Phases B–D; changes to [main.py](../../src/wealth_advisor/api/main.py))

18. `AdviseRequest` changes:
    - Add `include_stocks: bool = False`, `stock_rule_set: Optional[Literal["set1","set2"]]`, `min_market_cap_cr: Optional[float]` and `min_traded_value_cr: Optional[float]`.
    - Both minimums must be ≥ 0 and are capped at a sane upper bound.
    - Each response row gets `instrument_type: "stock" | "mf"`. Stock rows also carry `symbol`, `sector`, `market_cap_cr` and the checks passed (e.g. `8/9`).
    - The response also carries `stock_filters_applied`, `stock_sync_as_of` and `stock_warnings`.
19. New endpoints:
    - **Sync:**
      - `POST /api/stocks/sync` — body `{min_market_cap_cr?, min_traded_value_cr?, force?, mode?: "full" | "retry_failed" | "rescreen"}`. Returns `202 {job_id}`, or `409` if a sync is running or one already finished today without `force`.
      - `GET /api/stocks/sync/status` — the latest run and any running job: `{status, done, total, failed, passed_set1, passed_set2, started, finished, min_market_cap_cr, min_traded_value_cr}`.
      - `GET /api/stocks/sync/{job_id}` — progress of one job.
    - **Data:**
      - `GET /api/stocks/search` — parameters `q`, `sector`, `index_tag?`, `only_passing?`, `rule_set?`, `limit`. It searches the synced universe and falls back to the bundled list for symbols never fetched.
      - `GET /api/stocks/sectors`
    - **Analysis:**
      - `POST /api/stocks/analyze` — body:
        - `symbol`, `stock_rule_set`, `narrative`, optional `min_market_cap_cr`
        - optional `overrides` (`fii_holding_pct`, `promoter_pledge_pct`)
        - optional `bank_metrics`
        - It reads the cache first. A symbol not in the cache is fetched live and then cached.
      - `POST /api/stocks/compare` — body `symbols: [a, b]`, `stock_rule_set`, `narrative`, optional `min_market_cap_cr`
      - `POST /api/stocks/{symbol}/refresh` — re-fetches one stock live
    - `GET /api/stocks/rules`
    - `GET /api/stocks/rules/compare`

    All of these write an audit log entry and a Langfuse trace context, as `/api/compare` does. Symbols are validated before any yfinance call: uppercase letters and digits plus `&` and `-`, at most 20 characters.

### Phase F — Frontend (depends on Phase E; steps 20–23 can run in parallel)

20. [App.tsx](../../frontend/src/App.tsx): add two views with nav buttons, using the existing `switchView()` / `pushState` routing.
    - `'compare-stocks'` at `/compare-stocks`
    - `'analyze-stock'` at `/analyze-stock`
21. Advise form:
    - Add an "Include direct stocks" toggle, stored in [useAppStore.ts](../../frontend/src/store/useAppStore.ts).
    - When the toggle is on, show:
      - a Stock Set 1 / Set 2 selector
      - an optional "Min market cap (₹ Cr)" field, with the hint "leave empty for no limit"
      - an optional "Min avg daily traded value (₹ Cr)" field
      - the last sync time
    - Show the API's warnings in the results: not synced, stale data, and sync minimum higher than the Advise minimum.
    - In the results table, stock rows show a "Stock" badge, the sector and the 10-pointer pass count (for example 9/10). The row action opens the Analyse Stock tab for that symbol.
    - Add a stock-sleeve slice to the allocation pie chart.
22. New components:
    - **`StockSyncPanel.tsx`**, placed next to the existing data-sync control. It contains:
      - a "Sync stocks" button
      - optional minimum market-cap and traded-value fields
      - a "force" option, enabled only when a sync already ran today
      - a progress bar, polled from `/api/stocks/sync/{job_id}`
      - a last-run summary, e.g. "2,014 in universe · 1,620 fetched · 12 failed · 140 pass Set 2"
      - "Retry failed" and "Re-screen" actions
    - **`StockCombobox.tsx`**, forked from [FundCombobox.tsx](../../frontend/src/components/FundCombobox.tsx), with sector and index-tag filters and an "only fundamentally good" filter.
    - **`StockCompare.tsx`**, forked from [FundCompare.tsx](../../frontend/src/components/FundCompare.tsx). It contains:
      - two stock pickers with a swap button
      - the criteria scorecard
      - the 10-pointer checklists side by side
      - a revenue/PAT trend bar chart
      - a normalised price line chart
      - a valuation table
      - the narrative
    - **`StockAnalyzer.tsx`**. It contains:
      - a header card with a "Fundamentally good" verdict badge and the data's "as of" date
      - an optional minimum market-cap field
      - the 10-pointer or 6-pointer checklist, with the market-cap row showing "not applied" when no minimum is given
      - ratio tables grouped by category, with the formula shown on hover
      - the CAGR table and the CFO/PAT table and chart
      - the fair-value range against the current price
      - a collapsible economy and industry context section
      - a collapsible section for entering bank metrics, FII holding and pledge by hand
      - the narrative with citations
23. [RulesViewer.tsx](../../frontend/src/components/RulesViewer.tsx): add a "Stock rules" tab that compares Set 1 and Set 2, using `/api/stocks/rules`.

### Phase G — Tests (can be written alongside Phases B and E)

24. New and updated tests:
    - **`tests/test_stock_ratios.py`**, checked against worked examples in the FA notes:
      - Infosys FY21 balance sheet: current ratio 60,733 / 23,865 ≈ 2.54
      - Infosys CFO/PAT table: 120% for 2021
      - Expert Academy statements: PBT 2,10,000 and PAT 1,89,000
    - **`tests/test_stock_rulesets.py`:**
      - 10-pointer and 6-pointer pass/fail, handling of missing data, and Set 1 matching today's scores
      - With no market-cap minimum: that check is marked "not applied" and the minimum evaluable counts become 6 of 9 and 4 of 5
      - With a minimum: the user's value is used in the check
    - **`tests/test_stock_sync.py`** (synthetic provider plus a fake yfinance provider):
      - a full run stores every company and the results for both rule sets
      - the optional minimums filter out only when set
      - a second run on the same day is refused unless `force`
      - running two syncs at once returns 409
      - a failed ticker keeps its previous data
      - "retry failed" reruns only the failures
      - "re-screen" makes no provider calls
      - fresh statements are not refetched
      - the latest-run pointer moves only when a run completes
    - **`tests/test_stock_valuation.py`:** the Infosys weighted valuation example (total 2,065.98, range up to 2,479.18).
    - **`tests/test_stock_compare.py`:** swapping the two stocks gives the mirrored result, results are deterministic, and the cross-sector warning appears.
    - **API tests** for every stock endpoint, using the synthetic provider and `ScriptedLLM`.
    - **Additions to `tests/test_m8_decision.py`:**
      - the stock sleeve stays within its cap
      - no stock exceeds 10% of the portfolio
      - Conservative profiles get no stocks
      - unused sleeve weight goes back to mutual funds
      - no sector exceeds 40% of the sleeve
      - output is unchanged when `include_stocks=False`
      - with no sync, the sleeve is skipped with a reason
      - the sync-minimum warning appears when the sync's minimum is higher than Advise's

---

## Relevant files

**Modified**
- [config.py](../../src/wealth_advisor/config.py): `stock_rule_set`, sleeve percentages, per-stock cap, `max_sector_pct_of_stock_sleeve`, cache lifetimes, `stock_stale_after_days`, `stock_sync_max_rps`, stock minimum horizon.
- [categories.py](../../src/wealth_advisor/domain/categories.py): `"Direct Equity"` in `MULTI_ASSET_CATEGORIES`.
- [store.py](../../src/wealth_advisor/data/store.py): stock cache tables and access methods.
- [pipeline.py](../../src/wealth_advisor/pipeline.py), [decision.py](../../src/wealth_advisor/agents/decision.py), [suitability.py](../../src/wealth_advisor/guardrails/suitability.py), [main.py](../../src/wealth_advisor/api/main.py), and `guardrails/disclosures.py`.
- [App.tsx](../../frontend/src/App.tsx), [useAppStore.ts](../../frontend/src/store/useAppStore.ts), [RulesViewer.tsx](../../frontend/src/components/RulesViewer.tsx).

**New**
- Backend data: `data/stocks/{provider,yfinance_provider,synthetic,universe,sync}.py`, `data/stocks/nse_equity_list.csv`
- CLI: `wealth-advisor stocks sync` and `wealth-advisor stocks refresh-universe` in [cli.py](../../src/wealth_advisor/cli.py)
- Backend logic: `domain/stock_{ratios,rulesets,valuation,compare,context}.py`
- Backend agents: `agents/stock_{narrator,researcher}.py`
- Frontend: `frontend/src/components/Stock{SyncPanel,Combobox,Compare,Analyzer}.tsx`

**Existing code reused as patterns:** `compare_funds()`, `CompareNarrator` (template fallback), `size_positions()`, `use_rule_set()` (request-scoped rule set), and `facts_for_fund()` (building citable facts).

---

## Verification

1. Run `pytest` in `ai-personal-wealth-manager`. All existing tests (about 104) and the new stock tests must pass.
2. Run `ruff check src tests` and `mypy src`. In `frontend/`, run `npm run lint` and `npm run build`.
3. Manual checks in synthetic mode:
   - Sync stocks: the job finishes instantly and the summary counts appear.
   - Advise with stocks on, Aggressive, 10 years, no market-cap minimum: at most 30% of equity goes to stocks, no single stock exceeds 10% of the portfolio, and no sector exceeds 40% of the sleeve.
   - The same request with a ₹50,000 Cr minimum: only stocks at or above that size appear.
   - The same request with a Conservative profile: no stock rows appear, and the research log shows why.
4. Manual checks in real mode:
   - First full sync with no minimums: progress updates, the run finishes, and failed tickers are listed.
   - A second sync the same day is refused unless forced.
   - Analyse INFY: all ratio groups are filled, the 10-pointer shows FII as "no data", a valuation range appears, and the narrative cites its facts.
   - Compare ICICIBANK vs SBIN: the 6-pointer is used, and bank metrics entered by hand change the score.
5. The audit log's hash chain still verifies after stock runs (`replay`).

---

## Scope

**Included:**
- Separate stock rule sets (Set 1 and Set 2). There is no Stock Combined set.
- The entire NSE `EQ` series, synced into SQLite by a background job run at most once a day. Advise reads only the cache; Analyse and Compare accept any NSE ticker.
- No market-cap limit unless the user enters one. The same applies to the minimum traded value.
- A stock sleeve sized inside the equity bucket, with per-stock and per-sector caps.

**Excluded:**
- Live macro data (only a dated snapshot is used).
- Scraping Screener.in or NSE. Only the bundled NSE equity list is used, with a best-effort refresh command.
- NSE SME platform and trade-for-trade (`BE`/`BZ`) series.
- A scheduled or automatic sync. The sync is triggered by the user.
- Live FII or pledge data (only values entered by hand).
- Quarterly results tables.
- Stocks in portfolio upload or daily drift monitoring.
- Changes to the existing M13 `EQUITY_CATALOG` and supervisor path.
