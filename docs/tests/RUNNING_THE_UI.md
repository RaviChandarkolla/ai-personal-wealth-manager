# Testing the UI

This document provides instructions on how to run and test the React frontend UI alongside the FastAPI backend.

## Prerequisites

Ensure you have the following installed on your machine:
- Node.js and npm (for the Vite React frontend)
- Python 3.11+ and Poetry (for the FastAPI backend)

## 1. Start the Backend Server

The React frontend connects to the FastAPI backend (`http://localhost:8000/api`) for live advisory pipelines, SQLite fund queries, portfolio valuation, and grounded Q&A. You must start the backend first.

1. Open a terminal.
2. Navigate to the root of the project directory (`ai-personal-wealth-manager`).
3. Ensure the database is seeded (see [`RUNNING_THE_BACKEND.md`](./RUNNING_THE_BACKEND.md) for seeding synthetic vs. real data).
4. Run the backend server using Poetry:
   ```bash
   poetry run uvicorn wealth_advisor.api.main:app --reload --port 8000
   ```
5. Wait for the output: `Application startup complete.` The API will be available at `http://127.0.0.1:8000`.

## 2. Start the Frontend Server

Once the backend is running, start the Vite development server for the UI.

1. Open a new terminal window.
2. Navigate to the `frontend` directory inside the project:
   ```bash
   cd frontend
   ```
3. (First time only) Install the npm dependencies:
   ```bash
   npm install
   ```
4. Start the frontend development server:
   ```bash
   npm run dev
   ```
5. You should see output indicating Vite is running, usually at `http://localhost:5173/`.

## 3. Interact with the UI

Open your browser and navigate to `http://localhost:5173/`.

### What to test:

1. **Data Mode & Rule Set Controls (Header Bar)**
   - Toggle between **Synthetic (`DEMO`)** (45–60 synthetic mutual funds + 20 synthetic 5-year NSE stock profiles in `data/wealth_synthetic.db`) and **Real (`AMFI + mfapi.in + NSE/yfinance`)** (~300 active Direct-Growth funds + 100 curated NSE equities in `data/wealth_real.db`).
   - Click **Sync Data** in Real mode to refresh `scheme_master`, TER, AUM, and historical NAVs.
   - Switch between **Rule Set 1**, **Rule Set 2**, and **Combined** (or open the **Rules Catalog** modal) to inspect how mutual fund and stock screening rules, valuation weights, and comparison matrices work.
   - Inside the **Rules Catalog** modal, switch across the 4 tabs:
     - **Fund Selection** (Mutual Fund Rule Set 1, Set 2, Combined)
     - **Fund Comparison** (6-Dimension Mutual Fund Compare rubric)
     - **Stock Screening** (Stock Set 1 Equity Heuristics vs. Stock Set 2 *FA Course Notes* 10-Pointer Non-Bank & 6-Pointer Bank screens + Weighted Relative Valuation formula)
     - **Stock Comparison** (18-metric Non-Bank & 14-metric Bank head-to-head comparison matrices)

2. **Goal Entry, 3-Stage MF Advisory & Optional Direct Equity Sleeve (`Advise Me`)**
   - Enter a natural-language goal, select presets (Amount, Horizon, Risk Preference, Excluded Categories), or use a hybrid of both—e.g., `"I have ₹5,00,000 to invest for 7 years with growth risk"`.
   - **Optional Direct Stocks Sleeve**:
     - Toggle **Include Direct Stocks (NSE)** on in the Goal Input card.
     - Choose between **Set 2 · FA Course Notes (10-Pt / 6-Pt Bank)** (default) and **Set 1 · Equity Heuristics**.
     - Optionally set **Min Market Cap (₹ Cr)** (activates Criterion #10 in Set 2) and **Min Daily Traded Value (₹ Cr)**, and use the **Stock Sync Panel** (`Sync Stocks` / `Retry Failed` / `Re-screen`) to inspect or refresh synced NSE stock data.
     - Note: Direct stocks are automatically skipped (with an explanatory banner) if the risk profile is `Conservative` (`0%` stock sleeve) or the goal horizon is `< 5 years`, reallocating 100% of the Equity bucket to Mutual Funds.
   - Click **Advise Me** (`POST /api/advise`).
   - Verify:
     - **Risk Profile & Target Allocation** pie chart across Equity, Debt, Hybrid, and Gold buckets.
     - **Recommended Portfolio** table showing both Mutual Funds (`MF`) and Direct Equity picks (`STOCK` badge with sector, market cap, and `passed/evaluated` checklist badge, capped at `<= 10%` per stock and `<= 25%` per sector).
     - Click any fund row to open the **Fund Detail View**, or click **Analyse Stock** on any `STOCK` row to jump directly to the single-stock fundamental deep dive.
     - **Disclosures & Escalations** section at the bottom (including the SEBI direct equity concentration and educational disclosure when stocks are recommended).

3. **Head-to-Head Stock Comparison (`Compare Stocks`)**
   - Open **Compare Stocks** from the top navigation bar.
   - Select **2 to 4 NSE stocks** using the searchable stock combobox (or click quick presets like *IT Leaders: TCS vs INFY vs WIPRO*, *Private Banks: HDFCBANK vs ICICIBANK vs KOTAKBANK*, or *FMCG Giants: HINDUNILVR vs ITC*).
   - Switch between **Set 2 (FA Course Notes)** and **Set 1 (Equity Heuristics)**, and optionally set **Min Market Cap (₹ Cr)** or **Min Traded Value (₹ Cr)**.
   - Click **Compare Stocks** (`POST /api/stocks/compare`) and verify:
     - **Overall Winner Banner** & per-stock **Screen Pass/Fail + Composite Score + Fair Value Range** summary cards (plus a cross-sector warning banner if comparing across different industries or mixing banks with non-banks).
     - **Head-to-Head Criteria Matrix** (18 criteria for Non-Banks or 14 criteria for Banks) with per-row green `WIN` / `TIE` badges.
     - **Weighted Relative Valuation Cards** showing each stock's CMP, implied fair value range (`±10%`), margin of safety %, and Undervalued / Fairly Valued / Overvalued badge.
     - **5-Year Normalized Price Chart (Base 100)** and **Revenue vs. PAT 5Y CAGR Bar Chart**.
     - **AI Fundamental Comparison Narrative** grounded in the computed ratios.

4. **Single-Stock Fundamental Deep Dive (`Analyse Stock`)**
   - Open **Analyse Stock** from the top navigation bar (or click **Analyse Stock** from a recommendation or comparison card).
   - Search and select any NSE stock (e.g., `TCS`, `HDFCBANK`, `RELIANCE`, `MARUTI`, `DHFLTRAP`) and click **Analyse Stock** (`POST /api/stocks/analyze`).
   - Explore the **6 sub-tabs**:
     - **Overview & Checklist**: Pass/Fail status under Set 1 & Set 2, composite score & 4 sub-scores (Quality, Growth, Valuation, Safety), governance/solvency red-flag alerts, and the full criterion-by-criterion checklist table (showing `PASSED`, `FAILED`, `MISSING`, or `NOT_APPLIED` for optional filters).
     - **Financial Statements (5Y)**: Multi-year P&L, Balance Sheet, Cash Flow, and YoY growth table (`₹ Cr`).
     - **Ratios & DuPont**: Grouped ratio cards (Profitability, Growth, Leverage & Solvency, Working Capital & Efficiency, Cash Flow Quality, Banking Metrics) with formula tooltips and 3-step DuPont ROE decomposition ($\text{Net Profit Margin} \times \text{Asset Turnover} \times \text{Equity Multiplier}$).
     - **Valuation**: Weighted Relative Valuation breakdown table (Stock Multiple vs. Sector Median Multiple, Implied Price, Weight, and Fair Value Range `±10%`).
     - **Sector Peers**: Comparable stocks in the same NSE sector with their screening status, ROE, P/E, and composite scores.
     - **EIC Context**: Economy (India macro indicators), Industry (Porter's 5 Forces + sector medians), and Company (5Cs of Credit/Quality).
   - Test **What-If Overrides**: Expand the overrides drawer to supply custom `FII Holding %`, `Promoter Pledge %`, or Bank metrics (`GNPA %`, `NNPA %`, `CASA %`, `NIM %`, `CAR %`) and re-run the screen dynamically.

5. **Grounded Follow-Up Q&A Chat & History Sidebar**
   - After an advice run completes, use the **Follow-Up Chat** panel (`POST /api/runs/{run_id}/chat`) to ask questions such as *"Why was this fund picked over other Small Cap funds?"* or *"Why were other candidates rejected?"*.
   - Responses are strictly grounded in the run's saved `advice_context` and candidate facts (`scheme` + `scheme_master`).
   - Use the **History Sidebar** to reload previous runs and their conversation threads.

6. **Fund Explorer & Head-to-Head Fund Compare**
   - Open **Fund Explorer** or **Compare Funds** from the top navigation.
   - Search across all **~1,500+ open-ended AMFI schemes** in `scheme_master` (powered by SQLite FTS5).
   - Selecting any unseeded scheme automatically fetches its 6-year daily NAV history from `mfapi.in` on demand (`ensure_nav()`) and computes side-by-side return, risk, and cost comparisons.

7. **Portfolio Upload, X-Ray & Daily Recalibration**
   - Open the **Portfolio** view and upload a holdings `.csv` (or add holdings manually).
   - Click **Analyze Portfolio** (`POST /api/portfolio/upload`) to valuate holdings at the latest NAV, inspect **5/25 allocation drift**, view exit-load/STCG/LTCG tax warnings, and trigger the **08:00 IST Daily Recalibration** (`HOLD` / `REBALANCE` / `ESCALATE`).

8. **Inspecting UI LLM Traces in Langfuse (Optional)**
   - Ensure the Langfuse Docker stack is running (`docker compose -f langfuse/docker-compose.yml up -d`) and open `http://localhost:3000`.
   - When you click **Advise Me**, **Compare Stocks**, or **Analyse Stock** in the UI, all LLM calls for that request are traced and grouped under a Langfuse **Session** matching the `run_id`.
   - If you repeat the exact same query in the UI and want it to hit the LLM again instead of using `data/llm_cache/`, set `WEALTH_LLM_CACHE_ENABLED=false` in `.env` before starting the backend server.


9. **360° Stock Report (On-Demand)**
   - **Where to open it**:
     - **Analyse Stock** → the **360° Report** tab next to *Analysis* (inline, full-height panel).
     - **Advise Me** → the **360° Report** button on every `STOCK` row (drawer). A verdict badge (e.g. `BUY · 4.0★`) appears only if a fresh report is already cached.
     - **Compare Stocks** → one **360° Report** button per compared symbol (drawer).
     - **History Sidebar** → reload an Advise run; a **360° reports saved for this run** strip lists its reports and reopens each one without regenerating.
   - **Generate**: click **Generate 360° report**. A progress bar and status message follow the job (about a minute in Real mode; seconds in Synthetic mode). Closing and reopening the drawer for the same stock resumes the running job.
   - **Synthetic mode** needs no LLM key and gives a *mechanical-only* report. Verify: all 18 sections (Step 0 + S1–S17) are present, web-dependent items read *Not available / Requires live verification*, and nothing is guessed. A *Partial report* note appears if narrative text is mechanical only.
   - **Verify the report**:
     - Verdict header (action, stars, quant score, sell signals, as-of and generated times) and warnings.
     - Part navigation on the left (Parts 1–4), then each section with metrics. Metric chips: **Verified**, **Computed**, **Estimated**, **Manual**, **Not available**, **Requires live verification**. Hover a metric for formula, inputs, source and date.
     - **Charts**: price with EMA 20/50/200 and support/resistance lines, P/E band, return decomposition, analyst consensus (only when data exists), intrinsic value vs CMP, and long-term targets.
     - Tables such as *Income statement trend*, *Intrinsic value methods vs CMP*, *Entry plan*, *Legend sell triggers* and *Tax illustration*, then **Web sources** (Real mode) and the educational disclosure footer.
   - **Manual inputs**: expand **Manual inputs** in the report. Enter consensus EPS FY+1/FY+2, analyst Buy/Hold/Sell counts and mean target, industry CAGR, TAM, guidance, order book, shareholding history (**+ Add quarter**, up to 12 rows) and so on. An **As of** date is required. Click **Save & regenerate**; values then show as **Manual**. Out-of-range values (e.g. a percentage above 100) show a validation error.
   - **Regenerate** forces a fresh report; otherwise a report younger than 24 hours is reused.
   - **Optional Advise veto**: in the Direct Stocks controls, tick the 360° veto checkbox and set **min sell signals** (off by default). Re-run **Advise Me**: only flagged stocks drop, a warning names them, and the freed slot goes to the next-ranked stock (total weight unchanged). With it off, Advise output is unchanged.
   - **Langfuse (Optional)**: generating a report in Real mode traces `stock360_part1` … `stock360_part4` under the run/job id.
