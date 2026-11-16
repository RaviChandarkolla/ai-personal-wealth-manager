# Query Input Rules and Asset Class Selection

This document details the rules applied to user query inputs and the asset class selection framework used within the **Personal Wealth Manager** system (`wealth_advisor`).

---

## 1. Overview

The pipeline processes financial advisory requests through a deterministic-first, guardrailed architecture:
1. **Pre-LLM Hard Input Guardrail**: Validates the user query using regex rules to reject requests outside the mutual-fund recommendation scope (e.g., leverage, F&O, crypto, day trading).
2. **Profiling Agent (Extraction & Validation)**: Extracts required goal parameters (`amount`, `horizon`, `risk_preference`), validates data types, handles units, and prompts for missing mandatory inputs.
3. **Deterministic Risk Scoring**: Evaluates a 5-factor risk model to map the user into a standardized risk category (`Conservative`, `Moderate`, `Aggressive`).
4. **Asset Class Bucketing & Allocation Bands**: Computes asset allocation percentages across asset classes (`equity`, `debt`, `gold`, `alternatives`), applying hard capacity caps to ensure risk tolerance does not exceed capacity.
5. **Fund Category Selection & Suitability**: Shortlists mutual funds within permitted categories matching the allocated buckets.

---

## 2. Query Input Guardrails (Pre-LLM Out-of-Scope Filtering)

Located in: [`src/wealth_advisor/guardrails/input.py`](file:///Users/r.kolla/AI/agentic-ai-system-design/ai-personal-wealth-manager/src/wealth_advisor/guardrails/input.py)

Before sending any user input to the LLM, the system runs `check_input(text: str) -> GuardResult`. If any forbidden pattern is detected, execution halts immediately without incurring LLM token costs or latency.

### Guardrail Rule Table

| Scope Rule | Regex Pattern | Rejection Tag | Examples Blocked |
|---|---|---|---|
| **Leverage & Margin** | `\b(leverage[d]?\|margin trading\|borrow(ed)?\s+(money\|to invest)\|loan against)\b` | `leverage` | *"Invest 1 lakh with 5x leverage"*, *"margin trading on Nifty"*, *"borrow money to invest"* |
| **Derivatives / F&O** | `\bf\s?&\s?o\b\|\bderivatives?\b\|\b(call\|put)\s+options?\b\|\boptions?\s+trading\b\|\bfutures?\b` | `derivatives` | *"Buy Nifty call options"*, *"trade F&O with my savings"*, *"put option on BankNifty"* |
| **Intraday Trading** | `\b(intraday\|day[- ]?trad\w*\|scalping)\b` | `intraday trading` | *"I want to do intraday trading with 50k"*, *"day trading strategies"*, *"scalping setups"* |
| **Trade Execution** | `\b(execute\|place\|buy\|sell)\s+(this\|the\|my\|an?)?\s*(trade\|order)s?\b\|\bexecute\s+(it\|now)\b` | `trade execution` | *"Execute this trade now"*, *"place buy order for fund X"*, *"sell my portfolio"* |
| **Unsupported Assets** | `\b(crypto\w*\|bitcoin\|forex)\b` | `unsupported asset class` | *"put 10k in bitcoin"*, *"crypto investments"*, *"forex currency pairs"* |

### Guardrail Rejection Response
When a rule triggers, a `GuardResult(allowed=False, reason=..., message=...)` is generated with the following standardized explanation:
> *"Out of scope ({reason}). This assistant only recommends mutual-fund allocations; it does not execute trades or advise on leverage or derivatives."*

---

## 3. Query Parsing and Goal Extraction Rules

Located in: [`src/wealth_advisor/agents/profiling.py`](file:///Users/r.kolla/AI/agentic-ai-system-design/ai-personal-wealth-manager/src/wealth_advisor/agents/profiling.py)

When input passes the pre-guardrail filter, `ProfilingAgent.extract(query)` extracts parameters into an `ExtractedGoal` schema.

### Mandatory vs. Optional Fields

| Field | Type | Status | Validation & Extraction Logic |
|---|---|---|---|
| **`amount`** | `float` | **Mandatory** | Extracted as raw text, parsed via `parse_amount()`. Must be positive ($> 0$). |
| **`horizon`** | `float` | **Mandatory** | Extracted as raw text, parsed via `parse_horizon()`. Converted to decimal years ($> 0$). |
| **`risk_preference`** | `RiskPreference` | **Mandatory** | Mapped strictly to the 5-tier enum taxonomy. |
| **`purpose`** | `str` | Optional | Contextual metadata (e.g., *"house down payment"*, *"retirement"*). Defaults to `""`. |
| **`excluded_categories`** | `list[str]` | Optional | Specific fund categories to omit (e.g., `["mid cap", "small cap"]`). Defaults to `[]`. |

### Amount Parsing Rules (`parse_amount`)
The amount parser handles digits, commas, decimals, word numbers, and multiple currency multiplier units:
- **Multiplier Units**:
  - `crore`, `crores`, `cr`: $\times 10^7$
  - `lakh`, `lakhs`, `lac`, `lacs`, `l`: $\times 10^5$
  - `k`, `thousand`, `hazar`, `hazaar`: $\times 10^3$
  - `million`, `m`: $\times 10^6$
- **Word Numbers**: Supports `"zero"` through `"twenty"` (e.g., *"one lakh"* $\rightarrow$ `100,000`).
- **Garbage Rejection**: Non-numeric or ambiguous quantities (e.g., *"a lot"*, *"some money"*) return `None`.

### Horizon Parsing Rules (`parse_horizon`)
The horizon parser extracts time duration and converts all units to fractional years:
- **Year Units**: `y`, `yr`, `yrs`, `year`, `years`, `saal`, `sal`, `varsh` (direct numeric value).
- **Month Units**: `m`, `mo`, `mos`, `month`, `months`, `mahine`, `mahina`, `maah` ($\text{value} / 12$).
- Non-time units (e.g., *"5 parsecs"*, *"soon"*) return `None`.

### Risk Preference Taxonomy
The LLM maps user sentiments into exactly one of five standard values:
- `"no risk"` / `"can't lose money"` $\rightarrow$ `capital_preservation`
- `"low risk"` / `"safe"` $\rightarrow$ `conservative`
- `"medium"` / `"balanced"` $\rightarrow$ `moderate`
- `"high growth"` $\rightarrow$ `growth`
- `"high risk"` / `"max returns"` $\rightarrow$ `aggressive`

### Missing Field Handling
If any of `amount`, `horizon`, or `risk` cannot be parsed or are $\le 0$:
- **FastAPI API (`/api/advise`)**: Returns `HTTP 400 Bad Request` with detail: `"Missing info: <field1>, <field2>"`.
- **Interactive CLI**: Prompts targeted follow-up questions:
  - `amount`: *"How much do you want to invest (e.g. 50,000 or 2 lakh)?"*
  - `horizon`: *"For how long do you plan to stay invested (e.g. 5 years)?"*
  - `risk`: *"How much risk are you comfortable with (low / moderate / high)?"*

### Restatement & Confirmation Protocol
Before proceeding with recommendations, the parsed goal is restated in human-readable form using `ProfilingAgent.restate(goal)`:
> *"Invest Rs 50,000 for 5 years with a conservative risk preference (purpose: house down payment)."*

In interactive CLI mode, the user must confirm with `[y/n/edit]` before pipeline execution.

### Default Financial Profile Assumptions
If personal financial parameters are not supplied in the initial query, the system applies conservative baseline defaults from [`config.py`](file:///Users/r.kolla/AI/agentic-ai-system-design/ai-personal-wealth-manager/src/wealth_advisor/config.py):
- `savings_rate`: `0.20` (20% of monthly income)
- `emergency_months`: `6.0` months
- `income_stability`: `IncomeStability.STABLE`
- `hhi`: `None` (neutral concentration score of `5.0`)

---

## 4. Deterministic Risk Scoring Model

Located in: [`src/wealth_advisor/domain/risk.py`](file:///Users/r.kolla/AI/agentic-ai-system-design/ai-personal-wealth-manager/src/wealth_advisor/domain/risk.py)

A deterministic scoring engine calculates a numeric score between $1.0$ and $10.0$ based on a weighted sum of 5 factors:

$$\text{Risk Score} = \sum (\text{Factor Score}_i \times \text{Weight}_i)$$

| Factor | Weight | Scoring Rubric ($1$ to $10$) |
|---|---|---|
| **Horizon** | **30%** | $<1\text{y} \rightarrow 1$, $1\text{--}3\text{y} \rightarrow 3$, $3\text{--}5\text{y} \rightarrow 5$, $5\text{--}7\text{y} \rightarrow 7$, $7\text{--}10\text{y} \rightarrow 8$, $>10\text{y} \rightarrow 10$ |
| **Preference** | **25%** | `capital_preservation` $\rightarrow 2$, `conservative` $\rightarrow 4$, `moderate` $\rightarrow 6$, `growth` $\rightarrow 8$, `aggressive` $\rightarrow 10$ |
| **Savings & Stability** | **20%** | Base savings score ($\le 5\% \rightarrow 2$, $\le 10\% \rightarrow 4$, $\le 20\% \rightarrow 6$, $\le 30\% \rightarrow 8$, $>30\% \rightarrow 10$) $\times$ Multiplier (`1.0` if stable, `0.8` if variable income) |
| **Diversification** | **15%** | Unknown $\rightarrow 5.0$; else $1 + 9 \times (1 - \text{HHI})$ |
| **Liquidity / Emergency** | **10%** | $<3 \text{ months} \rightarrow 2$, $3\text{--}6 \text{ months} \rightarrow 5$, $6\text{--}12 \text{ months} \rightarrow 8$, $\ge 12 \text{ months} \rightarrow 10$ |

### Category Thresholds
- **Conservative**: Score $< 4.0$
- **Moderate**: $4.0 \le \text{Score} < 7.0$
- **Aggressive**: $\text{Score} \ge 7.0$

---

## 5. Asset Classes & Allocation Selection

Located in: [`src/wealth_advisor/models/enums.py`](file:///Users/r.kolla/AI/agentic-ai-system-design/ai-personal-wealth-manager/src/wealth_advisor/models/enums.py) and [`src/wealth_advisor/domain/allocation.py`](file:///Users/r.kolla/AI/agentic-ai-system-design/ai-personal-wealth-manager/src/wealth_advisor/domain/allocation.py)

### Asset Class Buckets (`Bucket` Enum)
The system models investments into four discrete asset buckets:
1. **`Bucket.EQUITY`** (`"equity"`)
2. **`Bucket.DEBT`** (`"debt"`)
3. **`Bucket.GOLD`** (`"gold"`)
4. **`Bucket.ALTERNATIVES`** (`"alternatives"`)

### Baseline Allocation Bands by Category

| Category | Equity Band | Debt Band | Gold Band | Alternatives Band |
|---|---|---|---|---|
| **Conservative** | 15% – 30% | 70% – 85% | 0% – 10% | 0% |
| **Moderate** | 40% – 60% | 30% – 45% | 5% – 10% | 0% – 5% |
| **Aggressive** | 70% – 85% | 5% – 20% | 5% – 10% | 0% – 10% |

### Capacity Override Rules (Capacity Trumps Tolerance)
Regardless of the user's high risk preference, hard capacity constraints clamp the maximum equity percentage:
- **Horizon $< 1$ year**: Equity is capped at **$\le 5.0\%$** (`equity_cap_horizon_lt_1y`).
- **Horizon $< 3$ years**: Equity is capped at **$\le 20.0\%$** (`equity_cap_horizon_lt_3y`).
- **Emergency fund $< 3$ months**: Equity is capped at **$\le 30.0\%$** (`equity_cap_low_emergency`).

### Default Target Allocation Calculation (`target_allocation`)
Target weights are assigned using the midpoint of the equity, gold, and alternative bands, with **Debt absorbing the remainder** so the total portfolio strictly sums to 100%:

| Target Allocation Metric | Conservative | Moderate | Aggressive |
|---|---|---|---|
| **Equity Target** | **22.5%** | **50.0%** | **77.5%** |
| **Debt Target** (Remainder) | **72.5%** | **40.0%** | **10.0%** |
| **Gold Target** | **5.0%** | **7.5%** | **7.5%** |
| **Alternatives Target** | **0.0%** | **2.5%** | **5.0%** |
| **Total** | **100.0%** | **100.0%** | **100.0%** |

---

## 6. Mutual Fund Category Taxonomy & Selection

Located in: [`src/wealth_advisor/domain/categories.py`](file:///Users/r.kolla/AI/agentic-ai-system-design/ai-personal-wealth-manager/src/wealth_advisor/domain/categories.py)

Each non-zero target bucket is researched by candidate fund categories mapped to that asset class.

| Bucket | Category Name | Minimum Horizon | Small AUM Allowed | Expense Ceiling |
|---|---|---|---|---|
| **`debt`** | `Liquid` | 0.0 Years | Yes ($\ge 100\text{ Cr}$) | 0.30% |
| **`debt`** | `Short Duration` | 1.0 Year | No ($\ge 500\text{ Cr}$) | 0.50% |
| **`debt`** | `Corporate Bond` | 2.0 Years | No ($\ge 500\text{ Cr}$) | 0.50% |
| **`debt`** | `Conservative Hybrid` | 2.0 Years | No ($\ge 500\text{ Cr}$) | 0.90% |
| **`equity`** | `Index Fund` | 3.0 Years | No ($\ge 500\text{ Cr}$) | 0.50% |
| **`equity`** | `Large Cap` | 3.0 Years | No ($\ge 500\text{ Cr}$) | 1.00% |
| **`equity`** | `Flexi Cap` | 3.0 Years | No ($\ge 500\text{ Cr}$) | 1.00% |
| **`equity`** | `Mid Cap` | 5.0 Years | No ($\ge 500\text{ Cr}$) | 1.00% |
| **`gold`** | `Gold ETF` | 3.0 Years | Yes ($\ge 100\text{ Cr}$) | 0.60% |
| **`alternatives`** | *(None assigned in MVP)* | N/A | N/A | N/A |

### Suitability & Exclusion Rules
When shortlisting instruments for the selected buckets:
1. **User Exclusions**: Any category listed in the user's `excluded_categories` is strictly rejected.
2. **Horizon Check**: If `goal.horizon_years < min_horizon_years` for a fund's category, the fund is disqualified.
3. **Plan Type**: Direct plan is required (`require_direct_plan = True`).
4. **Track Record Floor**: $\ge 5$ years for Equity, $\ge 3$ years for Debt and Gold.
5. **Rating Floor**: Star rating must be $\ge 4.0$ (when ratings are available).
6. **Alternatives Bucket in MVP**: Because no specific fund category is mapped to `alternatives`, any allocation assigned to it is flagged as `unallocated_weight` and reported transparently to the user.

---

## 7. Unsupported & Prohibited Asset Classes

The system explicitly rejects or excludes the following asset classes:
- **Cryptocurrency / Bitcoin**: Blocked by regex guardrail.
- **Foreign Exchange (Forex)**: Blocked by regex guardrail.
- **Derivatives, Options, & Futures**: Blocked by regex guardrail.
- **Leveraged Instruments & Margin Debt**: Blocked by regex guardrail.
- **Individual / Direct Stocks**: Out of scope for the current mutual-fund advisor MVP; direct buy/sell trade execution is blocked.
