# Plan: Fund Explorer Tab + Per-Fund Live Refresh

Add a third top-level tab, **Fund Explorer** (`/explore?code=X`), next to Advisor and Compare Funds. The user picks one fund and sees everything the app knows about it. A **Fetch latest** button pulls fresh data for that fund from the live sources (mfapi.in, AMFI, news RSS) and saves it to the DB, with a refresh log.

Two things already exist and get reused: the standalone `/fund/{code}` page (`frontend/src/components/FundDetailView.tsx`) and `GET /api/funds/{code}`. The work is mainly to embed that page in the new tab, add a single-fund refresh, and fill in the details the Compare page shows but the detail page doesn't.

---

## 1. Design Options and Selections

These options were presented during planning. The selected choices are marked **✅ Selected**.

### 1.1 UX placement: how is the single-fund view exposed?

| Option | Status |
|---|---|
| New top-level tab "Fund Explorer" (`/explore?code=X`) next to Compare Funds, reusing the FundDetailView body plus a fund picker and a Refresh button | ✅ Selected (recommended) |
| Only add a Refresh button to the existing standalone `/fund/{code}` page, plus a "View details" link from Compare | ❌ Not selected |
| Both: the new tab, plus links from Compare results to it | ❌ Not selected |

### 1.2 Refresh scope: what does "Fetch latest" pull for one fund?

Multiple selections allowed.

| Option | Status |
|---|---|
| Full NAV history + metadata (ISIN, AMC, name) from mfapi.in (fast, per fund) | ✅ Selected |
| Latest TER + AUM from AMFI (downloads AMFI's full monthly files, slower, about 10–40 s) | ✅ Selected, **user can turn it on or off with a checkbox in the UI** |
| Live news and sentiment (Google News RSS) | ✅ Selected |
| Recompute stats/percentiles and save them to `fund_stats` | ✅ Selected |

> User note: "give me an option to select option 2 from UI". TER/AUM refresh is off by default and controlled by an **Include TER & AUM (slower)** checkbox.

### 1.3 Refresh history: keep a log?

| Option | Status |
|---|---|
| Yes: a new `fund_refresh_log` table, with "Last refreshed" shown in the UI | ✅ Selected (recommended) |
| No: just show the existing `nav_date` / `ter_as_of` / `aum_as_of` | ❌ Not selected |

### 1.4 Extra content in the fund view

Multiple selections allowed.

| Option | Status |
|---|---|
| Set 2 red-flag checklist for the single fund (reuse `build_checklist`) | ✅ Selected |
| Calendar-year returns bar chart + 1Y/3Y/5Y CAGR, rolling consistency, tracking error (from `FundProfile`) | ✅ Selected |
| Optional LLM-written fund summary (like the compare verdict, with a template fallback) | ❌ Not selected |
| "Compare with…" button that opens the Compare tab with this fund as Fund A | ❌ Not selected |

### 1.5 New data sources (holdings, exit load, min SIP, riskometer, manager info)

| Option | Status |
|---|---|
| No: out of scope; show only what the current sources provide | ✅ Selected (recommended) |
| Yes: research a source (e.g. AMC factsheets) as a later phase | ❌ Not selected |

---

## 2. What the Current Code Does

- Single-fund NAV fetching only happens when a fund's data is missing or out of date: `ensure_nav()` in `src/wealth_advisor/data/on_demand.py`. There is no way to force a refresh for one fund.
- `_fetch_and_save_mfapi()` updates `scheme_master` only; it never touches the curated `scheme` row.
- TER and AUM can only be refreshed in bulk: `sync_latest_ter()` / `sync_latest_aum()` update every matching fund.
- News results are cached for 15 minutes (`_RSS_CACHE` in `data/news.py`), so a "fetch latest" would return cached news.
- `GET /api/funds/{code}` is missing these fields: 1Y CAGR, rolling consistency, tracking error, calendar-year returns, and the Set 2 checklist. All of them are available through `compute_fund_profile()` and `build_checklist()`.
- There is no "last refreshed" record in the DB.
- `get_db_conn()` already accepts a `data_mode` query parameter.
- The frontend has no router. Views are switched with `history.pushState` in `App.tsx`, where `activeView` is `'advisor' | 'compare'`.

---

## 3. Implementation Steps

### Phase 1: Backend data layer

1. **`src/wealth_advisor/data/store.py`**
   - Add a `fund_refresh_log` table to `_SCHEMA` with these columns: `id INTEGER PRIMARY KEY AUTOINCREMENT, code, refreshed_at, data_mode, sources (JSON), status (ok|partial|failed), nav_points_before, nav_points_after, nav_date_before, nav_date_after, latest_nav, ter_before, ter_after, aum_before, aum_after, news_score, sentiment_score, errors (JSON)`.
   - Add a `FundRefreshRecord` Pydantic model.
   - Add two `MetadataStore` methods: `log_refresh(record)` and `refresh_history(code, limit=10)`.
2. **`src/wealth_advisor/data/amfi_ter.py` and `amfi_aum.py`** *(can run in parallel with step 1)*
   - Add an optional `only_codes: set[str] | None = None` parameter to `load_ter_into_master` / `sync_latest_ter` and `load_aum_into_master` / `sync_latest_aum`. When it's set, only those funds are updated in `scheme_master` and `scheme`.
   - The full Sync button keeps its current behaviour.
3. **`src/wealth_advisor/data/news.py`** *(can run in parallel with step 1)*
   - Add a `refresh` flag to `get_fund_news` and pass it through to `_fetch_google_news_rss` so it skips the 15-minute cache.
4. **New `src/wealth_advisor/data/fund_refresh.py`** *(depends on steps 1–3)*
   - Add `refresh_fund(store, code, *, include_ter_aum: bool, include_news: bool, data_mode: str) -> FundRefreshReport`. It runs these steps in order:
     1. Record the "before" values: NAV points, last NAV date, TER, AUM.
     2. Fetch the fund with `fetch_mfapi_json`, parse it with `parse_mfapi_payload(history_years=settings.nav_history_years)`, and save it with `save_nav`.
     3. Update the `scheme_master` row (ISIN, AMC, `nav_date`, `latest_nav`). For a curated fund, also update the `scheme` row (ISIN; `launch_date` if empty; `as_of`), using the same logic as `MfapiAmfiProvider.sync`.
     4. If `include_ter_aum` is on, run `sync_latest_ter(store, only_codes={code})` and `sync_latest_aum(store, only_codes={code})`.
     5. Fetch the benchmark proxy NAV if it's missing (same logic as `ensure_nav(fetch_benchmark=True)`).
     6. Run `compute_all_stats(store)`; the category percentiles need the full set of peer funds.
     7. If `include_news` is on, call `get_fund_news(..., fetch_live=True, refresh=True)`.
     8. Record the "after" values, then write a `fund_refresh_log` row.
   - If one step fails, the others still run. The error goes into `errors[]` and the status becomes `partial` (`failed` if the NAV fetch fails and nothing changed).

### Phase 2: API (`src/wealth_advisor/api/main.py`)

*Depends on Phase 1.*

5. **`POST /api/funds/{code}/refresh`**
   - Request body: `FundRefreshRequest { include_ter_aum: bool = False, include_news: bool = True }`. Data mode is passed as a query parameter (`get_db_conn`).
   - Errors:
     - **404** if the fund is unknown (`get_fund` and `get_master` both return nothing).
     - **409** in Synthetic mode or for `DEMO_` codes.
     - **422** if the code isn't digits only. The code is inserted into the mfapi URL, so this blocks path manipulation.
   - Returns a `FundRefreshReport`: before/after values, changed fields, status, errors, `refreshed_at`.
6. **`GET /api/funds/{code}/refresh-log?limit=10`**: returns the refresh history for the fund.
7. **Extend `GET /api/funds/{code}`** (`get_fund_detail`). Add these fields to the response:
   - `profile`: from `compute_fund_profile(store, code, fetch_if_missing=<real mode only>)`. Includes `cagr_1y`, `rolling_consistency_3y`, `tracking_error`, `calendar_returns`, `fund_age_years`, `manager_tenure_years`, `ytm`, `modified_duration`.
   - `checklist`: from `build_checklist(profile, peer_median_ytm=...)`. Move the peer-median-YTM calculation into a small `_peer_median_ytm(store)` helper that the compare endpoint also uses.
   - `last_refresh`: the newest `fund_refresh_log` row, or `null`.

### Phase 3: Frontend

*Steps 8–10 can be built alongside Phase 2 once the response shapes are agreed.*

8. **`frontend/src/components/FundDetailView.tsx`**
   - Add an `embedded?: boolean` prop. When it's set, drop the `min-h-screen` wrapper and the "Back to dashboard" link.
   - Add a `headerActions?: ReactNode` slot for the refresh button.
   - Add new sections (they appear on the standalone page too):
     - Extended metrics grid: 1Y/3Y/5Y CAGR, rolling 3Y median, rolling consistency, tracking error, alpha/beta.
     - Calendar-year returns `BarChart` (recharts, styled like `FundCompare`).
     - Set 2 red-flag checklist (same badge styling as Compare).
     - A "Last refreshed" badge in the header.
9. **New `frontend/src/components/FundExplorer.tsx`**
   - Reuses `FundCombobox` (Direct-only and Growth-only filters included) to pick a fund, and keeps `/explore?code=` in the URL.
   - Refresh panel:
     - An **Include TER & AUM (slower)** checkbox (off by default) and a **Fetch latest** button with a spinner (lucide `RefreshCw`).
     - A before → after summary: NAV date, points added, TER, AUM, errors.
     - A refresh history list from `GET /funds/{code}/refresh-log`.
   - The button is disabled with a tooltip in Synthetic mode.
   - Embeds `<FundDetailView code={code} dataMode={dataMode} embedded headerActions={...} />`.
   - After a successful refresh, it reloads `['fundDetail', code, ...]`, `['fundRefreshLog', code]`, `['dataMode']` and `['compareSchemes']`.
10. **`frontend/src/App.tsx`**
    - Add `'explore'` to the `activeView` union.
    - Add a `getExploreRoute()` function that uses the `code` URL parameter (not `fund`, which `getFundRoute` already uses).
    - Update `switchView` so it handles `/`, `/compare` and `/explore`.
    - Add a third nav button, **Fund Explorer** (lucide `FileSearch`).

### Phase 4: Tests

11. **New `tests/test_fund_refresh.py`**, with `fetch_mfapi_json`, `fetch_latest_ter` and `fetch_latest_aum` mocked. It checks that:
    - NAV is saved and `scheme_master` is updated.
    - The curated `scheme` row is updated.
    - `only_codes` updates just that one fund.
    - Stats are recomputed and a refresh log row is written.
    - A failed step gives a `partial` status.
12. **`tests/test_api.py`** additions, using the existing `TestClient` pattern. They check that:
    - The refresh endpoint returns 409 in Synthetic mode, 404 for an unknown code, 422 for a non-numeric code, and works on success.
    - The refresh-log endpoint works.
    - `GET /api/funds/{code}` includes `profile`, `checklist` and `last_refresh`.

---

## 4. Relevant Files

| File | Change |
|---|---|
| `src/wealth_advisor/data/store.py` | `fund_refresh_log` table and model, `log_refresh`, `refresh_history` |
| `src/wealth_advisor/data/amfi_ter.py` | `only_codes` filter |
| `src/wealth_advisor/data/amfi_aum.py` | `only_codes` filter |
| `src/wealth_advisor/data/news.py` | `refresh` flag that skips the cache |
| `src/wealth_advisor/data/fund_refresh.py` | **New**: runs the single-fund refresh steps |
| `src/wealth_advisor/data/on_demand.py` | Reuse `compute_fund_profile`, `FundProfile` |
| `src/wealth_advisor/data/real_fetcher.py` | Reuse `fetch_mfapi_json`, `parse_mfapi_payload` |
| `src/wealth_advisor/data/stats.py` | Reuse `compute_all_stats` |
| `src/wealth_advisor/domain/fund_compare.py` | Reuse `build_checklist` |
| `src/wealth_advisor/api/main.py` | Refresh and refresh-log endpoints, extended fund detail, `_peer_median_ytm` helper |
| `frontend/src/components/FundDetailView.tsx` | `embedded` mode, `headerActions`, new sections |
| `frontend/src/components/FundExplorer.tsx` | **New**: Fund Explorer tab |
| `frontend/src/components/FundCombobox.tsx` | Reused as is |
| `frontend/src/App.tsx` | Third tab and `/explore` route |
| `tests/test_fund_refresh.py` | **New** |
| `tests/test_api.py` | Endpoint tests |

---

## 5. Verification

1. `pytest tests/test_fund_refresh.py tests/test_api.py tests/test_fund_compare.py`, then the full `pytest` suite.
2. In `frontend/`: `npm run lint` and `npm run build`.
3. Manual check in Real mode:
   - Open Fund Explorer, pick a fund and click **Fetch latest**. Check that the NAV date moves forward and a refresh log entry appears.
   - Turn on **Include TER & AUM** and check that `ter_as_of` / `aum_as_of` change for that fund only (`sqlite3 data/wealth_real.db`).
   - Reload `/explore?code=X` and check that the fund loads straight away.
4. Manual check in Synthetic mode: the button is disabled, and the API returns 409.

---

## 6. Scope and Limitations

- **Included:**
  - The new tab.
  - Single-fund refresh: NAV and metadata, optional TER/AUM, news, stats.
  - The refresh log.
  - The Set 2 checklist and extended metrics.
- **Excluded:**
  - An LLM-written fund summary.
  - A "Compare with…" button.
  - New data sources: holdings, exit load, minimum SIP, riskometer and manager details aren't available from mfapi/AMFI.
- **Limitations:**
  - TER/AUM matching uses `scheme_master` rows, so a curated-only fund with no master row won't get new TER/AUM figures.
  - TER/AUM refresh downloads AMFI's full monthly files, so it can take about 10–40 seconds. That's why it's off by default.
  - News isn't saved in full; only the news and sentiment scores go into the refresh log row.
