# AMFI Scheme Master, Official TER & Quarterly AUM Sync Commands (Poetry)

All commands below should be run from the `ai-personal-wealth-manager/` project root.

---

## 1. Full Automated Live Sync (NAVAll + Official TER + Quarterly Average AUM)

Downloads AMFI's daily `NAVAll.txt`, the latest monthly SEBI/AMFI Total Expense Ratio (TER) `.xlsx` disclosure, and the latest quarterly Scheme-wise Average AUM (AAUM) JSON directly into `data/wealth_real.db` (`scheme_master`), then fills any remaining gaps with category fallback defaults:

```bash
poetry run wealth-advisor --mode real sync-schemes
```

---

## 2. Fast Scheme Master + Daily NAV Sync Only (Skip TER & AUM Download)

If you already synced TER and AUM recently and only want a fast (~2s) refresh of open-ended schemes and latest NAVs from AMFI's `NAVAll.txt`:

```bash
poetry run wealth-advisor --mode real sync-schemes --skip-ter-aum
```

---

## 3. Seed Curated Funds + Full AMFI Master, TER & AUM Refresh

Loads `data/curated_funds.csv`, fetches multi-year historical NAV series from `mfapi.in`, refreshes AMFI `NAVAll.txt` + live TER + live quarterly AAUM, and recomputes all fund statistics (`fund_stats`):

```bash
poetry run wealth-advisor --mode real seed-real --refresh-amfi
```

---

## 4. Offline / Manual File Import (Optional Override)

If you have downloaded local AMFI export files (`.xlsx`, `.csv`, `.xls` HTML table, or `.json`), you can import them directly without hitting AMFI's network endpoints:

```bash
# Import both local TER and AUM files alongside a local NAVAll.txt
poetry run wealth-advisor --mode real sync-schemes \
  --file data/NAVAll.txt \
  --ter-file path/to/amfi_ter.xlsx \
  --aum-file path/to/amfi_aum.json

# Or import only a local TER file (while fetching latest NAVAll.txt)
poetry run wealth-advisor --mode real sync-schemes \
  --ter-file path/to/amfi_ter.csv \
  --skip-ter-aum
```

---

## 5. Verify Synced TER & AUM in SQLite

### Check specific funds (e.g., Bandhan Small Cap `147946`, Quant Small Cap `120828`, Parag Parikh Flexi Cap `122639`)

```bash
poetry run python -c "
from wealth_advisor.db import open_db
conn = open_db('data/wealth_real.db')
rows = conn.execute('''
    SELECT code, name, expense_ratio, ter_as_of, aum_cr, aum_as_of
    FROM scheme_master
    WHERE code IN ('147946', '120828', '122639', '120503')
    ORDER BY code
''').fetchall()
for r in rows:
    print(dict(r))
"
```

### Check overall coverage across `scheme_master`

```bash
poetry run python -c "
from wealth_advisor.db import open_db
conn = open_db('data/wealth_real.db')
stats = conn.execute('''
    SELECT
        COUNT(*) AS total_schemes,
        SUM(CASE WHEN expense_ratio IS NOT NULL THEN 1 ELSE 0 END) AS with_ter,
        SUM(CASE WHEN ter_as_of IS NOT NULL THEN 1 ELSE 0 END) AS with_official_ter_date,
        SUM(CASE WHEN aum_cr IS NOT NULL THEN 1 ELSE 0 END) AS with_aum,
        SUM(CASE WHEN aum_as_of IS NOT NULL THEN 1 ELSE 0 END) AS with_official_aum_date
    FROM scheme_master
''').fetchone()
print(dict(stats))
"
```

---

## 6. Run Unit Tests for Scheme Master, TER & AUM Sync

```bash
poetry run pytest tests/test_scheme_master.py -v
```
