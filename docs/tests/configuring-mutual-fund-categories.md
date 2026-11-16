# Configuring Mutual Fund Categories

This guide explains how mutual fund categories are structured, configured, filtered, and extended in the `ai-personal-wealth-manager` system.

---

## 1. Architectural Overview

Mutual fund categories drive several core stages in the advisory pipeline:

1. **Asset Allocation (`Bucket`)**: Every category maps to an asset bucket (`Bucket.EQUITY`, `Bucket.DEBT`, `Bucket.GOLD`, or `Bucket.ALTERNATIVES`).
2. **Suitability Guardrails (`check_fund`)**: Enforces holding horizon constraints (`category_min_horizons`), expense ratio ceilings (`expense_ceilings`), and membership in permitted categories (`allowed_categories`).
3. **Research Agents (`MFResearchAgent`)**: Queries candidate schemes filtered by allowed and non-excluded categories (`query_funds(categories=[...])`).
4. **On-Demand Hydration**: Automatically fetches NAV history for sparse or under-represented categories from AMFI scheme master records.
5. **Head-to-Head Comparison & Alpha/Beta Calculation**: Uses category-specific benchmark proxy schemes to calculate relative metrics (Sharpe, Sortino, Alpha, Beta, Tracking Error).

---

## 2. Category Taxonomy & Built-in Categories

The taxonomy is defined in [`src/wealth_advisor/domain/categories.py`](file:///Users/r.kolla/AI/agentic-ai-system-design/ai-personal-wealth-manager/src/wealth_advisor/domain/categories.py).

### Complete Supported Mutual Fund Categories (16 Categories)

| Category Name | Asset Bucket | Min Horizon (Years) | Small AUM Floor? | Default Benchmark Index | Default Expense Ceiling |
| :--- | :--- | :---: | :---: | :--- | :---: |
| **Liquid** | `DEBT` | 0.0 | Yes (100 Cr) | CRISIL Liquid Debt A-I Index | 0.30% |
| **Short Duration** | `DEBT` | 1.0 | No (500 Cr) | CRISIL Short Duration Debt A-II Index | 0.50% |
| **Corporate Bond** | `DEBT` | 2.0 | No (500 Cr) | CRISIL Corporate Debt A-II Index | 0.50% |
| **Banking & PSU** | `DEBT` | 1.0 | No (500 Cr) | CRISIL Banking and PSU Debt Index | 0.50% |
| **Gilt** | `DEBT` | 3.0 | No (500 Cr) | CRISIL Dynamic Gilt Index | 0.50% |
| **Arbitrage** | `DEBT` | 2.0 | No (500 Cr) | Nifty 50 Arbitrage Index | 0.50% |
| **Conservative Hybrid** | `DEBT` | 2.0 | No (500 Cr) | CRISIL Hybrid 85+15 - Conservative Index | 0.90% |
| **Index Fund** | `EQUITY` | 3.0 | No (500 Cr) | Nifty 50 TRI | 0.50% |
| **Large Cap** | `EQUITY` | 3.0 | No (500 Cr) | Nifty 100 TRI | 1.00% |
| **Large & Mid Cap** | `EQUITY` | 5.0 | No (500 Cr) | Nifty LargeMidcap 250 TRI | 1.00% |
| **Flexi Cap** | `EQUITY` | 3.0 | No (500 Cr) | Nifty 500 TRI | 1.00% |
| **Mid Cap** | `EQUITY` | 5.0 | No (500 Cr) | Nifty Midcap 150 TRI | 1.00% |
| **Small Cap** | `EQUITY` | 7.0 | No (500 Cr) | Nifty Smallcap 250 TRI | 1.00% |
| **ELSS** | `EQUITY` | 5.0 | No (500 Cr) | Nifty 500 TRI | 1.00% |
| **Aggressive Hybrid** | `EQUITY` | 5.0 | No (500 Cr) | CRISIL Hybrid 35+65 - Aggressive Index | 1.00% |
| **Gold ETF** | `GOLD` | 3.0 | Yes (100 Cr) | Domestic Price of Gold | 0.60% |

> [!NOTE]
> Multi-asset categories like **Direct Equity**, **Bank FD**, **SGB**, **REIT**, and **InvIT** are also registered in `ALL_CATEGORIES` for multi-asset portfolio sleeve support.

---

## 3. SEBI / AMFI Category Normalization & Aliases

When AMFI `NAVAll.txt` files are downloaded, schemes often contain legacy or descriptive category names. The normalization layer maps these variants to canonical names:

- **[`CATEGORY_ALIASES`](file:///Users/r.kolla/AI/agentic-ai-system-design/ai-personal-wealth-manager/src/wealth_advisor/domain/categories.py)** and **[`_SEBI_CATEGORY_ALIASES`](file:///Users/r.kolla/AI/agentic-ai-system-design/ai-personal-wealth-manager/src/wealth_advisor/data/amfi.py)**:

| Raw AMFI / SEBI Header Text | Canonical Category |
| :--- | :--- |
| `Index`, `Index Funds` | `Index Fund` |
| `ELSS- Tax Saver`, `ELSS - Tax Saver`, `Tax Saver` | `ELSS` |
| `Banking and PSU Debt`, `Banking & PSU Debt` | `Banking & PSU` |
| `Short Term` | `Short Duration` |
| `Value / Contra`, `Contra`, `Value`, `Focused` | `Flexi Cap` |
| `Balanced Advantage`, `Dynamic Asset Allocation` | `Aggressive Hybrid` |
| `Equity Savings` | `Conservative Hybrid` |
| `Overnight`, `Ultra Short Duration`, `Money Market` | `Liquid` |
| `Low Duration` | `Short Duration` |
| `Gold ETF / FoF` | `Gold ETF` |

---

## 4. Rule Set-Driven Category Configuration

The system provides switchable fund selection rule sets in [`src/wealth_advisor/domain/rulesets.py`](file:///Users/r.kolla/AI/agentic-ai-system-design/ai-personal-wealth-manager/src/wealth_advisor/domain/rulesets.py):

### Set 1: Baseline Default (9 Categories)
Restricts selection to core diversified categories with proven stability:
```python
"allowed_categories": [
    "Liquid",
    "Short Duration",
    "Corporate Bond",
    "Conservative Hybrid",
    "Index Fund",
    "Large Cap",
    "Flexi Cap",
    "Mid Cap",
    "Gold ETF",
]
```

### Set 2: Magic of Mutual Funds (16 Categories)
Expands the selection to all 16 SEBI categories, allowing aggressive equity and specialized debt schemes:
- Adds: `Small Cap`, `Large & Mid Cap`, `ELSS`, `Aggressive Hybrid`, `Arbitrage`, `Banking & PSU`, `Gilt`.
- Enforces stricter minimum investment horizons (e.g. Large Cap / Flexi Cap minimum 5 years; Small Cap minimum 7 years).

### Combined: Set 1 + Set 2 Merge Engine
Dynamically merges Set 1 and Set 2 via `merge_rule_sets()`:
- `allowed_categories` becomes the **union** of both sets (all 16 categories).
- `category_min_horizons` picks the **more conservative** (longer) horizon for each category.
- `expense_ceilings` selects the **stricter (lower)** expense ceiling for each category.

### Activating a Rule Set
- **CLI Flag**:
  ```bash
  poetry run wealth-advisor advise --rule-set set2 "Aggressive 10y wealth growth"
  ```
- **Environment Variable**:
  ```bash
  WEALTH_FUND_RULE_SET=combined
  ```
- **Python Context Manager**:
  ```python
  from wealth_advisor.domain.rulesets import use_rule_set

  with use_rule_set("set2"):
      # Runs with 16 allowed categories
      ...
  ```

---

## 5. Configuring Categories via Settings & Environment Variables

All category behavior can be configured via [`Settings`](file:///Users/r.kolla/AI/agentic-ai-system-design/ai-personal-wealth-manager/src/wealth_advisor/config.py) or `.env`.

### Supported Configuration Keys

| Setting Field | Env Variable Name | Type | Description |
| :--- | :--- | :--- | :--- |
| `allowed_categories` | `WEALTH_ALLOWED_CATEGORIES` | `list[str]` | Whitelist of categories allowed for selection |
| `category_min_horizons` | `WEALTH_CATEGORY_MIN_HORIZONS` | `dict[str, float]` | Minimum holding horizon in years per category |
| `expense_ceilings` | `WEALTH_EXPENSE_CEILINGS` | `dict[str, float]` | Max acceptable expense ratio per category |
| `benchmark_proxy_codes` | `WEALTH_BENCHMARK_PROXY_CODES` | `dict[str, str]` | AMFI scheme code used as proxy benchmark |

### Example `.env` Configuration

```bash
# 1. Whitelist only specific categories
WEALTH_ALLOWED_CATEGORIES='["Large Cap", "Flexi Cap", "Mid Cap", "Liquid"]'

# 2. Override minimum holding horizons (in years)
WEALTH_CATEGORY_MIN_HORIZONS='{"Large Cap": 3.0, "Flexi Cap": 3.0, "Mid Cap": 5.0, "Small Cap": 7.0, "Liquid": 0.0}'

# 3. Custom expense ratio ceilings (%)
WEALTH_EXPENSE_CEILINGS='{"Large Cap": 0.75, "Flexi Cap": 0.80, "Mid Cap": 0.90, "Liquid": 0.25}'

# 4. Custom benchmark proxy AMFI codes
WEALTH_BENCHMARK_PROXY_CODES='{"Large Cap": "120716", "Mid Cap": "147622", "Small Cap": "147623"}'
```

### Programmatic Python Configuration

```python
from wealth_advisor.config import Settings

custom_settings = Settings(
    allowed_categories=["Large Cap", "Flexi Cap", "Short Duration", "Liquid"],
    category_min_horizons={"Large Cap": 3.0, "Flexi Cap": 3.0, "Short Duration": 1.0, "Liquid": 0.0},
    expense_ceilings={"Large Cap": 0.70, "Flexi Cap": 0.75, "Liquid": 0.20},
)
```

---

## 6. Runtime Exclusion & User Constraints

Categories can also be restricted per investor goal or CLI invocation without editing configuration files.

### 1. Via CLI `--exclude` Flag
Exclude specific categories during portfolio recommendation:
```bash
poetry run wealth-advisor advise \
  --risk aggressive \
  --horizon 10 \
  --amount 1000000 \
  --exclude "Small Cap" "Mid Cap" \
  "Long term wealth creation"
```

### 2. Via `Goal` Dataclass
```python
from wealth_advisor.models.core import Goal
from wealth_advisor.models.enums import RiskPreference

goal = Goal(
    purpose="Retirement",
    horizon_years=15.0,
    risk_preference=RiskPreference.MODERATE,
    amount=2500000.0,
    excluded_categories=["Small Cap", "Aggressive Hybrid"],
)
```

### 3. Natural Language / AI Notes
When users describe preferences in natural language (e.g., *"No small caps, keep it only in large caps and liquid debt"*), the intent extractor populates `excluded_categories` or the deterministic `PortfolioSpec.fund_requests` targets specific categories.

---

## 7. How to Add a New Mutual Fund Category

To introduce a new category into the system, follow these steps:

### Step 1: Register Category in `src/wealth_advisor/domain/categories.py`
Add the category to the `CATEGORIES` dictionary:
```python
CATEGORIES["Multi Cap"] = CategoryInfo(
    bucket=Bucket.EQUITY,
    min_horizon_years=5.0,
    small_aum_ok=False,
    benchmark="Nifty 500 Multicap 50:25:25 TRI",
)
```

### Step 2: Add Aliases in `categories.py` and `amfi.py`
Map any variations found in AMFI texts:
```python
# In src/wealth_advisor/domain/categories.py
CATEGORY_ALIASES["Multi-Cap"] = "Multi Cap"

# In src/wealth_advisor/data/amfi.py
_SEBI_CATEGORY_ALIASES["multi cap"] = "Multi Cap"
_SEBI_CATEGORY_ALIASES["multi-cap"] = "Multi Cap"
```

### Step 3: Configure Settings in `src/wealth_advisor/config.py`
Add defaults for horizon and expense ceiling:
```python
category_min_horizons = {
    ...
    "Multi Cap": 5.0,
}

expense_ceilings = {
    ...
    "Multi Cap": 1.00,
}

benchmark_proxy_codes = {
    ...
    "Multi Cap": "120716",
}
```

### Step 4: Add to Rule Sets in `src/wealth_advisor/domain/rulesets.py`
Add to `SET2_RULES["allowed_categories"]` (and `SET1_RULES` if desired):
```python
SET2_RULES["allowed_categories"].append("Multi Cap")
```

### Step 5: (Optional) Add Category Median Fallback in `src/wealth_advisor/data/amfi.py`
```python
CATEGORY_DEFAULT_TER_AUM["Multi Cap"] = (0.65, 1.70, 7500.0)
```

---

## 8. Database Inspection & Verification

### Inspecting Categories in SQLite
Check counts of ingested schemes by category in the active database:

```bash
# Check category distribution in the real database
sqlite3 data/wealth_real.db "SELECT category, count(*) FROM scheme_master GROUP BY category ORDER BY count(*) DESC;"
```

### Querying Funds via Python REPL
Verify category queries and filtering:
```python
from wealth_advisor.db import open_db
from wealth_advisor.data.store import MetadataStore

conn = open_db("data/wealth_real.db")
store = MetadataStore(conn)

# Get all categories present in master
print(store.master_categories())

# Query direct growth schemes for a specific category
funds = store.query_funds(category="Large Cap", plan="direct")
print(f"Loaded {len(funds)} Large Cap funds")
```

### Running Test Verification
Run the automated test suite to ensure all category rules, merges, and suitability filters pass:

```bash
# Test rule set configurations and category resolution
poetry run pytest tests/test_rulesets.py

# Test rule set merging (union of allowed categories)
poetry run pytest tests/test_rule_merge.py

# Test rule catalog reflection
poetry run pytest tests/test_rule_catalog.py

# Test AMFI scheme master category parsing
poetry run pytest tests/test_scheme_master.py
```
