# AI Personal Wealth Advisor — Incremental Implementation Plan

Source design: [course_content.md](course_content.md). This plan turns it into small milestones.
Each milestone is **independently runnable and testable** and builds on the previous one. Do not start milestone N+1 until N's "Done when" checklist passes.

---

## 0. Ground rules (apply to every milestone)

1. **LLM never produces a number.** Risk scores, weights, allocations, drift, returns, CAGR, expense ratios all come from deterministic code or a retrieved/cited source.
2. **Evidence ≠ judgment.** Researchers gather, Analyst scores, Decision Maker decides, Compliance gates. Keep them separate modules.
3. **Recommend only.** No trade execution, no broker order APIs, ever.
4. **Every output carries citations** (`source`, `as_of_date`, `id`) or is flagged `no_data`.
5. **Everything is replayable.** Persist inputs, tool calls, model + prompt version, outputs (append-only).
6. **Test first for the deterministic core**; for LLM parts use recorded fixtures (record/replay), `temperature=0`, Pydantic-validated structured output.
7. **Vertical slices.** Prefer a thin end-to-end path (mutual funds only) before adding breadth (more asset classes).

### Suggested stack (swap freely)

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Ecosystem for agents/data |
| Models/validation | Pydantic v2 | Structured LLM output, schemas |
| LLM access | Thin `LLMClient` interface (provider-agnostic) | Swap provider, cache, mock in tests |
| Storage (start) | SQLite (+ SQLAlchemy) | Zero-ops; migrate to Postgres at M15 |
| Vector store (start) | Chroma or FAISS (local) | Simple; can move to pgvector later |
| Orchestration | Plain Python + `asyncio` first; LangGraph optional later | Understand the pattern before adopting a framework |
| Data | pandas, numpy | Return/risk metrics |
| API | FastAPI | Only after core works in CLI |
| Frontend | React + TypeScript + Vite | Interactive UI, ecosystem for charts (recharts) and components |
| Tests | pytest, pytest-asyncio, hypothesis (for math) | |
| Quality | ruff, mypy, pre-commit | |

### Alignment with `AgenticAISystemDesignBatch2/capstone_project`

The capstone (`educosys_claude/`) is a LangChain/async code assistant. We follow its conventions where they fit, and deviate deliberately where this domain needs it.

| Aspect | Capstone | This project | Decision |
|---|---|---|---|
| Package layout | Feature folders (`agent/`, `llm/`, `memory/`, `tools/`, `tasks/`, `observability/`) | `src/wealth_advisor/` with `llm/`, `domain/`, `models/`, later `agents/`, `rag/`, `audit/` | Same idea; we use `src/` layout |
| LLM access | `llm/factory.py` (`get_llm`, `get_embedder`, provider switch) | `LLMClient` protocol + `OpenAIClient`, `CachedLLM`, `FakeLLM` | **Add** `get_llm()` factory (provider switch, embedder) in M3/M5; keep protocol for testability |
| Config | `config.yaml` + loader | pydantic-settings + `WEALTH_*` env | Keep (typed, env overrides); thresholds stay in code |
| Logging | `observability/logger.py`, per-module `get_logger` | `logging_setup.py` with `run_id` | Keep; rename/move to `observability/` when tracing grows (M16) |
| Tracing | LangChain logging only | Langfuse `CallbackHandler` in `OpenAIClient` | Ours is ahead; reuse for agents |
| Agents | LangChain agent built in `agent/factory.py`, `@tool` functions, minimal-privilege tools per task type | None yet | **Follow**: `agents/<name>/factory.py`, `@tool` wrappers over `domain/` functions, per-agent tool allow-list (M3, M6) |
| Async | `ainvoke`, async REPL | Sync so far | Stay sync through M9; introduce async at M12 (`asyncio.gather`) |
| Memory | SQLite checkpointer + `thread_id` sessions | Not needed yet | Reuse the pattern for profiling chat (M3) and daily-loop state (M10) |
| Task orchestration | `tasks/` planner, SQLite task store, state machine | None | Model M10 state machine (HOLD/REBALANCE/ESCALATE) on the same SQLite-backed pattern |
| Skills / MCP | `skills/`, `mcp/` | None | Out of scope |
| Tests | None visible | pytest + hypothesis + fakes | Keep; stricter than capstone by design |
| Python / deps | >=3.12 | >=3.11,<4.0, Poetry | Keep |

### Suggested repo layout (new folder, e.g. `wealth_advisor/`)

```
wealth_advisor/
  pyproject.toml
  src/wealth_advisor/
    config.py            # settings, thresholds (single source of truth)
    llm/                 # LLMClient interface, cache, fakes
    models/              # Pydantic domain models
    domain/              # DETERMINISTIC: risk, allocation, drift, metrics, scoring
    data/                # AMFI/NAV ingestion, metadata store, market data cache
    rag/                 # chunking, embeddings, vector store, hybrid retrieval, rerank
    agents/              # profiling, ingestion, research/*, news, sentiment, analyst, decision, compliance, monitoring
    orchestration/       # supervisor, dossier (blackboard), scheduler, event bus
    guardrails/          # input validation, grounding validator, suitability, disclosures
    audit/               # append-only decision log, replay
    api/                 # FastAPI (late)
    cli.py
  tests/
  data/                  # raw downloads, sqlite db, vector index (gitignored)
  docs/
```

---

## 1. Domain defaults to lock in early (expert decisions)

These are the numbers/rules the deterministic engine will use. Put them in `config.py`; every one is tunable and unit-tested. They are **starting assumptions, not advice** — review before relying on them.

### 1.1 Risk score (1–10) — weights from the design

| Factor | Weight | Suggested mapping to 1–10 (higher = more risk capacity) |
|---|---|---|
| Investment horizon | 30% | <1y→1, 1–3y→3, 3–5y→5, 5–7y→7, 7–10y→8, >10y→10 |
| Stated risk preference | 25% | Capital preservation→2, Conservative→4, Moderate→6, Growth→8, Aggressive→10 |
| Savings rate & income stability | 20% | Savings rate bands × income-stability multiplier (salaried/stable vs variable) |
| Existing allocation / diversification | 15% | Concentration (HHI) and existing equity share; unknown → neutral 5 |
| Liquidity / emergency fund | 10% | Months of expenses covered: <3→2, 3–6→5, 6–12→8, >12→10 |

Category: **Conservative** 1–3.9, **Moderate** 4–6.9, **Aggressive** 7–10.

### 1.2 Allocation bands (equity / debt+hybrid / gold / other)

| Category | Equity | Debt/Hybrid | Gold | Alternatives |
|---|---|---|---|---|
| Conservative | 15–30% | 70–85% (per design) | 0–10% | 0% |
| Moderate | 40–60% | 30–45% | 5–10% | 0–5% |
| Aggressive | 70–85% | 5–20% | 5–10% | 0–10% |

**Capacity override (important, from real-world practice):** risk *tolerance* (what user says) is capped by risk *capacity* (horizon, emergency fund). Hard caps:

- Horizon < 1y → equity max 0–5%
- Horizon 1–3y → equity max 20%
- No emergency fund (<3 months) → flag and cap equity at 30% until fixed

### 1.3 Instrument suitability filters (defaults, mutual funds first)

- Direct plans preferred (lower expense ratio); flag regular plans.
- Min track record 5 years for equity/hybrid funds (3 for debt); min AUM floor (e.g. ₹500 Cr per design example, ₹100 Cr for small categories).
- Expense-ratio ceiling **relative to category** (e.g. index funds ≤ ~0.5%, active equity ≤ ~1.0% direct — tune from data).
- Rating floor ≥ 4★ where a rating exists (design example); exclude user-excluded categories.
- Debt: prefer high-credit-quality, avoid long-duration for short horizons (match duration ≤ horizon).
- Concentration: max 1–3 instruments per bucket (per design), max ~30% in any one fund, max ~5–10% in any single stock (when equities are added).

### 1.4 Analyst score rubric (weights from design)

`Fundamental 35% · Fit 25% · News 20% · Sentiment 10% · Liquidity & cost 10%`

- **Computed in code** from normalized 0–100 sub-scores (e.g. category percentile of 3y/5y *rolling* returns, volatility, max drawdown, expense-ratio percentile, rating).
- LLM only contributes *tags* (news materiality: none/low/high; sentiment label) and the *prose* pros/cons. Code converts tags to points.
- Prefer **rolling returns and risk-adjusted metrics (Sharpe/Sortino, max drawdown)** over point-to-point trailing returns; never rank on last-1-year return alone (recency bias).

### 1.5 Decision rules

- Action thresholds on final score (e.g. ≥70 BUY/INCREASE, 50–70 HOLD, 35–50 DECREASE, <35 EXIT) — configurable.
- **Conviction delta** (anti-overtrading): REBALANCE only if |Δscore| ≥ ~10 points **or** a hard event (rating downgrade, manager exit, SEBI action).
- **Drift bands (5/25 rule):** rebalance a bucket if absolute drift > 5 percentage points, or relative drift > 25% of its target weight.
- **ESCALATE** if analyst confidence < threshold, or any section is `no_data`, or sources conflict materially.
- Always include exit-load / tax-impact warning before suggesting a sell (V3 computes it; MVP only warns).

### 1.6 Data sources (verify licensing and URLs when implementing)

- **Mutual funds:** AMFI daily NAV file (`NAVAll.txt`) is the authoritative free source; `mfapi.in` is a convenient community API (dev only, not licensed for production claims). Fact sheets: AMC websites / AMFI.
- **Equities (V2):** NSE/BSE bhavcopy for EOD prices; `yfinance` (`.NS`/`.BO`) for dev prototyping only.
- **Rates/debt (V2):** RBI policy rate, bank FD rate pages, G-sec yields (RBI/CCIL).
- **Gold (V2):** Gold ETF/Sovereign Gold Bond NAV/price via exchange data.
- **News (V2):** a licensed news API or RSS; **Sentiment (V2):** start with news-derived sentiment, no social scraping.
- Store `source`, `fetched_at`, `as_of` for **every** record — this is what makes citations possible.

---

## 2. Roadmap overview

| Phase | Milestones | Outcome |
|---|---|---|
| **Foundations** | M0–M2 | Skeleton + domain models + fully tested deterministic engine (no LLM) |
| **MVP (text query + MF research)** | M3–M9 | "I have ₹50,000, low risk, 5 years" → cited mutual-fund shortlist with disclosures, logged & replayable |
| **MVP+ Daily loop** | M10–M11 | Daily 08:00 re-run, HOLD/REBALANCE/ESCALATE, portfolio CSV + drift |
| **V2 breadth** | M12–M15 | All asset classes + news/sentiment via fan-out, statements, event-driven monitoring, API |
| **Frontend** | M16 | Rich interactive React web application |
| **Hardening** | M17 | Evaluation, hallucination tests, observability, security |
| **V3** | M18 | Tax-aware, broker/CAS sync, multi-goal |

---

## PHASE A — Foundations

### M0 — Project skeleton  (STATUS: done)
**Goal:** a repo where tests, linting, config, and an LLM abstraction work.

- [x] Create package, `pyproject.toml` (poetry-core backend), ruff/mypy (clean)/pytest, `.pre-commit-config.yaml` (run `poetry run pre-commit install` once).
- [x] `config.py` with all thresholds from §1 (pydantic-settings; env overrides).
- [x] `LLMClient` protocol: `complete(messages, schema=None) -> parsed model`, with (a) real provider adapter, (b) `FakeLLM` for tests, (c) on-disk response cache keyed by (model, prompt hash).
- [x] Structured logging with a `run_id` carried through all calls. (`run_id` is not yet attached to Langfuse traces.)
- [x] `cli.py` with `hello` and `llm-ping` commands.
- [x] Add `get_llm()` factory (`llm/factory.py`, `WEALTH_LLM_PROVIDER` = openai|anthropic) returning our `LLMClient`.

**Done when:** `pytest` green, `FakeLLM` returns a Pydantic object, a real call works with an API key, cache hit avoids a second call.

### M1 — Domain models  (STATUS: done)
**Goal:** the shared vocabulary. No logic yet.

- [x] `Goal` (amount, horizon_years, risk_preference, purpose, excluded_categories).
- [x] `UserFinancialProfile` — **versioned, immutable rows** (`profile_id`, `version`, `created_at`, inputs, `risk_score`, `category`, `allocation_band`).
- [x] `Instrument` (id/ISIN, name, asset_class, category, plan_type), `Citation` (source, url/doc_id, as_of, snippet).
- [x] `Fact` = value + `Citation` (the only way a number may enter a dossier).
- [x] `DossierEntry`, `ResearchDossier` (fields: equity, mf, commodity, fixed_income, alt, news_context, sentiment; each section may be `NoData(reason)`).
- [x] `AnalystVerdict` (pros, cons, score, sub_scores, confidence, change_conditions).
- [x] `Recommendation` (per instrument: action, target_weight, rationale, citations), `DecisionRecord`.

**Done when:** models serialize/deserialize round-trip; a `Fact` without a citation fails validation.

### M2 — Deterministic engine (the trust core)  (STATUS: done; 18 tests, ~97% domain coverage)
**Goal:** all financial math as pure, unit-tested functions. **No LLM, no network.**

- [x] `compute_risk_score(inputs) -> RiskResult` using §1.1 (returns per-factor breakdown for explainability).
- [x] `allocation_band(category, horizon, emergency_months)` applying §1.2 caps.
- [x] `target_allocation(band)` → bucket weights (midpoint; debt takes the remainder). Takes no `goal` argument.
- [x] `size_positions(shortlist, bucket_weight, total_amount)` → proportional to score; caps come from settings, not a parameter.
- [x] `compute_drift(current_holdings, target)` with the 5/25 rule.
- [x] `rebalance_plan(holdings_value, target)` → rupee deltas (bands come from settings). Rebalances all buckets when any one breaches.
- [x] Fund metrics from NAV series: CAGR, rolling returns, annualized volatility, max drawdown, Sharpe/Sortino. Downside capture not done (optional). Risk-free rate defaults to the `risk_free_rate` setting when not passed.
- [x] `score_instrument(sub_scores, weights)` → final 0–100.

**Tests:** hypothesis property tests — weights always sum to 100%, allocation never exceeds caps, drift is symmetric, risk score monotonic in horizon, identical input → identical output.

**Done when:** ≥90% coverage on `domain/`, every function documented with a worked numeric example in its docstring.

---

## PHASE B — MVP: text query + mutual fund research

### M3 — Profiling Agent (natural language → structured goal)  (STATUS: done, MVP slice)
**Goal:** "₹50,000, low risk, 5 years" → validated `Goal` + `UserFinancialProfile` v1.

- [x] Prompt + Pydantic schema for extraction (amount, horizon, risk preference, exclusions).
- [x] **Confirm-back step:** agent restates the parsed goal in plain language; CLI asks y/n/edit. Risk preference must map to the fixed taxonomy (§1.1), never free text.
- [x] Ask follow-ups only for missing mandatory fields (amount, horizon, risk).
- [x] Call `compute_risk_score` as a **tool**; LLM never computes it.
- [x] Input guardrail v0: reject out-of-scope asks (leverage, derivatives, intraday trading, "execute this trade").

**Done when:** 20 varied phrasings (incl. ₹/lakh/crore, "3 saal", ambiguous risk words) parse correctly using recorded fixtures; out-of-scope queries are rejected with a clear message.

### M4 — Mutual fund data layer  (STATUS: done, MVP slice)
**Goal:** trusted, dated, structured fund data in a local DB.

- [x] Ingest AMFI NAV file → tables: `scheme` (code, ISIN, name, AMC, category, plan, option), `nav` (scheme, date, nav).
- [x] Idempotent daily loader with validation (no negative/zero NAV, date gaps flagged, ISIN mapping).
- [x] Add static/semi-static attributes: expense ratio, AUM, benchmark, launch date, rating, manager — from fact sheets / AMC data. Start with a hand-curated CSV for ~30–50 funds across categories (liquid, short-duration, corporate bond, hybrid, index, large-cap, flexi-cap, mid-cap); every row has `source` + `as_of`.
- [x] `MetadataStore` query API with **exact numeric filters** (expense ≤ X, AUM ≥ Y, rating ≥ Z, category in …).
- [x] Use M2 metrics to precompute per-fund stats and category percentiles.

**Done when:** `python -m wealth_advisor.cli funds --category "Short Duration" --max-expense 0.8` returns cited, dated rows.

### M5 — RAG pipeline (offline ingest + hybrid retrieval)  (STATUS: done, MVP slice)
**Goal:** research documents retrievable with citations.

- [x] Collect a small corpus: fund fact sheets (PDF), SEBI circulars, rating reports for the curated funds.
- [x] Section-aware chunking; metadata on every chunk (`isin`, `doc_type`, `as_of`, `source_url`).
- [x] Embed + store (versioned collection so re-ingest doesn't silently overwrite).
- [x] **Hybrid retrieval:** metadata filter (structured store, exact) ∩ vector similarity (semantic), then rerank (cross-encoder or simple recency/rating boost to start).
- [x] Every returned chunk exposes a `Citation`.
- [x] Stale-doc handling: down-rank or drop docs older than N days for time-sensitive facts.

**Done when:** retrieval eval on ~20 hand-written questions reaches target recall@k; each result has source + date.

### M6 — Mutual Fund Research Agent + grounding validator  (STATUS: done, MVP slice)
**Goal:** the first specialist, producing a cited `dossier.mf[]`.

- [x] Agent receives: goal, risk band, allowed categories (from allocation band), constraints.
- [x] Tools: `query_funds` (M4), `retrieve_docs` (M5), `get_metrics` (M2). The agent selects candidates and writes `DossierEntry`s containing only `Fact`s.
- [x] **Grounding validator** (`guardrails/grounding.py`): extract every fund name / number in agent output and match against the citation set for this run. Unmatched → strip & regenerate once → else mark `no_data`.
- [x] Agent factory interface: `ResearcherConfig(asset_class, persona, tools, output_field)` so later asset classes are config, not code.

**Done when:** adversarial test — prompt the agent to invent a fund / expense ratio — the validator blocks 100% of seeded fabrications.

### M7 — Analyst Agent (evidence → pros/cons + score)  (STATUS: done, MVP slice)
**Goal:** turn the dossier into `AnalystVerdict`s.

- [x] Code computes sub-scores (§1.4) from facts; LLM writes pros/cons and `change_conditions`, each bullet citing fact ids.
- [x] Final score = `score_instrument` (M2), never LLM-generated.
- [x] Surface conflicts explicitly in `cons` (e.g. strong returns but high expense / manager change) — never average them away.
- [x] Confidence = f(data completeness, number of conflicts, doc staleness) — deterministic.

**Done when:** same dossier → same score every run; removing a data field lowers confidence; pros/cons without citations are rejected.

### M8 — Decision Maker + Suitability + Compliance (end-to-end MVP)  (STATUS: done, MVP slice)
**Goal:** a complete recommendation for the original query.

- [x] Decision Maker: filter by hard suitability (§1.3) → rank by score → shortlist 1–3 per bucket → `size_positions` (M2) → action per instrument + rationale with citations. Use the strongest model tier here only.
- [x] Suitability rule engine (deterministic): block output violating goal/risk/exclusions/caps.
- [x] Post-generation validator (second hallucination layer) before Compliance.
- [x] Disclosure injection (AI-generated, educational, past performance ≠ future results, risk disclosures link). If not SEBI-RIA operated → "educational, not personalized advice" framing.
- [x] Optional: bull-vs-bear debate round, **off by default**, triggered only on conflicting signals on large allocations.
- [x] CLI renders a clean table: instrument, bucket, weight, ₹ amount, expense ratio, rating, 5y CAGR, rationale, citations.

**Done when (MVP milestone):** `cli advise "I have ₹50,000 to invest, low risk, 5 years"` returns a cited shortlist; no uncited number in the output; suitability engine blocks a deliberately bad shortlist.

### M9 — Audit trail & replay  (STATUS: done, MVP slice)
**Goal:** answer "why did it recommend X?" months later.

- [x] Append-only `audit_log` (SQLite triggers or hash-chained rows to detect tampering): run_id, agent, model + prompt version, tool calls (args + result hashes), dossier snapshot, verdicts, `DecisionRecord`.
- [x] `cli replay <run_id>` re-runs deterministic steps from logged inputs and diffs against stored outputs.
- [x] Store `UserFinancialProfile` versions; each recommendation links to the exact profile version.

**Done when:** replaying a run reproduces identical scores and allocations.

---

## PHASE C — MVP+: the daily loop

### M10 — Daily recalibration cycle
**Goal:** the "living recommendation".

- [x] Persist `Recommendation` as the current position per user/goal.
- [x] Scheduler (APScheduler or cron) fires 08:00 IST: refresh NAV data → supervisor → dossier (skip unchanged via cache keyed by data hash) → analyst → decision maker.
- [x] Decision Maker also receives yesterday's recommendation + current holdings; compute **conviction delta** (§1.5).
- [x] Output state machine: **HOLD** (log only, silent), **REBALANCE** (new plan → compliance → notify), **ESCALATE** (human review queue).
- [x] Anti-overtrading: min-delta threshold, cool-down between rebalances, drift-band gating.
- [x] Notification abstraction (console → email); recommend-only, user must confirm.

**Done when:** simulate 30 days of replayed NAV data — REBALANCE fires only on genuinely material changes; HOLD days are logged as "checked, nothing changed".

### M11 — Portfolio upload (CSV) & drift
**Goal:** compare reality vs. target.

- [x] CSV schema (scheme/ISIN, units, avg cost, as-of); validator with row-level error report.
- [x] Valuation using latest NAV; current allocation by bucket.
- [x] Wire drift (M2) into the daily loop; produce rebalance deltas as *amounts*, with exit-load warning.
- [x] Update risk-score "existing allocation" factor from the real portfolio → new profile version.

**Done when:** a sample portfolio yields correct drift %, and the 5/25 rule triggers/doesn't trigger as expected in tests.

---

## PHASE D — V2: breadth (add one thing at a time)

### M12 — Supervisor fan-out + blackboard dossier
**Goal:** parallelism and the multi-agent shape.

- [x] `ResearchSupervisor`: decide which researchers to dispatch from risk band/horizon (conservative + 6 months → skip equity).
- [x] `asyncio.gather` with per-agent timeout; timeout ⇒ section = `NoData("timeout")`, never silently dropped.
- [x] Dossier collation = schema validation + `no_data` markers only (no opinions).
- [x] Refactor MF agent behind the agent-factory interface.

**Done when:** adding a dummy researcher requires only a new `ResearcherConfig`; a forced timeout produces a `no_data` section and the pipeline still completes (confidence lower, possibly ESCALATE).

### M13 — Add asset classes, one per sub-step (each ships alone)
Do them in this order (simplest data first) and re-run the full evaluation (M16 harness) after each.

- [x] **13a FD/Debt researcher:** bank FD rates, tenure, early-withdrawal penalty, DICGC insurance limit; debt MF credit quality/duration.
- [x] **13b Commodity (gold) researcher:** Gold ETF / SGB; price trend, tracking error, expense ratio.
- [x] **13c Equity researcher:** valuation (P/E vs sector), earnings trend, debt/equity, promoter pledging, insider activity; keep to a **large-cap/Nifty-50 universe** first.
- [x] **13d Alternatives researcher:** REITs/InvITs only (liquid, listed); skip PMS/AIF.
- [x] **13e News Agent:** headlines/macro flags per instrument, deduped, with `materiality` tag (none/low/high) — events like RBI rate decision, Budget, SEBI circulars.
- [x] **13f Sentiment Agent:** news-derived sentiment score + trend + `sample_size`; discount small samples (per design).

**Done when (each):** new section appears in dossier with cited facts only; analyst rubric consumes it; grounding validator still passes.

### M14 — Document ingestion (bank statements & CDSL/NSDL snapshots)
**Goal:** auto-build the financial profile.

- [x] Parsers: start with **text-based PDFs/CSV** for 1–2 banks (OCR comes after); CAS (CAMS/KFintech) PDF/JSON for holdings.
- [x] Layout-aware OCR for scanned statements as a later sub-step.
- [x] Transaction categorizer: rules first (salary, EMI, rent, SIP debit, discretionary), ML/LLM fallback; low confidence → **user-confirmation queue**.
- [x] Derive: monthly income, expenses, savings rate, income stability, emergency-fund months, existing SIPs.
- [x] Feed into `compute_risk_score`; create a new profile version.
- [x] **PII handling:** redact PAN/account numbers before any LLM call; store files encrypted; configurable retention.

**Done when:** a sample statement yields a savings rate within tolerance of a hand-calculated value; a misclassified-SIP test shows the confirmation queue triggers.

### M15 — Event-driven monitoring + API
- [x] Event bus (start in-process queue → Redis Streams/Kafka later): topics for market data, fund events (rating change, manager exit), portfolio ticks.
- [x] Monitoring Agent: drift calc, threshold rules, anomaly vs baseline; **alert de-duplication** (cool-down, batching) → triggers unscheduled supervisor run.
- [x] FastAPI: auth (JWT/OIDC), rate limiting, PII redaction middleware, endpoints for chat, upload, goals, recommendations, alerts.
- [x] Migrate SQLite → Postgres; vector store → pgvector (optional).

**Done when:** a simulated rating downgrade triggers an out-of-cycle run and exactly one alert (deduped).

---

## PHASE D.5 — Frontend (React)

### M16 — React Web Application
**Goal:** Rich, interactive user interface replacing the CLI.

- [x] **Scaffold & Setup:** Initialize React app (Vite + TypeScript), configure Tailwind CSS and a component library (e.g., shadcn/ui).
- [x] **State Management:** React Query for API data fetching/caching, Zustand for local UI state.
- [x] **Chat Interface:** Conversational UI for the Profiling Agent (M3) to collect goals. Render structured outputs (recommendations, citations) as rich widgets, not just text.
- [x] **Dashboard:** Visual portfolio summary (recharts/Chart.js for asset allocation pie charts, drift bar charts). Display current risk score and active goals.
- [x] **Portfolio Upload:** Drag-and-drop zone for CSV/PDF statements (M11, M14) with upload progress and inline validation error reporting.
- [x] **Action Center:** UI for HOLD/REBALANCE/ESCALATE notifications. One-click user confirmation for rebalancing plans.
- [x] **Integration:** Connect to FastAPI endpoints (M15) with proper authentication flows.

**Done when:** A user can log in, upload a portfolio, chat to set a goal, and view the recommended allocation and historical drift in the dashboard.

---

## PHASE E — Hardening

### M17 — Evaluation, safety, observability (start a minimal version at M6; expand here)
- [ ] **Golden set** of ~30 user scenarios with expected category, allocation band, and "must not recommend" lists.
- [ ] **Hallucination suite:** seeded fake funds/numbers; assert 0 reach output.
- [ ] **Regression snapshots:** deterministic scores/allocations pinned; changes require review.
- [ ] **Retrieval evals:** recall@k, citation correctness, staleness handling.
- [ ] **Backtest-lite:** run the recommend→daily-loop over a historical window; track turnover, drawdown vs. a simple benchmark (e.g. 60/40 or Nifty + liquid). Goal is *sanity and churn control*, not alpha.
- [ ] Tracing/metrics: per-agent latency, token cost per run, timeout rate, `no_data` rate, escalation rate.
- [ ] Prompt-injection tests (malicious text inside uploaded statements/news).
- [ ] Security: secrets management, least privilege, audit log integrity check.

---

## PHASE F — V3

### M18 — Advanced
- [ ] **Tax-aware rebalancing** (verify current LTCG/STCG and debt-fund taxation rules and exit loads at implementation time; encode as versioned rules, unit-tested).
- [ ] **Broker/CAS live sync** (read-only).
- [ ] **Multi-goal optimization** (retirement, child education, emergency fund) with goal-specific horizons and bucketing.
- [ ] **Personalized retrieval ranking** from outcome feedback.
- [ ] **SEBI IA integration path** (human adviser review, registered-entity workflow, record retention).

---

## 3. Per-milestone working checklist (copy for each)

```
- [ ] Write/extend tests first for the deterministic part
- [ ] Implement smallest slice
- [ ] Add/refresh recorded LLM fixtures
- [ ] Grounding/suitability validators still pass
- [ ] Audit log captures the new step
- [ ] CLI command or script demonstrating it
- [ ] Short note in docs/ of what changed + known gaps
- [ ] Tag the commit (m<N>-done)
```

## 4. Key risks and how the plan mitigates them

| Risk | Mitigation |
|---|---|
| LLM fabricates fund names/numbers | Facts-with-citations model (M1), grounding validator (M6), post-generation validator (M8), seeded-fabrication tests (M16) |
| Stale or wrong market data | `as_of` on every record, staleness rules (M5), validation in loaders (M4) |
| Over-trading from noisy daily scores | Conviction delta + 5/25 drift bands + cool-down (M10) |
| Recency-chasing recommendations | Rolling returns, risk-adjusted metrics, category percentiles (§1.4) |
| Unsuitable advice (e.g. equity for a 6-month goal) | Capacity caps (§1.2) + deterministic suitability engine (M8) |
| Regulatory exposure | Recommend-only, disclosures, human escalation, audit trail; treat output as educational unless operated by a SEBI-registered adviser |
| Scope explosion | Strict milestone gating; MF-only until M13 |
| Data licensing | Dev-only sources flagged in §1.6; swap for licensed feeds before any production use |

## 5. Suggested first three working sessions

1. **M0 + M1:** skeleton, config, `LLMClient`/`FakeLLM`, domain models.
2. **M2:** the deterministic engine with property tests (most valuable, no external dependencies).
3. **M3:** Profiling Agent via CLI — first moment the system "talks".

After that, follow the numbering; M8 is the first demo-worthy end-to-end result.
