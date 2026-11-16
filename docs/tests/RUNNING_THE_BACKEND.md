# Running the Backend

This guide provides the steps to run the backend for the AI Personal Wealth Advisor.

## 1. Install Dependencies
Ensure you are in the root directory of the project (`ai-personal-wealth-manager/`) and have Poetry installed.
Install the dependencies (including the `llm` and `dev` extras for LangChain and Langfuse support):
```bash
poetry install --all-extras
```

## 2. Populate the Database
The system requires data in the local SQLite database to function correctly. Without data, you will encounter a `No fund data loaded` error.

- **Synthetic Demo Mode** (`data/wealth_synthetic.db`, 45–60 synthetic mutual funds + 20 synthetic 5-year NSE stock profiles pre-screened under Stock Rule Set 1 & Set 2):
  ```bash
  poetry run wealth-advisor seed-demo
  ```
- **Real Market Data Mode** (`data/wealth_real.db`, ~300 active Direct-Growth mutual funds + ~1,500+ open-ended AMFI catalog + 100 curated NSE equities via `yfinance`):
  ```bash
  # Sync AMFI scheme_master + official AMFI Monthly TER & Quarterly Average AUM
  poetry run wealth-advisor --mode real sync-schemes

  # Fetch 6-year historical NAVs from mfapi.in for the 60 curated funds + top 240 scheme_master Direct-Growth funds (15 per SEBI category) and compute fund_stats
  poetry run wealth-advisor --mode real seed-real --refresh-amfi

  # Sync NSE stock universe snapshots, 5Y financial statements, sector medians, and Set 1 / Set 2 screens
  poetry run wealth-advisor --mode real sync-stocks --sync-mode full
  ```

## 3. Configure the Environment
Ensure your `.env` file is set up with the necessary API keys for the LLM provider and (optionally) local Langfuse tracing:
```env
# Local Langfuse Configuration
LANGFUSE_SECRET_KEY="sk-lf-..."
LANGFUSE_PUBLIC_KEY="pk-lf-..."
LANGFUSE_BASE_URL="http://localhost:3000"

# LLM Provider Configuration
WEALTH_LLM_PROVIDER=openai
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://your-llm-gateway/api  # optional custom gateway

# Optional: disable on-disk LLM cache so every call hits the LLM and is traced in Langfuse
# WEALTH_LLM_CACHE_ENABLED=false
```

## 4. Start the Backend Server
Run the FastAPI application using `uvicorn`:
```bash
poetry run uvicorn wealth_advisor.api.main:app --reload --port 8000
```

The backend API will be available at `http://localhost:8000`. You can view the interactive API documentation at `http://localhost:8000/docs`.

## 5. Inspecting LLM Calls in Langfuse
If you are running the local Langfuse Docker container (from the `langfuse/` directory):
```bash
docker compose -f langfuse/docker-compose.yml up -d
```
You can inspect all LLM traces at `http://localhost:3000`:
- Every pipeline run groups its LLM calls (`wealth_advisor.profiling`, `wealth_advisor.mf_researcher`, `wealth_advisor.analyst`) under a single **Session** matching the `run_id`.
- **Note on LLM Caching**: By default, responses are cached in `data/llm_cache/`. Cached hits do not invoke the LLM or emit new Langfuse traces. To force live LLM calls and traces:
  - For CLI commands, pass `--no-cache`:
    ```bash
    poetry run wealth-advisor llm-ping --no-cache "Hello Langfuse"
    poetry run wealth-advisor advise -y --no-cache "Invest 2 lakh for 5 years with moderate risk"
    ```
  - For the FastAPI server, set `WEALTH_LLM_CACHE_ENABLED=false` in `.env` (or clear `data/llm_cache/`).

## 6. Viewing Logs
All application and server logs are automatically written to the `logs/` directory in the project root:
- **Backend API logs**: `logs/backend.log`
- **CLI command logs**: `logs/cli.log`

To follow backend logs in real time:
```bash
tail -f logs/backend.log
```

## 7. Real Data Mode, the ~300-Fund Advisory Universe & Troubleshooting

When running in **Real Data Mode** (`data_mode = "real"`, backed by `data/wealth_real.db`), fund data is stored across two complementary SQLite tables (see [`docs/mutual_fund_flow_and_schema.md`](../mutual_fund_flow_and_schema.md) for the complete architecture and schema):

1. **`scheme` (60 Curated Flagship Funds)**:
   - Populated from `data/curated_funds.csv` via `bootstrap_real()` / `seed-real`.
   - Carries hand-curated metadata not published in free AMFI feeds (`manager`, `manager_since`, debt `ytm`, `modified_duration`, `avg_maturity_years`, `rating`, and `benchmark`), as well as the benchmark proxy index funds used for Jensen's Alpha and Beta calculations.
2. **`scheme_master` (~1,500+ Open-Ended AMFI Catalog, Top 240 Pre-Seeded for Advice)**:
   - Populated from the live AMFI `NAVAll.txt` feed via `sync_scheme_master()`, and enriched with official AMFI Monthly TER (`amfi_ter.py`) and Quarterly Average AUM (`amfi_aum.py`).
   - During `seed-real` / `MfapiAmfiProvider.sync()`, `MetadataStore.eligible_master_funds(per_category_limit=15)` selects the top **15 Direct-Growth funds per SEBI category** (`15 × 16 = 240` additional funds not in `scheme`), fetches 6 years of daily NAVs from `mfapi.in`, and computes `fund_stats`.
   - `MetadataStore.query_funds(include_master=True)` merges both tables (`60 + 240 = ~300` active Direct-Growth funds) for the **Advisor** 3-Stage Quantitative Funnel (`MFResearchAgent`), while `search_master()` exposes all ~1,500+ open-ended schemes for **Fund Explorer** and **Compare**.
   - **Stage 1b On-Demand Hydration**: If any non-excluded SEBI category has `< 3` seeded funds during an `advise` run (or when a user inspects/compares an unseeded `scheme_master` fund), `ensure_nav()` automatically fetches its historical NAVs from `mfapi.in` on the fly and updates `fund_stats`.

### Troubleshooting Missing or Unseeded Funds

If searching for or advising on a real-world mutual fund (e.g., *Quant Small Cap Fund - Direct Plan - Growth*, AMFI `120828`) returns no results or outdated stats, check for these causes:

1. **`scheme_master` in `data/wealth_real.db` has not been synced yet (`0` rows)**:
   - Running `bootstrap_real()` without `refresh_amfi=True` (or without having run `sync-schemes`) only loads `data/curated_funds.csv` (60 funds) unless `scheme_master` is already populated.
   - **AMFI `NAVAll.txt` 8-Column Format Note**: Live AMFI feeds (`https://www.amfiindia.com/spages/NAVAll.txt`) use an 8-column semicolon-delimited header (`Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date`) rather than the legacy 6-column format. `parse_nav_all()` and `build_scheme_master()` in `src/wealth_advisor/data/amfi.py` support both 8-column and 6-column rows.
2. **`data/wealth_real.db` was seeded before the 240-fund `scheme_master` expansion**:
   - Switching modes via `POST /api/config/data-mode` only auto-seeds when `store.latest_nav_date() is None`. If `data/wealth_real.db` was created earlier with only 36 or 60 funds, re-run `sync-schemes` and `seed-real` (or click **Sync Data** in the UI) to populate all ~300 active Direct-Growth funds.

### How to Sync & Refresh Real Data

- **Sync the full AMFI catalog + TER + AUM (`scheme_master`) and seed all ~300 Direct-Growth NAV histories (`nav` + `fund_stats`) via CLI**:
  ```bash
  poetry run wealth-advisor --mode real sync-schemes
  poetry run wealth-advisor --mode real seed-real --refresh-amfi
  ```
- **Or trigger a full sync via the running FastAPI backend**:
  ```bash
  curl -X POST http://127.0.0.1:8000/api/data/sync \
    -H "Content-Type: application/json" \
    -d '{"data_mode": "real", "refresh_master": true, "refresh_ter": true, "refresh_aum": true, "refresh_amfi": true}'
  ```
- **Add a custom fund with rich manager/debt metadata to `scheme`**:
  1. Add the scheme row to `data/curated_funds.csv`.
  2. Run `poetry run wealth-advisor --mode real seed-real` to fetch its 6-year NAV series from `mfapi.in` and recompute `fund_stats`.

## 8. Stock Fundamental Analysis (CLI & API Reference)

The backend includes a full **Stock Fundamental Analysis** engine grounded in *Fundamental Analysis Course Notes* (`docs/plans/FA-Course-Notes.md`), supporting **Daily Stock Sync**, **Stock Rule Set 1 (Equity Heuristics)**, **Stock Rule Set 2 (FA Course Notes 10-Pointer Non-Bank & 6-Pointer Bank Screen)**, **Weighted Relative Valuation**, **Head-to-Head Stock Comparison (2–4 stocks)**, **Single-Stock Deep Dive**, and an optional **Direct Equity Sleeve** inside `advise`.

### CLI Commands

1. **Sync NSE Stocks (`sync-stocks`)**:
   ```bash
   # Full sync (idempotent same-day check; pass --force to re-run today)
   poetry run wealth-advisor sync-stocks --sync-mode full --force

   # Sync with optional Market Cap (₹ Cr) and Daily Traded Value (₹ Cr) pre-filters
   poetry run wealth-advisor sync-stocks --sync-mode full --force --min-market-cap-cr 50000 --min-traded-value-cr 25

   # Re-compute sector medians and Set 1 / Set 2 screens on already-cached financials without network calls
   poetry run wealth-advisor sync-stocks --sync-mode rescreen --min-market-cap-cr 50000

   # Retry only failed or missing symbols from the last sync run
   poetry run wealth-advisor sync-stocks --sync-mode retry_failed
   ```

2. **List & Filter Screen Results (`stocks`)**:
   ```bash
   poetry run wealth-advisor stocks --rule-set set2 --passed-only
   poetry run wealth-advisor stocks --rule-set set1 --sector "Information Technology"
   ```

3. **Single-Stock Fundamental Analysis (`analyze-stock`)**:
   ```bash
   # Default Set 2 (9-pointer screen; or 10-pointer when --min-market-cap-cr is provided)
   poetry run wealth-advisor analyze-stock TCS --rule-set set2
   poetry run wealth-advisor analyze-stock TCS --rule-set set2 --min-market-cap-cr 50000
   poetry run wealth-advisor analyze-stock HDFCBANK --rule-set set2
   ```

4. **Head-to-Head Stock Comparison (`compare-stocks`, 2–4 symbols)**:
   ```bash
   poetry run wealth-advisor compare-stocks TCS INFY WIPRO --rule-set set2
   poetry run wealth-advisor compare-stocks HDFCBANK ICICIBANK --rule-set set2
   ```

5. **Inspect Stock Screening & Comparison Rules (`stock-rules`)**:
   ```bash
   poetry run wealth-advisor stock-rules --rule-set all
   ```

6. **Run Goal Advisory with Direct Equity Stock Sleeve (`advise --include-stocks`)**:
   ```bash
   poetry run wealth-advisor advise -y --include-stocks --stock-rule-set set2 \
     "Invest 5 lakh for 7 years with moderate risk"
   ```

### REST API Endpoints

- `POST /api/stocks/sync` — Trigger a background or synchronous stock sync (`mode`: `"full" | "retry_failed" | "rescreen"`, optional `min_market_cap_cr`, `min_traded_value_cr`, `sector`, `index_tag`, `force`).
- `GET /api/stocks/sync/status` & `GET /api/stocks/sync/{job_id}` — Poll sync status, freshness (`is_stale`, `already_synced_today`), and pass counts for Set 1 and Set 2.
- `GET /api/stocks` — List/search synced stocks with filtering by `q`, `sector`, `rule_set` (`set1` | `set2`), `passed_only`, `min_market_cap_cr`, and `min_traded_value_cr`.
- `GET /api/stocks/rules` & `GET /api/stocks/rules/compare` — Retrieve the structured Set 1 / Set 2 screening catalog, valuation weights, and 18-metric Non-Bank / 14-metric Bank comparison definitions.
- `POST /api/stocks/analyze` & `GET /api/stocks/analyze/{symbol}` — Run a single-stock fundamental analysis (5Y statements, ratios, DuPont breakdown, weighted relative valuation, Set 1 & Set 2 checklists, sector peers, EIC context, and grounded analyst memo).
- `POST /api/stocks/compare` — Run a 2–4 stock head-to-head comparison (`symbols: ["TCS", "INFY", "WIPRO"]`).
- `POST /api/stocks/refresh/{symbol}` — Force on-demand refresh of a single stock's snapshot, financials, and price history.
- `POST /api/advise` — Supports `"include_stocks": true`, `"stock_rule_set": "set2"`, `"min_market_cap_cr"`, and `"min_traded_value_cr"` to carve out a risk-calibrated Direct Equity sleeve (`0%` Conservative, `20%` of Equity for Moderate, `30%` of Equity for Aggressive when `horizon_years >= 5`, capped at `<= 10%` per stock and `<= 25%` per sector).

## 9. 360° Stock Analysis Report (On-Demand)

An on-demand, 18-section **360° report** (Step 0 + S1–S17 of the Master Prompt template) for any NSE stock. Full design: [`docs/stock-360-report.md`](../stock-360-report.md) and the plan [`docs/plans/stock-360-analysis-plan.md`](../plans/stock-360-analysis-plan.md).

### What it needs
- **No extra install or seed step.** The report's data (2y daily prices, 8 quarters of results, Nifty series) is fetched **only by the 360° job**, never by the daily `sync-stocks`.
- **Synthetic mode** (`seed-demo`): works offline and without an LLM key. It produces a **mechanical-only** report (all 18 sections, web-dependent items shown as *Not available / Requires live verification*). Web research is never used in synthetic mode.
- **Real mode**: needs network access plus an LLM key (`OPENAI_API_KEY` or the Anthropic equivalent). The narrative parts (4 LLM calls) and web evidence (OpenAI/Anthropic web search, or RSS) use the same provider as `WEALTH_LLM_PROVIDER` when `WEALTH_STOCK360_WEB_SEARCH_PROVIDER=auto`.
- Without an LLM key in real mode, the report falls back to mechanical text and is marked **partial**.

### Optional `.env` settings (all prefixed `WEALTH_`, defaults shown)
```env
WEALTH_STOCK360_REPORT_TTL_HOURS=24            # cached report reused within this window
WEALTH_STOCK360_WEB_TTL_HOURS=24
WEALTH_STOCK360_MAX_LLM_CALLS=8
WEALTH_STOCK360_MAX_WEB_SEARCHES=14
WEALTH_STOCK360_MAX_PARALLEL_JOBS=2            # a 3rd concurrent job gets HTTP 409
WEALTH_STOCK360_WEB_SEARCH_PROVIDER=auto       # auto | openai | anthropic | rss | none
WEALTH_STOCK360_VETO_ENABLED=false             # Advise quant veto, off by default
WEALTH_STOCK360_VETO_MIN_SELL_SIGNALS=5
# Valuation parameters: WACC, TERMINAL_GROWTH, BUFFETT_HURDLE, BUFFETT_EXIT_PE_CAP,
# GRAHAM_MULTIPLIER, CASH_CONVERSION_FLAG (all WEALTH_STOCK360_*)
```

### REST API
Generation is asynchronous (poll the job), mirroring the stock sync pattern.

| Endpoint | Purpose |
|---|---|
| `POST /api/stocks/{symbol}/report360` | Body `{force?, run_id?, context?, horizon_years?, risk_category?, holding?, report360_inputs?}`. Returns the cached report, or **202** `{job_id, report_id}`. **409** if a job for that symbol is running with different inputs or the parallel-job limit is hit. |
| `GET /api/stocks/report360/jobs/{job_id}` | Status, progress (0–100), message, error. Jobs left `running` at restart are marked failed. |
| `GET /api/stocks/report360/{report_id}` | Full `Report360` JSON (`sections`, `mechanical`, `final`, `sources`, `warnings`, `disclosures`, `charts`). |
| `GET /api/stocks/{symbol}/report360/latest` | Latest stored report for the symbol and data mode. |
| `GET` / `PUT /api/stocks/{symbol}/manual-inputs` | Manual overrides, each needing `as_of` (optional `source_url`). Shown as **Manual** in the report. |
| `GET /api/history/{run_id}` | Now also returns `reports360`: 360° reports linked to that Advise run. |

```bash
curl -X POST http://127.0.0.1:8000/api/stocks/TCS/report360 \
  -H "Content-Type: application/json" -d '{"force": false, "context": "analyzer"}'
curl http://127.0.0.1:8000/api/stocks/report360/jobs/<job_id>
curl http://127.0.0.1:8000/api/stocks/report360/<report_id>
```

**Manual input fields** (validated, 422 on bad values): `promoter_pct`, `pledge_pct`, `fii_pct`, `dii_pct`, `public_pct` (0–100), `order_book_cr`, `contingent_liabilities_cr`, `goodwill_cr`, `consensus_eps_fy1`, `consensus_eps_fy2`, `buy_count`, `hold_count`, `sell_count`, `target_price_mean`, `industry_cagr_pct`, `tam_cr`, `guidance` (text, ≤1000 chars) and `shareholding_history` (1–12 quarter rows with `quarter` + promoter/fii/dii/public/pledge %). They can also be sent nested as `overrides.report360_inputs` in `POST /api/stocks/analyze` (and `overrides_a` / `overrides_b` in compare); they are saved per symbol.

### Advise integration
- `POST /api/advise` accepts `stock360_veto` (bool) and `stock360_min_sell_signals` (1–10). Stock rows carry `report360` (`{action, stars}`) **only when a fresh cached report exists**; Advise never generates a report itself.
- **Veto** (off by default) uses the numbers only (no LLM, no web). It drops a stock when the mechanical action is SELL with at least N legend sell signals (a fresh full report's verdict overrides). The slot is filled by the next-ranked stock, a warning is added, and weights still respect the per-stock cap.
- When any badge appears, the report disclosure is added to the response.

### Observability
- Each report writes an audit-log entry (`stock_360` / `generate`).
- Langfuse traces use the steps `stock360_part1` … `stock360_part4`, grouped under the `run_id` (or job id). Set `WEALTH_LLM_CACHE_ENABLED=false` to force live calls.

### Tests
```bash
poetry run pytest tests/test_stock360_*.py -q   # 360° suites
poetry run pytest -q                            # full suite (369 tests)
```
