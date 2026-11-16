# Plan: Compare two mutual funds using the "Magic of MF" notes

## Summary

Today the database only holds about 50 curated funds. This plan adds a separate **AMFI scheme list** with every open-ended scheme, plus expense ratio (TER) and AUM data from AMFI.

When the user compares two funds, the backend downloads their NAV history from mfapi.in on demand and computes metrics with the existing `domain/metrics.py`. Plain code then scores and ranks the two funds against the criteria in `docs/Magic of Mutual Funds-combined notes.md`, one criterion per row. The LLM only writes an optional summary paragraph, and that text is checked against the computed facts. The UI gets a new Compare view with two searchable dropdowns.

## Design questions and answers

The options marked **(selected)** are the ones the user chose. **(recommended)** marks the option suggested when the question was asked.

### 1. Which funds should the dropdown list?

Today the database only holds the curated CSV funds, about 50 of them.

- **(selected, recommended)** All open-ended AMFI schemes, with filters (default: Direct + Growth). About 10,000+ schemes from AMFI `NAVAll.txt` or the mfapi.in scheme list, in a searchable dropdown.
- Only Direct + Growth plans, across all AMFI schemes.
- Every AMFI scheme, including closed-ended, IDCW and regular plans.

### 2. How should missing fund data be handled?

Free sources give NAV history for every fund. Expense ratio, AUM, manager, YTM and similar fields exist only for curated funds.

- (recommended) Compute NAV-based metrics for every fund. For funds outside the curated list, show fact-sheet fields as `no_data` and leave them out of the ranking.
- **(selected)** Also load AMFI's TER (expense ratio) data and quarterly scheme-wise AUM files, so more criteria can be scored. This covers more funds but needs more downloading and parsing, and the URLs and file formats must be checked first.

### 3. What if the user picks funds from different categories?

The notes say to compare funds within the same category.

- **(selected, recommended)** Allow it, but show a warning and rank only on criteria that work across categories (cost, consistency, risk-adjusted returns).
- Block it: the second dropdown only shows funds from the same category.
- Allow it and rank on every criterion, with no warning.

### 4. Should the user enter a horizon or risk profile?

With these inputs, suitability would affect the ranking (for example, Small Cap needs 7+ years).

- (recommended) Optional horizon and risk inputs. Suitability is shown as a pass/fail flag and adds a small part of the score.
- **(selected)** No. Compare the two funds on their own merits only.

### 5. Should an LLM write a plain-language verdict?

The score and ranking always come from plain code. The LLM text may cite only computed facts and is checked by the grounding validator.

- **(selected, recommended)** Yes, as an optional summary paragraph, checked by the grounding validator.
- No LLM. Use template text only.

## Steps

### Phase A: List of all real funds (backend data)

1. Add a new `scheme_master` table in `data/store.py` with these columns: code, isin_growth, isin_reinvest, name, amc, sebi_category, category, plan, option, structure, latest_nav, nav_date, expense_ratio, ter_as_of, aum_cr, aum_as_of, source, as_of.
   - It is a separate table, so the curated `scheme` table that `query_funds` and `advise` use stays unchanged.
   - Use the same migration pattern as `_migrate()`.
   - Add methods `upsert_master(rows)`, `search_master(q, category, plan, option, limit)` and `get_master(code)`.
   - Index `name` and `category`. Use SQLite FTS5 if it is available, otherwise `LIKE`.
2. Write a `build_scheme_master(rows: list[AmfiRow])` function in `data/amfi.py`. It reuses `parse_nav_all()` and:
   - keeps only open-ended schemes, using the `section` header
   - reads the SEBI category from the header (e.g. "Equity Scheme - Large Cap Fund" becomes "Large Cap")
   - maps that to the names in `CATEGORIES`, and keeps the raw SEBI name for categories with no match (Focused, Dynamic Asset Allocation, Credit Risk and so on)
   - reads plan (direct or regular) and option (growth or IDCW) from the scheme name
3. Add a new `data/amfi_ter.py` that loads expense ratios (TER) from AMFI's "TER of MF Schemes" page (`https://www.amfiindia.com/ter-of-mf-schemes`, monthly). Add a new `data/amfi_aum.py` that loads AMFI's quarterly average AUM from the "Average AUM" page (`https://www.amfiindia.com/aum-data/average-aum`).
   - **The main route is importing the file exported from each page** (`--ter-file`, `--aum-file`). Neither page offers a fixed download file; both load their data in the browser from an AMFI backend that is not documented.
   - Automatic download is optional. It depends on finding the page's data address (in the browser's developer tools, Network tab) and on checking AMFI's terms of use. If added, it must use https only and fall back to file import when it fails.
   - Store the `source` page, the reporting month or quarter as `as_of`, and the import date.
   - For TER, store the direct and regular plan values separately. Keep the total TER, and keep the breakdown (base TER, brokerage, transaction cost, taxes) when it is present.
   - Match rows to schemes by scheme code, or by ISIN when there is no code.
   - Produce a rejection report that lists the rows that could not be matched.
   - Not used: mfapi.in and `NAVAll.txt` have no TER or AUM data. Fund house websites use about 45 different formats. Yahoo Finance (`yfinance`) is an unofficial scraper, identifies funds by Morningstar ID rather than AMFI code, and has patchy coverage of Indian funds.
4. Add `refresh_master: bool`, `refresh_ter: bool` and `refresh_aum: bool` to `POST /api/data/sync`. Add a CLI command `sync-schemes [--file] [--ter-file] [--aum-file]`.

Phase A can be built at the same time as Phase C.

### Phase B: Download NAV and compute metrics on demand (depends on A)

5. Add a new `data/on_demand.py` with `ensure_nav(store, code)`.
   - If the stored NAV is missing or older than the latest AMFI date, it calls `fetch_mfapi_json` and `parse_mfapi_payload`, then `save_nav`.
   - It also makes sure the benchmark proxy fund (from `benchmark_proxy_codes`) has NAV data.
   - Storing NAV for funds outside the curated list is safe, because recommendations read funds through the `scheme` table.
6. Add three functions to `domain/metrics.py`, each with a worked example in its docstring:
   - `tracking_error(nav, bench)`: the annualised standard deviation of the gap between fund and benchmark returns
   - `calendar_returns(nav) -> dict[year, %]`
   - `rolling_consistency(nav, 3, rf)`: the share of 3-year rolling windows that beat the risk-free rate
7. Add `compute_fund_profile(store, code) -> FundProfile`. It combines the fund's details (from the curated list first, then the AMFI list) with these metrics:
   - CAGR over 1, 3 and 5 years
   - median rolling 3-year return and rolling consistency
   - calendar-year returns
   - volatility, max drawdown, Sharpe and Sortino ratios
   - alpha and beta, and tracking error for index funds and ETFs
   - fund age, taken from the first NAV date
   - manager, YTM, duration and maturity, which only curated funds have

   Every value is a `Fact` with a `Citation` (mfapi, AMFI, TER or AUM file, curated CSV). A missing value becomes `no_data`.

### Phase C: Scoring engine (plain code, no LLM)

8. Add a new `domain/fund_compare.py` with a `CRITERIA` list. Each criterion has:
   - an id and a label
   - the notes session it comes from
   - which direction is better (higher, lower, or information only)
   - which funds it applies to (all, active equity/hybrid, index/ETF, debt)
   - `category_neutral: bool`
   - a weight and a tie tolerance

| Criterion | Session | Better | Applies to | Works across categories |
|---|---|---|---|---|
| Rolling 3y median return | S7 (most underrated) | higher | all | no |
| Rolling consistency (% of windows above risk-free rate) | S7 | higher | all | yes |
| Trailing CAGR 3y / 5y | S7 | higher | all | no |
| Calendar years won head-to-head | S7 | more | all | no |
| Sharpe / Sortino | S8 | higher | all | yes |
| Standard deviation (volatility) | S8 | lower | all | no |
| Max drawdown | S8 | smaller | all | no |
| Alpha | S7/S8 | higher | active equity/hybrid | yes |
| Beta | S8 | information only | equity | – |
| Tracking error | S2/S3 | lower | index/ETF | yes |
| Expense ratio | S2 | lower | all | yes |
| Direct vs Regular | S3 | direct | all | yes |
| Growth vs IDCW | S3/S11 | growth | all | yes |
| AUM (initial indicator, low weight) | S2/S8 | higher | all | yes |
| Fund age (low weight) | S8 | older | all | yes |
| Manager tenure | S7 | longer | active | yes |
| Trusted AMC bonus | S7 | on the list | all | yes |
| YTM (with credit-check flag), modified duration, average maturity | S8 | context | debt | no |

9. Write `compare_funds(a: FundProfile, b: FundProfile) -> ComparisonResult`.
   - **Criteria used:** only criteria that apply to both funds and have data for both are scored.
   - **Different categories:** criteria that do not work across categories are also dropped, and the result carries the warning "Notes S7: compare within the same category".
   - **Points:** the winner of a criterion gets its full weight. A tie splits the weight.
   - **Score:** points won ÷ total weight scored × 100.
   - **Ranking:** the higher score ranks first. If the scores are within 2 points, the winner on rolling returns ranks first.
   - **Output rows:** each criterion row reports both values, the winner, the points, the status (`scored`, `no_data`, `not_applicable`, `info`, `excluded_cross_category`) and the citations.
10. Run a red-flag checklist from the notes for each fund:
    - regular plan
    - IDCW option, flagged more strongly for ELSS
    - fund age under 3 years
    - AUM under 100 Cr
    - alpha of 0 or below
    - YTM well above the category median ("check credit risk")
    - minimum holding periods for Small Cap and Mid Cap, shown as information
    - ELSS 3-year lock-in on each SIP instalment
    - Dynamic Asset Allocation warning for low-risk investors

    Thresholds come from `get_rule_set_overrides("set2")` in `domain/rulesets.py`, so each is defined in one place. Weights go in a new `compare_weights` setting in `config.py`.
11. List the criteria the notes mention but the app has no data for, each marked "not evaluated" with the reason: exit load, portfolio turnover, top 3 holdings and sectors, riskometer, and credit rating mix.

### Phase D: Summary paragraph and audit log (depends on C)

12. Add a new `agents/compare_narrator.py` that follows the `AnalystAgent._prose` pattern.
    - The LLM gets only the fact table and the criterion results, and writes a short verdict that cites `[fact_id]`.
    - The verdict is checked with `check_text(..., require_citation_ids=True)`. If it fails, the LLM gets one retry. If that also fails, template text is used.
    - The LLM never produces a score.
    - `narrative=false` skips the LLM entirely.
13. Write every comparison to the audit log with `AuditLog.append(run_id, agent="fund_compare", ...)`. Log the inputs, the fact hashes, the result and the narrative.
14. Add the disclosures (educational use only, past performance does not predict future results) using the existing compliance text.

### Phase E: API and CLI (depends on B, C and D)

15. Endpoints in `api/main.py`:
    - `GET /api/compare/schemes?q=&category=&plan=direct&option=growth&limit=50`: a fast search for the dropdown. In synthetic mode it lists the DEMO funds.
    - `GET /api/compare/categories`
    - `POST /api/compare` with `{codes: [a, b], narrative: bool, data_mode?}`. It returns a `CompareResponse` with: funds, criteria rows, checklist, scores, ranking, `same_category`, warnings, `not_evaluated`, narrative, disclosures, `run_id` and `as_of`.
    - Return 422 unless there are exactly 2 different, known codes. Each request makes at most 2 mfapi calls.
16. Add a CLI command `compare CODE_A CODE_B [--no-narrative]` that prints the scorecard table.

### Phase F: Frontend (depends on E)

17. Add a new `frontend/src/components/FundCombobox.tsx`. The project has no combobox library, so this is built with plain React and Tailwind:
    - a React Query search on `/compare/schemes`, debounced by 250 ms
    - keyboard navigation
    - category and plan chips on each result
    - Direct and Growth filter checkboxes, both on by default
18. Add a new `frontend/src/components/FundCompare.tsx` with:
    - two comboboxes, a "Compare" button and a toggle for the summary paragraph
    - a winner banner showing the ranking and both scores
    - the cross-category warning, when it applies
    - a criteria table (criterion, session, Fund A, Fund B, winner, points, status), with `no_data` rows greyed out
    - the red-flag checklist for each fund
    - the "Not evaluated" list
    - a calendar-year returns bar chart and a normalised NAV line chart, both in recharts
    - citations and disclosures
19. Add top-level navigation ("Advisor | Compare Funds") in `App.tsx`. Use the same URL-path approach as the fund detail view (`/compare?a=&b=`), so a comparison can be shared as a link.

### Phase G: Tests

20. `tests/test_scheme_master.py`:
    - reading NAVAll sections, categories, plan and option from a sample text file
    - the TER and AUM parsers on sample files
    - the search filters
21. Additions to `tests/test_metrics.py`:
    - a fund's tracking error against itself is 0
    - calendar returns on a synthetic series
    - hypothesis property tests for rolling consistency
22. `tests/test_fund_compare.py`:
    - swapping A and B gives the same result
    - comparing a fund with itself gives a tie
    - `no_data` criteria are left out and the remaining weights are rescaled
    - funds from different categories are scored on cross-category criteria only, and the warning is shown
    - checklist flags trigger at the Set 2 thresholds
    - the same input always gives the same output
23. `tests/test_api.py`:
    - fund search
    - comparing 2 DEMO funds
    - 422 for a single code or two identical codes
    - `narrative=false` makes no LLM call (use `FakeLLM`)
    - a narrative with an invented number falls back to the template text

## Relevant files

- `src/wealth_advisor/data/store.py`: add `scheme_master` and its migration
- `src/wealth_advisor/data/amfi.py`: reuse `parse_nav_all` and `AmfiRow.section`
- `src/wealth_advisor/data/real_fetcher.py`: reuse `fetch_mfapi_json` and `parse_mfapi_payload`
- `src/wealth_advisor/domain/metrics.py`: reuse `cagr`, `rolling_returns`, `sharpe`, `sortino`, `alpha` and `beta`; add three new functions
- `src/wealth_advisor/domain/rulesets.py`: where the Set 2 thresholds come from
- `src/wealth_advisor/guardrails/grounding.py`: `check_text`
- `src/wealth_advisor/audit/log.py`: `AuditLog.append`
- `src/wealth_advisor/api/main.py`, `src/wealth_advisor/cli.py`, `src/wealth_advisor/config.py`
- New files: `data/amfi_ter.py`, `data/amfi_aum.py`, `data/on_demand.py`, `domain/fund_compare.py`, `agents/compare_narrator.py`, `frontend/src/components/FundCombobox.tsx`, `frontend/src/components/FundCompare.tsx`

## Verification

1. Run `poetry run pytest tests/`. The existing tests should pass without changes.
2. Run `wealth-advisor sync-schemes`.
   - More than 5,000 open-ended schemes should load.
   - TER and AUM should match most Direct-Growth schemes, and the rejection report should list the rest.
3. Run `wealth-advisor compare` on:
   - two real Flexi Cap funds (find their codes with the search), and check the CAGR values against mfapi or Value Research
   - a Large Cap fund and a Liquid fund, and confirm the cross-category warning appears and only cross-category criteria are scored
4. In the UI:
   - search "parag" and check that its funds appear
   - compare two funds
   - reload the shared URL and get the same result
   - turn off the summary paragraph and confirm no LLM call is made (check Langfuse)
   - run `npm run build`
5. After a comparison, run `wealth-advisor audit-verify` and confirm it passes.

## Decisions

- **Fund list:** all open-ended AMFI schemes, filtered to Direct + Growth by default. The separate `scheme_master` table means advice and recommendations are not affected.
- **Missing data:** TER and AUM are loaded from AMFI. Manager, YTM, duration and maturity exist only for curated funds, and show as `no_data` for every other fund.
- **Different categories:** allowed, with a warning, and scored on cross-category criteria only.
- **Scope:** no goal or horizon inputs, exactly two funds, and no ranking against category peers (that would need NAV data for the whole category).
- **Scores:** always computed by plain code. The LLM only writes the summary paragraph, and that paragraph is checked against the facts.
- **Not included:** exit load, portfolio turnover, holdings and sectors, riskometer, and taxes. These are listed as "not evaluated".
