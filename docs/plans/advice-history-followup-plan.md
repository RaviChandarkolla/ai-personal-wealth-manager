# Plan: Advice History + Grounded Follow-up Chat

Every `/api/advise` result gets saved, together with a snapshot of how the funds were chosen and rejected. The sidebar lists past runs and lets the user delete one. Under a selected result there's a chat box. A question like "why isn't fund X in the list?" is answered from checks the code runs on fund X against the run's original goal and rule set. The model only writes up those checks in plain language, so it can't make up reasons.

---

## Design Decisions (options offered → selected)

| # | Question | Options offered | Selected |
|---|----------|-----------------|----------|
| 1 | **History scope** — the app has no users/auth today. How should history be scoped? | (a) Single local user (all history in backend SQLite) — *recommended*: simplest; matches current stateless, no-auth design<br>(b) Per-browser client_id (UUID in localStorage sent as header) — separates history across browsers without real auth<br>(c) Browser-only (localStorage, no backend storage) | **(a) Single local user (all history in backend SQLite)** |
| 2 | **Delete semantics** — what happens to the hash-chained `audit_log` entries when a history item is deleted? | (a) Keep `audit_log` intact; only delete from new history + chat tables — *recommended*: preserves the tamper-evident audit chain<br>(b) Also delete audit entries (breaks chain verification) | **(a) Keep `audit_log` intact; only delete from new history + chat tables** |
| 3 | **Follow-up capability** — what should follow-up questions be able to do? | (a) Explain only (grounded Q&A over the saved advice + on-demand fund lookups/suitability checks) — *recommended*: e.g. "why is fund X not in the list" runs `check_fund` on X under the original goal/rule set<br>(b) Explain + allow re-running advice with modified inputs (e.g. "make it more aggressive") | **(a) Explain only** |
| 4 | **Follow-up history** — should the follow-up chat thread be saved and shown when reopening a history item? | (a) Yes, persist chat per run — *recommended*<br>(b) No, chat is ephemeral | **(a) Yes, persist chat per run** |
| 5 | **History UI** — where should history appear? | (a) Collapsible left sidebar listing past runs — *recommended*<br>(b) Separate "History" tab/drawer | **(a) Collapsible left sidebar listing past runs** |

---

## Phase 1: Saving history (backend)

1. Add a `history_db_path` setting in `config.py`. History goes in its own database file so the sidebar shows runs from both data modes (synthetic and real).
2. Create `src/wealth_advisor/history/store.py` with a `HistoryStore` class and two tables. Turn on `PRAGMA foreign_keys=ON` so deleting a run also deletes its chat messages.
   - **`advice_history`** has one row per run:
     - `run_id` (primary key), `created_at`, `title` (taken from the query or from amount/horizon/purpose)
     - `query`, `data_mode`, `rule_set`, `amount`, `horizon_years`, `risk_category`, `n_recommendations`
     - `response_json`: the full advice response
     - `context_json`: a snapshot of how the funds were chosen (see step 3)
   - **`followup_message`** holds the chat: `id`, `run_id` (linked to the run, deleted with it), `role` (user or assistant), `content`, `payload_json` (sources and funds looked up), `created_at`.
   - Methods: `save`, `list`, `get`, `delete`, `add_message`, `messages`.
3. Build `context_json` from `AdviceResult` in `pipeline.py`. It holds:
   - the target bucket weights and the goal (including excluded categories and horizon)
   - the candidate funds' IDs, categories and facts from the research step, plus the researcher's dropped funds and notes
   - each fund's analyst score
   - the funds the decision step rejected and why (`decision.rejected`)
   - the limits in force: maximum funds per bucket, maximum weight per fund, minimum ticket

## Phase 2: API endpoints in `api/main.py` (*depends on Phase 1*)

4. In `advise_endpoint`, call `HistoryStore.save(...)` after the response is built. If saving fails, log it but still return the advice.
5. Add four endpoints:
   - `GET /api/history?limit=&offset=` returns run summaries, newest first, with a follow-up count.
   - `GET /api/history/{run_id}` returns the saved advice response and its chat messages, or 404.
   - `DELETE /api/history/{run_id}` returns 204, or 404 if the run doesn't exist. It deletes the history row and its chat, not the audit log.
   - `POST /api/history/{run_id}/followup` takes `{question}` (1–1000 characters). It saves the user message and the assistant reply and returns `{message_id, answer, referenced_funds, sources}`.

## Phase 3: Follow-up agent (*can be built alongside Phase 2*)

6. Create `src/wealth_advisor/agents/followup.py` with a `FollowUpAgent` class. Each question goes through these steps:
   1. **Safety check.** Run the question through `check_input()` in `guardrails/input.py` and refuse it if it's flagged (e.g. a prompt-injection attempt).
   2. **Find the funds mentioned.** The model extracts fund names or codes from the question. Each one is matched against the fund database for the run's data mode, using `MetadataStore.search_master` / `query_funds` (as `/api/compare/schemes` does).
   3. **Explain why a fund is missing.** For each mentioned fund that wasn't recommended, a new `explain_exclusion(fund, context, goal, rule_set)` function returns the reasons that apply, from first to last stage:
      - its category is in the user's excluded list
      - its bucket got 0% in the target allocation
      - its category isn't used for that bucket (`categories_for`)
      - it wasn't among the 3 funds the researcher shortlisted per bucket
      - it fails one or more suitability rules: `check_fund()` is run inside `use_rule_set(run.rule_set)` and returns violations such as expense ratio, AUM, track record, horizon, alpha or maturity
      - it passed but scored lower than the lowest pick in its bucket, or the per-bucket fund limit was reached
      - it was rejected by the decision step with a recorded reason
   4. **Write the answer.** The model returns `{answer, fact_ids}` using only:
      - the explanation facts
      - the saved advice (recommendations with pros, cons, rationale and sources)
      - the last 6 chat messages
   5. **Clean up the answer.** Remove promotional wording with `scrub()` from `guardrails/compliance.py` and add a short "not investment advice" note.
7. Each question may use at most 2 model calls (`BudgetedLLM`). Wrap them in `trace_context(run_id=<original run>)` so the Langfuse session for the run also shows the follow-ups. If the call limit is reached, return the deterministic explanation without model wording.

## Phase 4: Frontend (*depends on Phase 2's API shape*)

8. In `store/useAppStore.ts`, add `activeRunId` and `historySidebarOpen`.
9. New `components/HistorySidebar.tsx`, a collapsible left panel:
   - Loads the list with React Query using key `['history']`.
   - Each item shows the title, date, data mode / rule set and number of funds.
   - Clicking an item sets `activeRunId`. The delete icon asks for confirmation, calls the delete endpoint, and refreshes the list. If the deleted run is the one on screen, it clears `activeRunId`.
10. New `components/FollowUpChat.tsx`, shown below the results:
    - Loads the run's saved messages, has an input box with a 1000-character limit, and shows a loading state while waiting.
    - Answers list the funds that were looked up, each linking to the existing fund detail page.
    - Some example questions to click, e.g. "Why isn't <fund> in the list?"
11. In `App.tsx`:
    - Show the selected history item if there is one, otherwise the latest `adviseMutation.data`.
    - After a new advise call succeeds, refresh the history list and select the new run.
    - Add a "New advice" button that clears the selection.

## Phase 5: Tests (*runs with Phases 1–3*)

12. `tests/test_history.py`:
    - Saving, listing and getting runs works on a temporary database.
    - Deleting a run also deletes its messages.
    - Deleting leaves the audit log's entries and hash-chain check for that run unchanged.
    - The API endpoints work through FastAPI's test client, including the 404 cases.
13. `tests/test_followup.py` uses `demo_conn` and `ScriptedLLM`:
    - `explain_exclusion` gives the right reason for each case: excluded category, expense ratio above the limit, horizon too short, outranked, not shortlisted.
    - A fund name that can't be matched gets a "fund not found" answer.
    - An injection attempt is refused.
    - Promotional wording is removed from answers.
    - The model-call limit falls back to the deterministic explanation.

---

## Relevant files

- `src/wealth_advisor/api/main.py`: hook into `advise_endpoint`, add the new endpoints; reuse `get_db_conn` and `open_db`.
- `src/wealth_advisor/pipeline.py`: `AdviceResult` is where `context_json` comes from.
- `src/wealth_advisor/guardrails/suitability.py`: reuse `check_fund` and `Violation`.
- `src/wealth_advisor/guardrails/input.py` and `guardrails/compliance.py`: reuse `check_input` and `scrub`.
- `src/wealth_advisor/domain/rulesets.py` and `domain/categories.py`: reuse `use_rule_set` and `categories_for`.
- `src/wealth_advisor/data/store.py`: reuse `MetadataStore.get_fund`, `search_master` and `query_funds` to find funds by name.
- `src/wealth_advisor/llm/base.py` and `llm/langchain_client.py`: reuse `BudgetedLLM`, `complete(schema=...)` and `trace_context`.
- `src/wealth_advisor/audit/log.py`: follow its schema style; leave the audit log alone.
- `src/wealth_advisor/config.py`: add `history_db_path`.
- `frontend/src/App.tsx` and `frontend/src/store/useAppStore.ts`: changed.
- `frontend/src/components/HistorySidebar.tsx` and `FollowUpChat.tsx`: new.
- `tests/fakes.py` and `tests/conftest.py`: reuse `ScriptedLLM` and `demo_conn`.

## Verification

1. Run `pytest` from `ai-personal-wealth-manager/`. The 104 existing tests plus the new ones should pass.
2. Manual check with backend and frontend running:
   - Run two advice requests (one synthetic, one real); both show in the sidebar.
   - Reopen an older run; it shows its saved result without calling the model again.
   - Ask "why is <a well-known fund that isn't listed> not in the list, explain to me"; the answer should name the specific rule and the numbers.
   - Reload the page; the chat is still there.
   - Delete the run; it disappears and `GET /api/history/{id}` returns 404.
3. Check that `AuditLog` still verifies its hash chain for the deleted run.
4. In Langfuse, follow-up calls should show up under the original run's session.

## Scope

- **Included:** history list, open, and delete per item; grounded follow-up Q&A; saved chat per run.
- **Not included:** user accounts or login, re-running advice from chat, "delete all", search or filter within history, streaming answers.
- History is saved only from `/api/advise`, not from the CLI.
