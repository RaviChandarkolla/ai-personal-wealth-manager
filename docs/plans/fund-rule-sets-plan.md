# Plan: Switchable fund-selection rules (Set 1, Set 2, Combined) with a rules viewer

## Summary

Today the selection rules are spread across fixed thresholds in `src/wealth_advisor/config.py`, the `_BANDS` table in `domain/allocation.py`, the `CATEGORIES` table in `domain/categories.py`, `guardrails/suitability.py` and `domain/subscores.py`. These become **Set 1**, unchanged. **Set 2** comes from `docs/Magic of Mutual Funds-combined notes.md`. A third option, **Combined**, is generated from Set 1 and Set 2 by a merge engine.

A rule set is a group of settings that replace the defaults. `get_settings()` already reaches almost every rule, so it applies the active set's values on top of the base settings. The active set is held per request, so a choice made for one request does not change the global setting. The existing parallel analyst step copies that per-request state into its worker threads, so it keeps working. Set 1 changes nothing, so current tests should pass as they are.

The UI adds a three-way selector and a rules viewer that shows every rule in a set with worked examples. Examples are generated from the live values, so they never go out of date.

## Decisions

- The rule set is a global default with a per-request override, held per request rather than in a global variable.
- New fund categories are visible only under Set 2 and Combined.
- Alpha and beta are computed against a proxy benchmark NAV. Manager, YTM and maturity data come from optional CSV columns, and each rule is skipped when its data is missing.
- The trusted-AMC rule is a soft score bonus only.
- Combined is generated from Set 1 and Set 2, never written by hand.
- Combined filters and caps take the stricter value, scoring weights are averaged, and category and AMC lists are merged.
- When ranges do not overlap, the lower-risk side wins and the conflict is shown.
- Examples in the viewer are generated from the live values.
- Not included: user-defined merge strategies, editing rules from the UI, and combining more than two sets.

## Phase A: Rule-set core

1. In `config.py`, add `fund_rule_set: Literal["set1","set2","combined"] = "set1"`. Add new settings whose defaults switch them off, so Set 1 behaves exactly as today:
   - `allocation_bands` (today's `_BANDS`) and `category_min_horizons`
   - `allowed_categories` (the current 9 categories)
   - `equity_cap_horizon_lt_5y: float | None`
   - `require_positive_alpha: bool`, `max_beta: float | None`, `min_manager_tenure_years: float | None`
   - `trusted_amcs: list[str]`, `trusted_amc_bonus: float`
   - `fundamental_weights`, `ytm_outlier_pp: float | None`, `match_maturity_to_horizon: bool`
   - make `min_rating` optional
   - `benchmark_proxy_codes: dict[str, str]`
2. Rename the cached settings function to `_base_settings()`. `get_settings()` then returns the base settings with the active rule set applied, and caches one copy per set.
3. Create `domain/rulesets.py` with:
   - `RULE_SETS` holding the definitions for `set1` and `set2`
   - `combined`, built from them by the merge code in Phase D and cached
   - `use_rule_set(name)` and `active_rule_set()` to set and read the set for the current request

## Phase B: Make the rules read the settings (depends on A)

4. `domain/allocation.py`: `allocation_band()` reads `s.allocation_bands`. `equity_cap()` adds the cap for horizons under 5 years.
5. `domain/categories.py`: add `min_horizon(category, s)`, which uses the override first and falls back to the static table. `categories_for()` keeps only categories in `s.allowed_categories`. `subscores.fit_score` and `check_fund` use `min_horizon()`.
6. `guardrails/suitability.py` `check_fund()` gets three new rules, each applied only when its setting is on and the data exists:
   - alpha must be above 0 for actively managed equity funds (Index Fund and Gold ETF are exempt)
   - for debt funds, `avg_maturity_years` must be no longer than the goal horizon
   - the rating check is skipped when `min_rating` is None
7. `domain/subscores.py`:
   - `fundamental_score` uses weighted parts from `s.fundamental_weights`: rolling returns (weighted more), 5-year CAGR, Sharpe, drawdown, alpha score and a low-volatility percentile
   - `fit_score` penalises beta above `max_beta`
   - `liquidity_cost_score` adds the trusted-AMC bonus (capped at 100)
   - `detect_conflicts` adds two flags: manager tenure shorter than the minimum, and YTM more than X percentage points above the category median ("check credit risk")

### Set 2 values (from the notes)

- **Allocation bands** (follow the conservative, balanced and aggressive hybrid splits in Session 6):

  | Risk profile | Equity | Debt | Gold | Alternatives |
  |---|---|---|---|---|
  | Conservative | 10-25% | 75-90% | 0-10% | 0 |
  | Moderate | 40-60% | 30-50% | 5-10% | 0 |
  | Aggressive | 65-80% | 15-30% | 5-10% | 0 |

- **Equity caps:** no equity for horizons under 3 years, and at most 25% for 3-5 years ("equity for goals more than 5 years away", plus Session 10 on protecting the corpus near the goal).
- **Minimum horizons:**
  - 5 years: Large Cap, Flexi Cap, Large & Mid Cap, Mid Cap, Index Fund, ELSS, Aggressive Hybrid
  - 7 years: Small Cap
  - 3 years: Gilt, Conservative Hybrid, Gold ETF
  - 2 years: Arbitrage
  - 1 year: Banking & PSU, Short Duration
  - no minimum: Liquid
- **Fund filters:** direct plan required; minimum fund age 3 years (age is not the only criterion per Session 8); AUM floor 100 Cr (AUM is only an initial indicator); no star-rating filter; alpha above 0 required.
- **Analyst weights:** fundamental 0.45, fit 0.30, cost 0.15, news 0.05, sentiment 0.05.
- **Trusted AMCs:** SBI, HDFC, ICICI Prudential, PPFAS and Motilal Oswal, with a +5 bonus.
- **Disclosure:** ELSS has a 3-year lock-in on every SIP instalment.

## Phase C: Data (can run in parallel with B)

8. Add 7 categories to `CATEGORIES`, plus their expense ceilings in `config.py`. Set 1's `allowed_categories` leaves them out.

   | Category | Bucket | Small-AUM exception |
   |---|---|---|
   | Small Cap | equity | no |
   | Large & Mid Cap | equity | no |
   | ELSS | equity | no |
   | Aggressive Hybrid | equity | no |
   | Arbitrage | debt | no |
   | Banking & PSU | debt | no |
   | Gilt | debt | no |

9. `data/curated_funds.csv`:
   - add optional columns `manager_since`, `ytm`, `modified_duration`, `avg_maturity_years`
   - add 3-4 direct-growth funds per new category
   - **AMFI scheme codes must be verified** through the mfapi.in search
   - add a benchmark proxy fund for each equity benchmark (the Nifty 50 index fund already exists; optionally add Midcap 150 and Smallcap 250 index funds)
10. `data/store.py`:
    - `FundRecord` gets the new fields; `FundStats` gets `alpha`, `beta`, `pct_low_vol`
    - add a small migration step that adds missing columns to existing databases
    - `load_funds_csv` parses the new columns
11. `data/stats.py` `compute_all_stats`: calculate beta and annualised alpha from 3 years of daily returns against `benchmark_proxy_codes[category]`, and add the within-category low-volatility percentile. The beta and alpha formulas go in `domain/metrics.py`.
12. `data/demo_seed.py`: add `_SPEC` entries for the new categories, and fill manager, YTM and maturity values so the synthetic data covers Set 2's rules.
13. `agents/tools.py` `facts_for_fund`: add the facts `amc`, `alpha`, `beta`, `pct_low_vol`, `manager_tenure_years`, `ytm`, `modified_duration` and `avg_maturity_years`. Each gets a citation, so the grounding check keeps working. Optionally show alpha and beta in the researcher's candidate table (`agents/mf_researcher.py`).

## Phase D: Merge engine and rule catalogue (depends on A; can run in parallel with B and C)

14. New `domain/rule_merge.py` with `merge_rule_sets(a, b)`. It returns the combined overrides plus a provenance list with one entry per rule: setting, Set 1 value, Set 2 value, result, strategy, which set won, and the reason. Each setting is merged one of these ways:

    | Strategy | Applies to | Result |
    |---|---|---|
    | Stricter minimum (higher wins) | `min_aum_cr`, `min_aum_cr_small`, `min_track_years_*`, `min_rating`, `min_manager_tenure_years`, `buy_min_score`, `min_confidence` | the higher floor |
    | Stricter maximum (lower wins) | `expense_ceilings` (per category), `vol_caps` (per risk profile), `equity_cap_*`, `max_beta`, `max_weight_per_instrument` | the lower ceiling |
    | Longer horizon | `category_min_horizons` | the longer horizon per category |
    | Overlapping range | `allocation_bands` per risk profile and bucket | the overlap of both ranges; if they do not overlap, the lower-risk side, flagged as a conflict |
    | Union | `allowed_categories`, `trusted_amcs` | everything from both sets |
    | Either set | `require_direct_plan`, `require_positive_alpha`, `match_maturity_to_horizon` | the rule applies if either set has it |
    | Blend | `analyst_weights`, `fundamental_weights` | average of both, rescaled to sum to 1 |
    | One-sided | any rule only one set defines (e.g. YTM warning, AMC bonus, beta limit) | that set's value |

    Reasoning: filters and caps take the stricter value because the combined set is meant to be safer than either set alone. Scoring weights are averaged so both views count. The category list is a union, but every new category must still pass the stricter filters.

15. The resulting combined values:
    - **Allocation bands:**

      | Risk profile | Equity | Debt | Gold | Alternatives |
      |---|---|---|---|---|
      | Conservative | 15-25% | 75-85% | 0-10% | 0 |
      | Moderate | 40-60% | 30-45% | 5-10% | 0 |
      | Aggressive | 70-80% | 15-20% | 5-10% | 0 |

    - **Equity caps:** 0% under 3 years, 25% for 3-5 years, 30% when emergency savings are low.
    - **Fund filters:** AUM floor 500 Cr; equity funds need 5 years of history; star rating 4 or above; alpha above 0; direct plan; maturity no longer than the horizon.
    - **Analyst weights:** fundamental 0.40, fit 0.275, news 0.125, sentiment 0.075, cost 0.125.

    **Known effect of the 500 Cr floor:** it may filter out new-category funds with smaller AUM. When that leaves a bucket with no funds, the provenance list shows why. The weights may also end up uneven between buckets, and the UI displays that note.

16. New `domain/rule_catalog.py`: a list of rule entries, each with:
    - `id`, `group` (Allocation, Capacity caps, Fund filters, Scoring, Categories & horizons, Warnings, Disclosures)
    - `title`, `description`, `kind` (hard filter, score, allocation, disclosure)
    - `setting_keys`, a source reference (e.g. "Notes S8: Alpha", "Set 1: plan 1.3")
    - `example(s)`: a function that builds an example from the live values

    Example outputs:
    - "Mid Cap fund with expense ratio 1.2% is rejected: ceiling is 1.0%."
    - "A 2-year goal gets at most 0% equity."
    - "A Moderate profile gets 40-60% equity, 30-45% debt, 5-10% gold. Rs 10L at the midpoints is Rs 5L equity, Rs 4.25L debt, Rs 0.75L gold."
    - "Fund with alpha -0.8 is rejected because it trails its benchmark."
    - "SBI fund gets +5 on the cost score."

    `describe_rule_set(name)` returns the rules grouped, with values and examples, plus provenance for `combined`. It also marks rules a set does not use (e.g. "Set 1: not used").

## Phase E: Pipeline and API (depends on A, B and D)

17. `pipeline.py`: `advise(rule_set=...)` runs inside `use_rule_set()`.
    - The audit records the set name and the combined values (profiling and decision records include `fund_rule_set`); for `combined` it also stores the provenance.
    - `audit/replay.py` re-applies the recorded set.
18. Disclosures (`guardrails/compliance.py`) name the set used. For `combined` they also list the main merge decisions, plus the ELSS lock-in note when ELSS is recommended.
19. Endpoints in `api/main.py`:
    - keep the active set in memory with `get_active_rule_set` and `set_active_rule_set`, persisted the same way `set_active_mode` persists data mode
    - `GET /api/config/rule-set` returns `{active, default, available: [{id, label, description, highlights}]}`; `POST /api/config/rule-set` takes `{rule_set, persist_env}`
    - `GET /api/rules?rule_set=set1|set2|combined` returns the grouped rules from `describe_rule_set`
    - `GET /api/rules/compare` returns every rule with its Set 1, Set 2 and combined values, the strategy used and the winning set
    - `AdviseRequest.rule_set` is optional and accepts all three options; `AdviseResponse` returns `rule_set` and, for combined, a short provenance summary
    - the request override does not change the global setting
20. `cli.py`: add `--rule-set set1|set2|combined` to the advise command, and `wealth-advisor rules [--rule-set X | --compare]` to print the catalogue.

## Phase F: Frontend (depends on E)

21. `frontend/src/App.tsx`:
    - add a `useQuery(['ruleSet'])` and a switch mutation, following the data-mode pattern
    - the toggle sits next to the data-mode toggle and has three options: **Set 1 · Default**, **Set 2 · Magic of MF**, **Combined · Set 1 + 2**, each with a hover description
    - `adviseMutation` sends `rule_set`
    - the results header shows a badge for the set used; for combined runs the badge expands to the provenance summary
22. Add a "View rules" button next to the toggle. It opens a new `frontend/src/components/RulesViewer.tsx` drawer that loads its data with React Query from `/api/rules` and `/api/rules/compare`:
    - **Tabs:** Set 1, Set 2, Combined, Compare.
    - **Set tabs:** collapsible sections by group. Each rule shows its title, a kind badge, its value, a description, a highlighted example box and its source reference.
    - **Combined tab:** each rule also shows the strategy used and which set won (e.g. "Stricter max - from Set 1"). Conflicts are highlighted.
    - **Compare tab:** a table with columns Rule | Set 1 | Set 2 | Combined | Strategy. Cells that differ are highlighted, and a filter can show only differences.
    - A search box filters rules by title.
23. Optional: the recommendation table can link a rejected fund or a violated rule to its entry in the rules viewer, using the rule id.

## Phase G: Tests

24. New `tests/test_rulesets.py`:
    - Set 1 settings equal today's defaults
    - Set 2 settings are applied, and the per-request choice does not leak across requests or threads
    - Set 2 bands and equity caps
    - `categories_for` leaves out the new categories under Set 1
    - `check_fund` flags negative alpha and maturity longer than the horizon
    - the AMC bonus is applied
25. Stats tests:
    - the proxy fund has beta of 1 and alpha of 0 against itself
    - the column migration runs safely more than once
26. New `tests/test_rule_merge.py`, one test per strategy:
    - floor -> maximum
    - ceiling -> minimum
    - overlapping ranges, including the no-overlap case falling back to the lower-risk side
    - union of lists
    - "either set" for on/off rules
    - blended weights sum to 1
    - one-sided rules are adopted
    - the provenance list covers every setting either set overrides
    - the combined bands match the values in step 15
27. New `tests/test_rule_catalog.py`:
    - every catalogue entry's setting keys exist in Settings
    - every example builds without error for all 3 sets
    - every setting that Set 1 or Set 2 overrides appears in at least one catalogue entry (so nothing is missing from the viewer)
28. API tests in `tests/test_api.py`:
    - GET and POST `/api/config/rule-set`
    - `/api/rules` for each set
    - `/api/rules/compare` row count
    - advise with `rule_set="set2"` and `"combined"` returns the set (and provenance for combined) and leaves the global setting unchanged
29. Pipeline tests:
    - on synthetic data, the combined set recommends no fund rejected by Set 1 or Set 2
    - a 2-year goal gets 0% equity under Set 2 and under combined

## Relevant files

- **Backend:** `src/wealth_advisor/config.py`, `domain/rulesets.py` (new), `domain/rule_merge.py` (new), `domain/rule_catalog.py` (new), `domain/allocation.py`, `domain/categories.py`, `domain/subscores.py`, `domain/metrics.py`
- **Rules and data:** `guardrails/suitability.py`, `guardrails/compliance.py`, `data/store.py`, `data/stats.py`, `data/demo_seed.py`, `data/curated_funds.csv`
- **Wiring:** `agents/tools.py`, `agents/mf_researcher.py`, `pipeline.py`, `audit/replay.py`, `api/main.py`, `cli.py`
- **Frontend:** `frontend/src/App.tsx`, `frontend/src/components/RulesViewer.tsx` (new)

## Verification

1. Run `poetry run pytest tests/`. Existing tests should pass without changes, along with the new ones.
2. Run `wealth-advisor rules --compare` and check that the combined values match step 15.
3. Reseed the synthetic data, then run the same query under Set 1, Set 2 and combined. The allocations and shortlists should differ, Set 2 and combined should recommend no equity for a 2-year goal, and the combined allocation should fall within both sets' bands.
4. Real mode: run `POST /api/data/sync`, then confirm the new funds have NAV data and that alpha and beta are filled in `fund_stats`.
5. In the UI:
   - switch between the three options, reload, and confirm the choice persists
   - check that the request body shows `rule_set`
   - open the viewer and check every tab
   - confirm the examples update when a value changes (e.g. edit an expense ceiling and restart)
   - confirm the Compare tab's differences-only filter works
   - run `npm run build`
6. Replay a Set 2 run and a combined run from the audit log and confirm the output matches.

## Not covered by the rules

Exit load, tax-based optimisation, portfolio turnover, sector allocation, the review and redeem workflows (Sessions 9-10), and the expense-ratio slab rules. There is no data for these, so they appear only as disclosures where relevant. The Set 2 numbers above are one reading of the notes and are easy to change in `RULE_SETS`.
