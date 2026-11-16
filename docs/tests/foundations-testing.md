# Running and Testing the Project (M0–M9)

All commands run from `ai-personal-wealth-manager/`. Sections 1–5 cover setup and the foundations (M0–M2); sections 6–8 cover running the MVP (M3–M9), manual questions, and unit tests.

## 1. Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"      # core + test tools
.venv/bin/pip install -e ".[llm]"      # only for the real LLM check (M0)
```

## 2. Automated tests

```bash
.venv/bin/pytest -q                                   # all tests
.venv/bin/pytest -q --cov=wealth_advisor.domain       # coverage of domain/ (target >= 90%)
.venv/bin/pytest -q -k risk                           # subset by name
.venv/bin/pytest -q --hypothesis-seed=0               # reproducible property tests
.venv/bin/ruff check src tests                        # lint
.venv/bin/mypy src                                    # types (optional)
```

Tests live in `tests/test_foundations.py`. What each covers:

| Milestone | Test | Verifies |
|---|---|---|
| M0 | `test_weights_sum_to_one` | Config weights sum to 1 |
| M0 | `test_fake_llm_and_cache` | `FakeLLM` returns a Pydantic object; second identical call is a cache hit and does not call the inner client |
| M1 | `test_fact_requires_citation` | A `Fact` without a citation is rejected; JSON round-trip is lossless |
| M1 | `test_recommendation_requires_citation` | A recommendation with no citations is rejected |
| M2 | `test_risk_worked_example` | Risk score 6.75 → Moderate for the documented example |
| M2 | `test_risk_monotonic_in_horizon`, `test_risk_deterministic_and_bounded` | Longer horizon never lowers the score; same input gives same output; score within 1–10 |
| M2 | `test_allocation_sums_and_caps`, `test_conservative_target` | Targets sum to 100; horizon and emergency-fund equity caps hold |
| M2 | `test_sizing_respects_caps`, `test_sizing_redistributes` | Max 3 instruments, ≤30% each, amounts in ₹500 steps, excess redistributed |
| M2 | `test_drift_rule`, `test_drift_relative_trigger`, `test_drift_symmetric` | 5pp absolute / 25% relative triggers; symmetry |
| M2 | `test_rebalance_plan` | Deltas bring buckets to target; no plan when within bands |
| M2 | `test_metrics`, `test_rolling_returns` | CAGR, max drawdown, volatility, Sharpe/Sortino, rolling returns; bad NAV rejected |
| M2 | `test_score_instrument` | Weighted score; missing or out-of-range sub-scores rejected |

## 3. Manual checks

### M0: CLI and logging

```bash
.venv/bin/python -m wealth_advisor.cli hello
# -> wealth-advisor ready (run_id=<12 hex chars>)
```

Config override:

```bash
WEALTH_LOG_LEVEL=DEBUG WEALTH_MIN_TICKET=1000 .venv/bin/python -c \
  "from wealth_advisor.config import get_settings as g; print(g().min_ticket, g().log_level)"
# -> 1000.0 DEBUG
```

### M0: real LLM call, tracing and cache

Prerequisites in `.env`: `OPENAI_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (and optionally `WEALTH_LLM_MODEL`).

```bash
.venv/bin/python -m wealth_advisor.cli llm-ping "Say hello in five words."   # miss: calls provider
.venv/bin/python -m wealth_advisor.cli llm-ping "Say hello in five words."   # hit: file in data/llm_cache, no new trace
```

Expected: first run prints a reply and a trace appears in Langfuse; the second prints the same reply with no new trace. The second run logs `cache hits=1 misses=0`, and `data/llm_cache/` holds one JSON file after both runs.

### M1: models

```bash
.venv/bin/python - <<'EOF'
from wealth_advisor.models import Fact, Citation
c = Citation(source="AMFI", as_of="2026-01-01")
f = Fact(id="f1", name="expense_ratio", value=0.45, citation=c)
print(Fact.model_validate_json(f.model_dump_json()) == f)   # True
try:
    Fact(id="f2", name="expense_ratio", value=0.45)
except Exception as e:
    print("rejected:", type(e).__name__)                     # ValidationError
EOF
```

### M2: engine end-to-end (profile → target → sizing → drift)

```bash
.venv/bin/python - <<'EOF'
from wealth_advisor.domain import *
from wealth_advisor.models import RiskPreference, Bucket

r = compute_risk_score(RiskInputs(horizon_years=5, risk_preference=RiskPreference.CONSERVATIVE,
                                  savings_rate=0.2, emergency_months=6))
print(r.score, r.category, r.factors)   # 6.25 Moderate (capacity lifts it above the stated preference)

band = allocation_band(r.category, horizon_years=5, emergency_months=6)
target = target_allocation(band)
print(target)

print(size_positions([Candidate(id="A", score=80), Candidate(id="B", score=60)],
                     target[Bucket.DEBT], total_amount=50_000))

print(rebalance_plan({Bucket.EQUITY: 6000, Bucket.DEBT: 4000},
                     {Bucket.EQUITY: 50, Bucket.DEBT: 50}))
EOF
```

Capacity override check: repeat with `horizon_years=0.5` and confirm the equity target is ≤ 5%; with `emergency_months=1`, ≤ 30%.

## 4. M0–M2 "Done when" checklist

- [ ] `pytest` is green (18 tests).
- [ ] `FakeLLM` returns a Pydantic object (`test_fake_llm_and_cache`).
- [ ] A real call works with an API key (`llm-ping`).
- [ ] A cache hit avoids a second call (`test_fake_llm_and_cache`, `data/llm_cache/`).
- [ ] A `Fact` without a citation fails validation.
- [ ] Models round-trip through JSON.
- [ ] `domain/` coverage is ≥ 90%.
- [ ] Property tests hold: weights sum to 100%, caps are never exceeded, drift is symmetric, risk is monotonic in horizon, results are deterministic.

## 5. Troubleshooting

- `ModuleNotFoundError: wealth_advisor`: re-run `pip install -e ".[dev]"` inside the venv.
- `ModuleNotFoundError: langchain_openai` or `langfuse`: install the `llm` extra.
- Hypothesis failures print a minimal failing example; fix the code, or pin it with `@example(...)`.
- `No fund data loaded`: run `seed-demo` (or `load-funds` + `ingest-amfi`) against the same DB you pass to `advise`.
- Tests import `tests.fakes`; `pythonpath = ["src", "."]` in `pyproject.toml` makes that work, so run pytest from the project root.

## 6. Running the MVP (M3–M9)

### Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,llm]"
```

`.env` needs the provider key (`OPENAI_API_KEY`, or `ANTHROPIC_API_KEY` with `WEALTH_LLM_PROVIDER=anthropic`), optionally `WEALTH_LLM_MODEL`, and the `LANGFUSE_*` keys if tracing is on. The SQLite file defaults to `data/wealth.db`; override with `WEALTH_DB_PATH` or `--db <file>` (placed before the subcommand).

Shorthand used below: `wa=".venv/bin/python -m wealth_advisor.cli"`.

### Step by step

```bash
# 1. Load the SYNTHETIC demo universe: 45 fake funds, NAV history, stats, fact sheets
$wa seed-demo

# 2. Query funds with exact filters (M4)
$wa funds --category "Short Duration" --max-expense 0.8
$wa funds --min-rating 4 --min-aum 1000 --plan direct

# 3. Ask for advice (M3, M6, M7, M8). Needs an LLM key.
$wa advise "I have ₹50,000 to invest, low risk, 5 years"
$wa advise -y "2 lakh for 3 saal, moderate risk"      # -y skips prompts

# 4. Audit and replay (M9)
$wa runs                       # list run ids
$wa replay <run_id>            # re-runs deterministic steps, prints OK/DIFF
$wa audit-verify               # checks the hash chain
```

Real data instead of the demo:

```bash
$wa load-funds my_funds.csv        # columns: code,name,category,source,as_of (+ isin,amc,plan,expense_ratio,aum_cr,rating,benchmark,launch_date,manager)
$wa ingest-amfi --file NAVAll.txt  # or omit --file to download from AMFI
```

`advise` is interactive on a terminal: it restates the parsed goal and asks `y/n/edit`, and asks follow-up questions for missing amount, horizon or risk. With `-y` or piped input it never prompts and exits with code 2 if a field is missing.

Exit codes: `0` ok, `2` out of scope / missing input / no data, `3` blocked by compliance.

## 7. Manual questions to ask

Run each with `$wa advise "<question>"` after `seed-demo`.

| Question | Expect |
|---|---|
| `I have ₹50,000 to invest, low risk, 5 years` | Restated goal (50,000, 5 years, conservative). Profile category Moderate (risk 6.25: capacity lifts it above the stated preference), so equity, debt and gold rows. Every row has a score, rationale and cites ids like `DEMO010:expense_ratio`; disclosures and the synthetic-data notice at the end. |
| `2 lakh for 3 saal, moderate risk` | Amount 200,000 and horizon 3 parsed from "lakh" and "saal". |
| `₹10,000 for 6 months, no risk` | Horizon 0.5 years, capital preservation. Equity target at most 5%; equity funds are rejected on horizon and the shortfall is reported as unallocated or escalated. |
| `1 lakh for 7 years, aggressive, no mid cap` | No Mid Cap fund in the output. |
| `I want to invest some money` | Asks follow-ups for the missing fields (interactive), or prints `Missing: ...` with `-y`. |
| `Buy Nifty call options with 5x leverage` | `Out of scope (...)`, exit code 2, no LLM call. |
| `Execute this trade now` | Rejected as trade execution. |

Things to check in any successful output:

- Only direct plans appear, with expense ratio under the category ceiling and AUM above the floor. Regular plans (Falcon), young funds (Cedar) and high-cost low-AUM funds (Maple) never appear.
- No instrument above 30% weight, at most 3 per bucket, amounts in multiples of ₹500, total at most the amount entered.
- Each rationale number matches a figure in the table; each row lists citation ids.
- Re-running the same question is served from `data/llm_cache` (log line `cache hits=`).
- `$wa replay <run_id>` ends with `REPLAY MATCHES`; `$wa audit-verify` prints `audit chain OK`.
- Tamper test (use a copy of the DB): drop the `audit_log_no_update` trigger in sqlite3, edit a payload, then `audit-verify` should report `BROKEN`.

## 8. Unit tests (M0–M9)

```bash
.venv/bin/pytest -q                                   # everything (97 tests)
.venv/bin/pytest -q tests/test_foundations.py         # M0-M2
.venv/bin/pytest -q tests/test_m3_profiling.py        # M3: 20 phrasings, missing fields, guardrail
.venv/bin/pytest -q tests/test_m4_data.py             # M4: AMFI parser, filters, CSV loader, CLI funds
.venv/bin/pytest -q tests/test_m5_rag.py              # M5: chunking, versioning, staleness, recall@3
.venv/bin/pytest -q tests/test_m6_research.py         # M6: grounding validator, fabrication tests
.venv/bin/pytest -q tests/test_m7_analyst.py          # M7: scores, confidence, cited prose
.venv/bin/pytest -q tests/test_m8_decision.py         # M8: end-to-end, suitability gate
.venv/bin/pytest -q tests/test_m9_audit.py            # M9: hash chain, replay, immutability
.venv/bin/pytest -q -k "fabrication or grounding"     # hallucination-related tests only
.venv/bin/pytest -q --cov=wealth_advisor --cov-report=term-missing   # coverage
.venv/bin/ruff check src tests && .venv/bin/mypy src  # lint and types
```

The MVP tests need no API key or network: they use `FakeLLM` and `tests/fakes.py::ScriptedLLM`, and an in-memory database seeded from the synthetic demo data (`tests/conftest.py`).

### M3–M9 "Done when" checklist

- [ ] 20 varied phrasings parse correctly; out-of-scope queries are rejected (M3).
- [ ] `funds --category ... --max-expense ...` returns sourced, dated rows (M4).
- [ ] Retrieval recall@3 is at least 0.9 on 20 questions; every hit has a source and date (M5).
- [ ] All seeded fabrications (invented fund, number, citation) are blocked (M6).
- [ ] Same dossier gives the same score; removing a field lowers confidence; uncited bullets are rejected (M7).
- [ ] `advise` returns a cited shortlist; the suitability gate blocks a bad shortlist (M8).
- [ ] Replay reproduces identical scores and allocations; tampering is detected (M9).
