# Progress Checkpoint: Switchable Fund-Selection Rule Sets (Set 1, Set 2, Combined)

**Timestamp:** 2026-10-15 12:32:00 IST  
**Plan Reference:** `docs/plans/fund-rule-sets-plan.md`  
**Workspace:** `/Users/r.kolla/AI/agentic-ai-system-design/ai-personal-wealth-manager`  
**Status:** In Progress (Ready to resume Phase A: `config.py` and `rulesets.py`)

---

## 1. Executive Summary & Objective

The objective is to implement switchable fund-selection rule sets:
1. **Set 1:** Preserves current default production behavior exactly (100% backward-compatible, all 104 existing tests must remain green).
2. **Set 2:** Derived from "Magic of Mutual Funds" investment guidelines (7 new categories, positive alpha requirement for active equity, maturity-horizon matching for debt, tighter horizon floors, 3-year manager tenure, trusted AMC bonus, YTM outlier credit flags).
3. **Combined:** Dynamically computed via a deterministic merge engine using 8 conflict resolution strategies with granular provenance tracking.
4. **Rules Viewer:** Interactive UI drawer + compare view + live parameter examples across both backend API/CLI and frontend.

---

## 2. Completed Work & Verified Baseline

1. **Existing Baseline Verified:**
   - Ran pytest across existing test suite: 104 passed.
2. **AMFI & Scheme Data Verified:**
   - Queried `api.mfapi.in` to obtain and verify scheme codes and ISINs for all 7 new categories and benchmark proxies:
     - **Benchmark Proxies:**
       - Midcap 150 Index Proxy: `147622` (Motilal Oswal Nifty Midcap 150 Index - Direct Growth)
       - Smallcap 250 Index Proxy: `147623` (Motilal Oswal Nifty Smallcap 250 Index - Direct Growth)
     - **New Categories (7):**
       - Small Cap: `118778` (Nippon India), `125497` (SBI), `130503` (HDFC), `125354` (Axis)
       - Large & Mid Cap: `118834` (Mirae Asset), `130498` (HDFC), `120596` (ICICI Prudential)
       - ELSS: `135781` (Mirae Asset), `147481` (PPFAS), `120847` (Quant)
       - Aggressive Hybrid: `119609` (SBI), `118272` (Canara Robeco), `136464` (HDFC Retirement)
       - Arbitrage: `119771` (Kotak), `119574` (SBI), `120364` (ICICI Prudential), `118931` (HDFC)
       - Banking & PSU: `125503` (SBI), `128629` (HDFC), `121279` (Bandhan)
       - Gilt: `119707` (SBI), `120608` (ICICI Prudential Short Term Gilt)
3. **Rule Merge Engine Built:**
   - Created `src/wealth_advisor/domain/rule_merge.py` implementing all 8 merge strategies:
     - Stricter minimum (AUM, track record, rating, manager tenure, score floor)
     - Stricter maximum (equity capacity caps, beta ceiling, expense ceilings, vol caps)
     - Longer horizon (category minimum holding horizons)
     - Range overlap (allocation bands intersection; fallback to lower-risk on conflict)
     - Union (allowed categories, trusted AMCs)
     - Either set (direct plan, positive alpha, maturity matching)
     - Blend (analyst weights, fundamental weights averaged and normalized)
     - One-sided rules (YTM outlier threshold, trusted AMC bonus)
   - Generates full provenance audit entries with winner and rationale.

---

## 3. Implementation Roadmap (Phases Remaining)

### Phase A: Core Configuration & Rule Sets (Immediate Next Step)
- [ ] **`src/wealth_advisor/config.py`**:
  - Add `fund_rule_set: Literal["set1", "set2", "combined"] = "set1"`
  - Add configuration fields:
    - `allocation_bands: dict[str, dict[str, tuple[float, float]]]`
    - `category_min_horizons: dict[str, float]`
    - `allowed_categories: list[str]`
    - `equity_cap_horizon_lt_5y: float | None = None`
    - `require_positive_alpha: bool = False`
    - `max_beta: float | None = None`
    - `min_manager_tenure_years: float | None = None`
    - `trusted_amcs: list[str] = []`
    - `trusted_amc_bonus: float = 0.0`
    - `fundamental_weights: dict[str, float]`
    - `ytm_outlier_pp: float | None = None`
    - `match_maturity_to_horizon: bool = False`
    - `min_rating: float | None = 4.0`
    - `benchmark_proxy_codes: dict[str, str]`
  - Add 7 new categories to `expense_ceilings`
  - Refactor settings caching: `_base_settings()`, dynamic `get_settings(rule_set=None)` with per-rule-set cache, and `cache_clear()` that resets both base and per-set caches.
- [ ] **`src/wealth_advisor/domain/rulesets.py`**:
  - Define `RULE_SETS["set1"]` (reproducing current defaults).
  - Define `RULE_SETS["set2"]` (implementing Set 2 rules).
  - Compute `RULE_SETS["combined"]` dynamically via `merge_rule_sets()`.
  - Implement request-scoped `ContextVar` management (`use_rule_set(name)`, `get_active_rule_set()`, `set_active_rule_set()`).

### Phase B: Domain Rules Integration
- [ ] **`src/wealth_advisor/domain/allocation.py`**: Read `s.allocation_bands` and `s.equity_cap_horizon_lt_5y`.
- [ ] **`src/wealth_advisor/domain/categories.py`**: Add 7 new categories; implement `min_horizon(category, s)`; filter `categories_for()` by `s.allowed_categories`.
- [ ] **`src/wealth_advisor/guardrails/suitability.py`**:
  - Positive alpha check on active equity when `s.require_positive_alpha`.
  - Debt maturity check against goal horizon when `s.match_maturity_to_horizon`.
  - Rating check conditional on `s.min_rating is not None`.
  - Use `min_horizon(category, s)`.
- [ ] **`src/wealth_advisor/domain/subscores.py`**:
  - Fundamental score weights from `s.fundamental_weights`.
  - Fit score beta penalty when `s.max_beta` exceeded.
  - Liquidity/cost score trusted AMC bonus.
  - Conflict flags: manager tenure < `s.min_manager_tenure_years`, YTM > category median + `s.ytm_outlier_pp`.

### Phase C: Data & Metrics
- [ ] **`src/wealth_advisor/domain/metrics.py`**: Add `beta(nav, benchmark_nav)` and `alpha(nav, benchmark_nav, risk_free_rate)`.
- [ ] **`src/wealth_advisor/data/store.py`**:
  - Add `manager_since`, `ytm`, `modified_duration`, `avg_maturity_years` to `FundRecord`.
  - Add `alpha`, `beta`, `pct_low_vol` to `FundStats`.
  - Column migration in `MetadataStore.__init__`.
  - Update `load_funds_csv`.
- [ ] **`src/wealth_advisor/data/stats.py`**: Compute alpha, beta, `pct_low_vol` in `compute_all_stats()`.
- [ ] **`src/wealth_advisor/data/demo_seed.py`**: Add new categories and manager/YTM/maturity fields.
- [ ] **`src/wealth_advisor/agents/tools.py`**: Expose facts (`alpha`, `beta`, `manager_tenure_years`, `ytm`, `modified_duration`, `avg_maturity_years`, `amc`) with citations.
- [ ] **`data/curated_funds.csv`**: Add new columns and rows for verified funds.

### Phase D: Rule Catalog & Explainability
- [ ] **`src/wealth_advisor/domain/rule_catalog.py`**: Catalog definitions with live dynamic examples, `describe_rule_set(name)`, and `compare_rule_sets()`.

### Phase E: Pipeline, Compliance & API/CLI
- [ ] **`src/wealth_advisor/pipeline.py`**: `advise(..., rule_set=...)` wraps execution with `use_rule_set()`, logs rule set name and provenance into audit trail.
- [ ] **`src/wealth_advisor/guardrails/compliance.py`**: Rule set disclosure note, merge notes for Combined, ELSS 3-year lock-in disclosure.
- [ ] **`src/wealth_advisor/audit/replay.py`**: Re-apply recorded rule set during replay.
- [ ] **`src/wealth_advisor/api/main.py`**: Add `GET/POST /api/config/rule-set`, `GET /api/rules`, `GET /api/rules/compare`, and `rule_set` parameter in `AdviseRequest`/`AdviseResponse`.
- [ ] **`src/wealth_advisor/cli.py`**: Support `--rule-set` in `advise` and add `rules` subcommand.

### Phase F: Frontend UI
- [ ] **`frontend/src/components/RulesViewer.tsx`**: Drawer modal with tabs for Set 1, Set 2, Combined, and Compare (with diff-only toggle).
- [ ] **`frontend/src/App.tsx`**: 3-way toggle next to data-mode toggle, rule-set badge, and provenance inspector.

### Phase G: Test Suite & Verification
- [ ] Create `tests/test_rulesets.py`, `tests/test_rule_merge.py`, `tests/test_rule_catalog.py`.
- [ ] Update `tests/test_api.py`.
- [ ] Verify all 104 existing tests + all new tests pass.
- [ ] Frontend build verification (`npm run build`).

---

## 4. How to Resume

To resume immediately when instructed:
1. Open this `CHECKPOINT.md` and `docs/plans/fund-rule-sets-plan.md`.
2. Proceed directly with **Phase A**: edit `src/wealth_advisor/config.py` and create `src/wealth_advisor/domain/rulesets.py`.
3. Follow through Phases B through G in order.
