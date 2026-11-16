# UI Testing Guide: Phase C (M10–M11) & Phase D (M12–M15)

Reference plan: [plan-by-claude.md](../plans/plan-by-claude.md)

To test **Phase C (M10–M11)** and **Phase D (M12–M15)** from the UI, use a combination of the **React Web UI** (`http://localhost:5173`) and the **FastAPI Interactive Swagger UI** (`http://localhost:8000/docs`) for backend endpoints that do not yet have dedicated buttons in the React dashboard.

---

## Prerequisite: Start the Backend and React UI

1. **Terminal 1 — Start the FastAPI backend** (from `ai-personal-wealth-manager/`):
   ```bash
   poetry run wealth-advisor seed-demo
   poetry run uvicorn wealth_advisor.api.main:app --reload --port 8000
   ```
2. **Terminal 2 — Start the React frontend** (from `ai-personal-wealth-manager/frontend/`):
   ```bash
   npm install
   npm run dev
   ```
3. Open in your browser:
   - **React App:** `http://localhost:5173`
   - **FastAPI Swagger UI:** `http://localhost:8000/docs`

---

## Testing Phase C — MVP+: The Daily Loop (M10–M11)

### 1. M11 — Portfolio Upload (CSV) & 5/25 Drift (`frontend/src/components/PortfolioUpload.tsx`)

1. **Create a sample `portfolio.csv` file** on your computer:
   ```csv
   scheme_code,units,avg_cost,as_of
   DEMO_LC_01,1500,95.50,2025-01-10
   DEMO_LIQ_01,200,100.00,2025-01-10
   INVALID_CODE,100,50.00,2025-01-10
   ```
2. In the React UI (`http://localhost:5173`), scroll to the **"Upload Existing Portfolio"** card.
3. Drag and drop `portfolio.csv` and click **Analyze Portfolio**.
4. **What to verify in the UI:**
   - The green banner shows the **active holdings count** (`2`) and **total portfolio valuation** (`₹...`) computed from the latest NAV.
   - The yellow **Warnings** box lists row-level validation errors (e.g., `Row 4 (scheme_code): Unknown scheme/ISIN 'INVALID_CODE'`).
   - Open your browser's **DevTools → Network tab → `upload` response** (or test `POST /api/portfolio/upload` in `http://localhost:8000/docs`) to inspect the full `drift` object (`abs_drift`, `rel_drift`, `breached` per the 5/25 rule, rupee rebalance `trades` with exit-load warnings, and incremented `profile_version`).

---

### 2. M10 — Daily Recalibration Cycle (`HOLD` / `REBALANCE` / `ESCALATE`) (`src/wealth_advisor/api/main.py`)

1. **Step 1 — Establish an Active Position in the React UI:**
   - On `http://localhost:5173`, choose **₹5L**, **5Y**, **Moderate** risk, and click **Advise Me**.
   - This runs the pipeline and saves the initial active position.
   - If any data is missing or confidence is below threshold, the red **Action Center - Attention Required** panel appears at the bottom with the **Escalate to Human Advisor** button.
2. **Step 2 — Trigger the 08:00 IST Daily Run from Swagger UI (`http://localhost:8000/docs`):**
   - Open `POST /api/daily/run` → **Try it out** → Execute with:
     ```json
     {
       "force": false,
       "auto_confirm": false
     }
     ```
   - **Verify:** Since NAV data hasn't materially changed, `outcome` returns `"HOLD"` with `"cache_hit": true` (silent log: *"checked, nothing changed"*).
3. **Step 3 — Simulate 30 Days of Daily Recalibration (`POST /api/daily/simulate`):**
   - In `http://localhost:8000/docs`, open `POST /api/daily/simulate` → **Try it out** → Execute with:
     ```json
     {
       "days": 30,
       "auto_confirm": true
     }
     ```
   - **Verify:** The response shows the 30-day breakdown (`hold_count`, `rebalance_count`, `escalate_count`, `cache_hits`, and daily conviction deltas/drift checks).
4. **Step 4 — Confirm a Pending Rebalance (`POST /api/daily/confirm`):**
   - When a daily run returns `REBALANCE` with `"auto_confirm": false`, execute `POST /api/daily/confirm` with `{}` to promote the pending rebalance into the active position.

---

## Testing Phase D — V2: Breadth (M12–M15)

### 1. M12 — Supervisor Fan-Out & Capacity Gating (`frontend/src/App.tsx`)

1. In the React UI (`http://localhost:5173`), select the **Structured + AI Notes (Hybrid)** tab:
   - Set **Horizon (Years)** to `0.5` (6 months) and **Risk Preference** to `Conservative`.
   - Click **Exclusions & Capacity** and set **Emergency Buffer (Months)** to `2` (< 3 months).
   - Click **Advise Me**.
2. **What to verify in the UI:**
   - Check the **Target Allocation** pie chart and **Recommended Portfolio** table: equity is capped/skipped for a 6-month conservative goal, and only Debt/Liquid buckets are researched and recommended.

---

### 2. M13 — Multi-Asset / News & Sentiment Agents (`frontend/src/App.tsx`)

1. **Toggle Synthetic vs. Real Data Mode:**
   - In the top header of the React UI, switch **Data** between **Synthetic** and **Real** (and click **Sync** to pull live AMFI / `mfapi.in` NAVs), or switch **Rules** between **Set 1 · Default**, **Set 2 · Magic MF**, and **Combined · 1 + 2**.
2. **Verify News & Sentiment Sub-Scores (`13e` & `13f`):**
   - Run **Advise Me** (e.g., ₹5L, 7Y, Growth).
   - In the **Recommended Portfolio** table, look under the **Score** column to see the inline News (`N:xx`) and Sentiment (`S:xx`) sub-scores.
   - Scroll down to the **"Real-Time News & Market Context"** section to inspect:
     - **Macro & Regulatory Backdrop (SEBI • RBI • AMFI)** cards with `positive`/`neutral`/`negative` sentiment badges and `high`/`medium`/`low` materiality tags.
     - **Recommended Funds — News & Sentiment Breakdown** showing per-fund positive/neutral/negative counts and headlines.
3. **Inspect Full Dossier & Citations (`frontend/src/components/FundDetailView.tsx`):**
   - Click any fund name in the **Recommended Portfolio** table to open `/fund/<code>` in a new tab.
   - Verify the 5 sub-score bars (**Fundamental 35%**, **Fit 25%**, **News 20%**, **Sentiment 10%**, **Liquidity & Cost 10%**), NAV chart, RAG documents, and cited `Fact`s.
4. **Compare Funds Head-to-Head:**
   - Click **Compare Funds** in the top navigation bar to compare two schemes across performance, risk, and news/sentiment.

---

### 3. M14 — Document Ingestion (Bank Statements, CAS & Confirmation Queue) (`src/wealth_advisor/api/main.py`)

Open Swagger UI (`http://localhost:8000/docs`) and test the M14 ingestion endpoints:

1. **Upload a Bank CSV + CAS Statement (`POST /api/statements/upload`):**
   - Click **Try it out** on `POST /api/statements/upload` and paste:
     ```json
     {
       "profile_id": "default_user",
       "statement_type": "bank_csv",
       "content": "date,description,amount,type,balance\n2025-01-01,NEFT SALARY ACME CORP PAN ABCDE1234F,120000,CREDIT,250000\n2025-01-05,HOUSE RENT TRANSFER,-30000,DEBIT,220000\n2025-01-10,NACH SIP HDFC FLEXI CAP,-15000,DEBIT,205000\n2025-01-15,UPI MISC TRANSFER,-25000,DEBIT,180000",
       "retention_days": 30,
       "goal_amount": 500000,
       "horizon_years": 5,
       "risk_preference": "moderate"
     }
     ```
   - **Verify in the response:**
     - `"encrypted_at_rest": true` and `expires_at` timestamp.
     - `profile.derived_from_statements` shows auto-computed `monthly_income`, `monthly_expenses`, `savings_rate`, `emergency_fund_months`, and updated `risk_score` + `version`.
     - `pending_confirmations` lists low-confidence/ambiguous transactions (such as `UPI MISC TRANSFER`) queued for user confirmation.
2. **Confirm a Low-Confidence Transaction (`GET /api/statements/confirmations` & `POST /api/statements/confirm`):**
   - Copy the `item_id` from `pending_confirmations`.
   - Call `POST /api/statements/confirm` with:
     ```json
     {
       "item_id": "<paste-item_id-here>",
       "confirmed_category": "sip"
     }
     ```
   - Verify the item status transitions to `"CONFIRMED"`.

---

### 4. M15 — Event-Driven Monitoring, Alert De-Duplication & PII/Auth Middleware (`src/wealth_advisor/api/main.py`)

1. **Test PII Redaction in Follow-Up Chat (React UI or Swagger UI):**
   - After running **Advise Me** on `http://localhost:5173`, scroll down to the **Follow-Up Chat** panel (`frontend/src/components/FollowUpChat.tsx`), or call `POST /api/chat` in `http://localhost:8000/docs` with:
     ```json
     {
       "message": "My PAN is ABCDE1234F and account is 123456789012. I want to invest ₹1,00,000 for 5 years with moderate risk."
     }
     ```
   - **Verify:** The response header contains `x-pii-redacted: true`, `redacted_message` replaces the PAN and account number with `[REDACTED_PAN]` and `[REDACTED_ACCOUNT]`, and `pii_redacted` lists `["pan", "account_number"]`.
2. **Simulate a Rating Downgrade & Verify Alert De-Duplication (`POST /api/alerts/events`):**
   - In `http://localhost:8000/docs`, open `POST /api/alerts/events` → **Try it out** → Execute **twice in a row** with the same payload:
     ```json
     {
       "topic": "fund_events",
       "event_type": "rating_downgrade",
       "profile_id": "default_user",
       "instrument_id": "DEMO_LC_01",
       "payload": {
         "old_rating": 5,
         "new_rating": 2
       }
     }
     ```
   - **Verify:**
     - **1st call:** `"alert_emitted": true`, `"alerts_count": 1`, and `"recalibration_runs"` contains the triggered out-of-cycle supervisor run.
     - **2nd call (within cooldown):** `"alert_emitted": false`, `"alerts_count": 1` (unchanged), and `"suppressed_duplicates": 1` — proving alert de-duplication works.
