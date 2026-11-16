# 360° Stock Report

On-demand, per-stock report built from `prompts/stock360/master_v2.md` (Step 0 + S1–S17, four parts).
Design and decisions: [stock-360-analysis-plan.md](plans/stock-360-analysis-plan.md).

**How it works**
- Python computes every number (Yahoo data, derived metrics, peers). Unfetched items show *Not available* / *Requires live verification* and are never guessed.
- Web research (OpenAI/Anthropic web search, Google News RSS fallback) supplies dated, URL-cited facts; manual inputs override them.
- The LLM only writes judgement text from cited facts (4 calls). Ungrounded numbers are rejected and retried, then replaced by deterministic text. The final action may differ from the quant verdict by at most one band.
- Without an LLM (or if it fails) a *partial*, mechanical-only report is produced.

**API** (`/api/stocks/...`): `POST {symbol}/report360` (200 cached / 202 job), `GET report360/jobs/{job_id}`, `GET report360/{report_id}`, `GET {symbol}/report360/latest`, `GET|PUT {symbol}/manual-inputs`.

**Advise**: stock rows carry a cached `report360` badge (never generates). Optional veto (`stock360_veto`, off by default; `STOCK360_VETO_ENABLED`, `STOCK360_VETO_MIN_SELL_SIGNALS`) drops picks whose quant-only 360° verdict is SELL/AVOID and returns the weight to funds.

**Settings**: `stock360_*` and `tax_*` in `config.py`.

**Known limits**: the OpenAI/Anthropic web-search adapters are unit-tested on recorded response shapes only, not live. Educational output, not investment advice.

## Manual inputs, charts and entry points (gap closure)

- **Manual inputs** (`PUT /api/stocks/{symbol}/manual-inputs`, or nested as `overrides.report360_inputs` in `/api/stocks/analyze` and compare `overrides_a/b`): promoter/pledge/FII/DII/public %, order book, contingent liabilities, goodwill, consensus EPS FY+1/FY+2, analyst Buy/Hold/Sell counts, mean target, industry CAGR, TAM, management guidance (text) and shareholding history by quarter (up to 12 rows). All need an "as of" date; they surface as `Manual` metrics/tables and take precedence over web facts.
- **Charts** (`Report360.charts`, built in `assemble`; absent in reports stored before this change): price with EMA 20/50/200 and support/resistance, P/E band, return decomposition, analyst consensus (only if data exists), plus the intrinsic-value and target bar charts.
- **Entry points:** a "360° Report" tab in Stock Analyzer, buttons in Advise rows and Compare, and a "360° reports saved for this run" strip when reopening a run from history. A single store-driven drawer (`open360`, `active360Jobs` in `useAppStore`) keeps running jobs resumable.
