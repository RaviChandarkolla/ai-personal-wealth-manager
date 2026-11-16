"""AMFI Total Expense Ratio (TER) parser and loader for scheme_master."""

import csv
import io
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

from pydantic import BaseModel

from .store import MetadataStore

AMFI_TER_URL = "https://www.amfiindia.com/ter-of-mf-schemes"
AMFI_TER_MONTHS_API = "https://www.amfiindia.com/api/populate-ter-month"
AMFI_TER_DATA_API = "https://www.amfiindia.com/api/populate-te-rdata-revised"

_AMC_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\baditya\s+birla\s+sl\b"), "aditya birla sun life"),
    (re.compile(r"\baditya\s+birla\s+sunlife\b"), "aditya birla sun life"),
    (re.compile(r"\babsl\b"), "aditya birla sun life"),
    (re.compile(r"\bicici\s+pru\b(?!\s*dential)"), "icici prudential"),
    (re.compile(r"\bkotak\s+mahindra\b"), "kotak"),
    (re.compile(r"\bwhiteoak\b(?!\s+capital)"), "whiteoak capital"),
    (re.compile(r"\bppfas\b"), "parag parikh"),
    (re.compile(r"\baroda\s+bnp\b(?!\s+paribas)"), "baroda bnp paribas"),
    (re.compile(r"\bcanara\s+rob\b(?!\s*eco)"), "canara robeco"),
]

_MONTH_END_DAYS = {
    1: 31,
    2: 28,
    3: 31,
    4: 30,
    5: 31,
    6: 30,
    7: 31,
    8: 31,
    9: 30,
    10: 31,
    11: 30,
    12: 31,
}


class TerRecord(BaseModel):
    code: str = ""
    isin: str = ""
    scheme_name: str = ""
    plan: str = ""  # "direct" | "regular" | ""
    base_ter: float | None = None
    brokerage: float | None = None
    transaction_cost: float | None = None
    taxes: float | None = None
    total_ter: float
    as_of: date


class TerLoadReport(BaseModel):
    rows_parsed: int = 0
    rows_matched: int = 0
    unmatched: list[str] = []
    rejected: list[str] = []


class _HTMLTableExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cur_row: list[str] | None = None
        self._cur_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._cur_row = []
        elif tag in ("td", "th") and self._cur_row is not None:
            self._cur_cell = []

    def handle_data(self, data: str) -> None:
        if self._cur_cell is not None:
            self._cur_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cur_row is not None and self._cur_cell is not None:
            self._cur_row.append("".join(self._cur_cell).strip())
            self._cur_cell = None
        elif tag == "tr" and self._cur_row is not None:
            if any(c for c in self._cur_row):
                self.rows.append(self._cur_row)
            self._cur_row = None


def fetch_ter_text(url: str = AMFI_TER_URL, timeout: int = 30) -> str:
    if not url.startswith("https://"):
        raise ValueError("only https URLs are allowed")
    req = urllib.request.Request(url, headers={"User-Agent": "wealth-advisor/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if raw.startswith(b"PK\x03\x04"):
        rows = _rows_from_xlsx_bytes(raw)
        return json.dumps({"data": rows})
    return raw.decode("utf-8", errors="replace")


def _parse_date(val: str | None, default: date | None = None) -> date:
    if not val or not str(val).strip():
        return default or date.today()
    s = str(val).strip()
    # ISO datetime string e.g. "2026-04-01T00:00:00.000Z"
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-" and "T" in s:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    # Excel serial date number e.g. "46113"
    if re.fullmatch(r"\d{5}(?:\.0+)?", s):
        serial = int(float(s))
        if 30000 <= serial <= 70000:
            return date(1899, 12, 30) + timedelta(days=serial)
    # Quarter range string e.g. "July - September 2026" or "October - December 2025"
    m_qtr = re.search(r"[-–]\s*([A-Za-z]+\s+\d{4})$", s)
    if m_qtr:
        end_part = m_qtr.group(1).strip()
        for fmt in ("%B %Y", "%b %Y"):
            try:
                dt = datetime.strptime(end_part, fmt).date()
                last_day = _MONTH_END_DAYS.get(dt.month, 28)
                return date(dt.year, dt.month, last_day)
            except ValueError:
                continue
    for fmt in (
        "%Y-%m-%d",
        "%d-%b-%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%m-%Y",
        "%B-%Y",
        "%b-%Y",
        "%b %Y",
        "%B %Y",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return default or date.today()


def _parse_float(val: str | None) -> float | None:
    if val is None:
        return None
    s = str(val).strip().rstrip("%").replace(",", "").strip()
    if not s or s in ("-", "NA", "N/A", "null", "None"):
        return None
    return float(s)


def _col_letter_to_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return max(idx - 1, 0)


def _rows_from_xlsx_bytes(data: bytes) -> list[dict[str, str]]:
    """Extract worksheet rows from an .xlsx binary archive using stdlib zipfile + ElementTree."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in ss_root.iter():
                if si.tag.endswith("}si") or si.tag == "si":
                    texts = [
                        t.text or ""
                        for t in si.iter()
                        if t.tag.endswith("}t") or t.tag == "t"
                    ]
                    shared_strings.append("".join(texts))

        sheet_names = [
            n for n in zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
        ]
        if not sheet_names:
            return []
        sheet_root = ET.fromstring(zf.read(sorted(sheet_names)[0]))

    raw_table: list[list[str]] = []
    for elem in sheet_root.iter():
        if not (elem.tag.endswith("}row") or elem.tag == "row"):
            continue
        row_cells: dict[int, str] = {}
        max_col = -1
        for cell in elem:
            if not (cell.tag.endswith("}c") or cell.tag == "c"):
                continue
            ref = cell.attrib.get("r", "")
            col_idx = _col_letter_to_index(ref) if ref else (max_col + 1)
            max_col = max(max_col, col_idx)
            cell_type = cell.attrib.get("t", "")
            val_text = ""
            for child in cell:
                if child.tag.endswith("}v") or child.tag == "v":
                    val_text = (child.text or "").strip()
                    break
                if child.tag.endswith("}is") or child.tag == "is":
                    val_text = "".join(
                        (t.text or "")
                        for t in child.iter()
                        if t.tag.endswith("}t") or t.tag == "t"
                    ).strip()
                    break
            if cell_type == "s" and val_text.isdigit():
                s_idx = int(val_text)
                if 0 <= s_idx < len(shared_strings):
                    val_text = shared_strings[s_idx]
            row_cells[col_idx] = val_text
        if max_col >= 0:
            row_list = [row_cells.get(i, "") for i in range(max_col + 1)]
            if any(c for c in row_list):
                raw_table.append(row_list)

    if len(raw_table) < 2:
        return []
    headers = [h.strip() for h in raw_table[0]]
    return [
        {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers)) if headers[i]}
        for row in raw_table[1:]
    ]


def _dict_rows_from_text(text: str) -> list[dict[str, str]]:
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            payload = json.loads(stripped)
            items: list[dict] = []
            if isinstance(payload, list):
                items = [x for x in payload if isinstance(x, dict)]
            elif isinstance(payload, dict):
                data_field = payload.get("data")
                if isinstance(data_field, list):
                    items = [x for x in data_field if isinstance(x, dict)]
                elif isinstance(data_field, dict) and isinstance(data_field.get("data"), list):
                    items = [x for x in data_field["data"] if isinstance(x, dict)]
            if items:
                return [
                    {str(k).strip(): ("" if v is None else str(v).strip()) for k, v in row.items() if k}
                    for row in items
                ]
            return []
        except json.JSONDecodeError:
            pass
    if stripped.startswith("<") and ("<table" in stripped.lower() or "<tr" in stripped.lower()):
        parser = _HTMLTableExtractor()
        parser.feed(stripped)
        if len(parser.rows) < 2:
            return []
        headers = [h.strip() for h in parser.rows[0]]
        return [
            {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}
            for row in parser.rows[1:]
        ]
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    first = lines[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    return [
        {k.strip(): (v.strip() if v else "") for k, v in row.items() if k}
        for row in reader
    ]


def _find_col(row: dict[str, str], *candidates: str) -> str | None:
    lower_map = {k.lower(): v for k, v in row.items()}
    for c in candidates:
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    for k_lower, v in lower_map.items():
        for c in candidates:
            if c.lower() in k_lower:
                return v
    return None


def parse_ter_text(text: str, default_as_of: date | None = None) -> tuple[list[TerRecord], list[str]]:
    """Parse AMFI TER JSON/CSV/HTML/text into TerRecord items and rejection strings."""
    raw_rows = _dict_rows_from_text(text)
    records: list[TerRecord] = []
    rejected: list[str] = []

    for idx, row in enumerate(raw_rows, start=2):
        # Note: NSDLSchemeCode in AMFI's TER feed is not the numeric AMFI scheme_code
        code = (_find_col(row, "scheme code", "amfi code", "code", "scheme_code") or "").strip()
        if "/" in code:
            code = ""
        isin = (_find_col(row, "isin", "isin_growth", "isin div payout/ isin growth") or "").strip()
        name = (_find_col(row, "scheme name", "scheme_name", "name", "scheme") or "").strip()
        as_of_s = _find_col(row, "ter_date", "ter date", "as_of", "as of", "date", "month")
        as_of = _parse_date(as_of_s, default_as_of)

        direct_total_s = _find_col(
            row,
            "direct plan - total ter",
            "direct_ter",
            "direct ter",
            "direct total ter",
            "d_ter",
        )
        regular_total_s = _find_col(
            row,
            "regular plan - total ter",
            "regular_ter",
            "regular ter",
            "regular total ter",
            "r_ter",
        )

        if direct_total_s is not None or regular_total_s is not None:
            emitted = False
            for plan_label, total_s, prefix, short_p in (
                ("direct", direct_total_s, "direct", "d"),
                ("regular", regular_total_s, "regular", "r"),
            ):
                if total_s is None or not total_s.strip() or total_s.strip() in ("-", "NA", "N/A"):
                    continue
                try:
                    total_val = _parse_float(total_s)
                    if total_val is None or total_val <= 0 or total_val > 10.0:
                        rejected.append(f"row {idx} ({plan_label}): invalid TER {total_s!r}")
                        continue
                    base_val = _parse_float(
                        _find_col(
                            row,
                            f"{prefix} plan - base expense ratio (ber)",
                            f"{prefix} plan - base ter",
                            f"{prefix}_base_ter",
                            f"{short_p}_ber",
                            f"{short_p}_baseter",
                        )
                    )
                    brok_val = _parse_float(
                        _find_col(
                            row,
                            f"{prefix} plan - brokerage cost",
                            f"{prefix} plan - additional expense as per regulation 52(6a)(b)",
                            f"{prefix} plan - additional expense 52(6a)(b)",
                            f"{prefix}_brokerage",
                            f"{short_p}_brokeragecost",
                            f"{short_p}_6a_b",
                        )
                    )
                    tx_val = _parse_float(
                        _find_col(
                            row,
                            f"{prefix} plan - transaction cost",
                            f"{prefix} plan - additional expense as per regulation 52(6a)(c)",
                            f"{prefix} plan - additional expense 52(6a)(c)",
                            f"{prefix}_transaction_cost",
                            f"{short_p}_transactioncost",
                            f"{short_p}_6a_c",
                        )
                    )
                    gst_val = _parse_float(
                        _find_col(
                            row,
                            f"{prefix} plan - statutory levies",
                            f"{prefix} plan - gst",
                            f"{prefix}_gst",
                            f"{prefix}_taxes",
                            f"{short_p}_statutorylevies",
                            f"{short_p}_gst",
                        )
                    )
                    plan_isin = (_find_col(row, f"{prefix} plan - isin", f"{prefix}_isin") or isin).strip()
                    plan_code = (_find_col(row, f"{prefix} plan - scheme code", f"{prefix}_code") or code).strip()
                    if "/" in plan_code:
                        plan_code = ""
                    records.append(
                        TerRecord(
                            code=plan_code,
                            isin=plan_isin,
                            scheme_name=name,
                            plan=plan_label,
                            base_ter=base_val,
                            brokerage=brok_val,
                            transaction_cost=tx_val,
                            taxes=gst_val,
                            total_ter=total_val,
                            as_of=as_of,
                        )
                    )
                    emitted = True
                except ValueError:
                    rejected.append(f"row {idx} ({plan_label}): non-numeric TER {total_s!r}")
            if not emitted and not any(
                s and s.strip() not in ("-", "NA", "N/A") for s in (direct_total_s, regular_total_s)
            ):
                rejected.append(f"row {idx}: missing both direct and regular TER")
            continue

        ter_s = _find_col(row, "total ter", "total_ter", "expense_ratio", "expense ratio", "ter")
        plan_col = (_find_col(row, "plan") or "").strip().lower()
        if not plan_col and name:
            plan_col = "direct" if "direct" in name.lower() else "regular"
        try:
            ter_val = _parse_float(ter_s)
            if ter_val is None or ter_val <= 0 or ter_val > 10.0:
                rejected.append(f"row {idx}: invalid TER {ter_s!r}")
                continue
            base_val = _parse_float(_find_col(row, "base expense ratio", "base ter", "base_ter"))
            brok_val = _parse_float(_find_col(row, "brokerage", "52(6a)(b)"))
            tx_val = _parse_float(_find_col(row, "transaction_cost", "transaction cost", "52(6a)(c)"))
            gst_val = _parse_float(_find_col(row, "statutory levies", "gst", "taxes", "tax"))
            records.append(
                TerRecord(
                    code=code,
                    isin=isin,
                    scheme_name=name,
                    plan=plan_col,
                    base_ter=base_val,
                    brokerage=brok_val,
                    transaction_cost=tx_val,
                    taxes=gst_val,
                    total_ter=ter_val,
                    as_of=as_of,
                )
            )
        except ValueError:
            rejected.append(f"row {idx}: bad TER value {ter_s!r}")

    # Deduplicate daily TER rows for the same scheme/plan within a monthly feed, keeping latest as_of
    if len(records) > 1:
        deduped: dict[tuple[str, str, str, str], TerRecord] = {}
        for r in records:
            key = (r.code, r.isin, r.scheme_name.strip().lower(), r.plan)
            prev = deduped.get(key)
            if prev is None or r.as_of >= prev.as_of:
                deduped[key] = r
        records = list(deduped.values())

    return records, rejected


def _normalize_scheme_prefix(name: str) -> str:
    """Normalize a scheme name to its canonical base prefix (stripping plan/option suffixes and AMC aliases)."""
    s = name.lower().strip()
    if not s:
        return ""
    # Strip parenthetical notes like (formerly known as ...) or (erstwhile ...)
    s = re.sub(r"\((?:formerly|erstwhile|earlier)[^)]*\)", " ", s)
    # Strip plan/option suffixes in any order (e.g. "- Direct Plan - Growth", "- Growth - Direct Plan")
    s = re.sub(r"(?:[-–\s]+|\b)(?:direct|regular|retail|institutional)\s*(?:plan)?\b.*", "", s)
    s = re.sub(r"(?:[-–\s]+)(?:growth|idcw|dividend|bonus)\b.*", "", s)
    s = s.replace("&", " and ")
    for pat, repl in _AMC_ALIASES:
        s = pat.sub(repl, s)
    # Normalize compound category words
    s = re.sub(r"\bflexicap\b", "flexi cap", s)
    s = re.sub(r"\blargecap\b", "large cap", s)
    s = re.sub(r"\bmidcap\b", "mid cap", s)
    s = re.sub(r"\bsmallcap\b", "small cap", s)
    s = re.sub(r"\bmulticap\b", "multi cap", s)
    s = re.sub(r"\bfund\s+of\s+funds?\b", "fof", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+(?:fund|scheme)s?$", "", s)
    return s


def load_ter_into_master(
    store: MetadataStore,
    text: str,
    default_as_of: date | None = None,
    *,
    only_codes: set[str] | None = None,
) -> TerLoadReport:
    """Parse TER data and update matching rows in scheme_master (and scheme if present)."""
    records, rejected = parse_ter_text(text, default_as_of=default_as_of)
    report = TerLoadReport(rows_parsed=len(records) + len(rejected), rejected=rejected)
    if not records:
        return report

    master_rows = store.conn.execute(
        "SELECT code, isin_growth, isin_reinvest, name, plan FROM scheme_master"
    ).fetchall()
    by_code: dict[str, list[str]] = {}
    by_code_plan: dict[tuple[str, str], list[str]] = {}
    by_isin: dict[str, list[str]] = {}
    by_isin_plan: dict[tuple[str, str], list[str]] = {}
    by_name_plan: dict[tuple[str, str], list[str]] = {}

    for r in master_rows:
        c = r["code"]
        p = (r["plan"] or "direct").lower()
        by_code.setdefault(c, []).append(c)
        by_code_plan.setdefault((c, p), []).append(c)
        for isin_col in ("isin_growth", "isin_reinvest"):
            val = (r[isin_col] or "").strip()
            if val and val != "-":
                by_isin.setdefault(val, []).append(c)
                by_isin_plan.setdefault((val, p), []).append(c)
        norm = _normalize_scheme_prefix(r["name"] or "")
        if norm:
            by_name_plan.setdefault((norm, p), []).append(c)

    updates: list[tuple[float, str, str]] = []
    for rec in records:
        matched_codes: list[str] = []
        plan_lower = rec.plan.lower() if rec.plan else None
        if rec.code:
            if plan_lower and (rec.code, plan_lower) in by_code_plan:
                matched_codes = by_code_plan[(rec.code, plan_lower)]
            elif not plan_lower and rec.code in by_code:
                matched_codes = by_code[rec.code]
        if not matched_codes and rec.isin:
            if plan_lower and (rec.isin, plan_lower) in by_isin_plan:
                matched_codes = by_isin_plan[(rec.isin, plan_lower)]
            elif not plan_lower and rec.isin in by_isin:
                matched_codes = by_isin[rec.isin]
        if not matched_codes and rec.scheme_name:
            norm = _normalize_scheme_prefix(rec.scheme_name)
            plan_key = plan_lower or ("direct" if "direct" in rec.scheme_name.lower() else "regular")
            matched_codes = by_name_plan.get((norm, plan_key), [])

        if not matched_codes:
            label = rec.code or rec.isin or rec.scheme_name or "unknown"
            if rec.plan:
                label = f"{label} ({rec.plan})"
            report.unmatched.append(label)
            continue

        if only_codes is not None:
            matched_codes = [mc for mc in matched_codes if mc in only_codes]
            if not matched_codes:
                continue

        as_of_iso = rec.as_of.isoformat()
        for mc in matched_codes:
            updates.append((rec.total_ter, as_of_iso, mc))
        report.rows_matched += 1

    if updates:
        store.conn.executemany(
            "UPDATE scheme_master SET expense_ratio = ?, ter_as_of = ? WHERE code = ?",
            updates,
        )
        store.conn.executemany(
            "UPDATE scheme SET expense_ratio = ? WHERE code = ?",
            [(u[0], u[2]) for u in updates],
        )
        store.conn.commit()
    return report


def load_ter_file(store: MetadataStore, path: str | Path) -> TerLoadReport:
    p = Path(path)
    raw = p.read_bytes()
    if raw.startswith(b"PK\x03\x04"):
        text = json.dumps({"data": _rows_from_xlsx_bytes(raw)})
    else:
        text = raw.decode("utf-8", errors="replace")
    return load_ter_into_master(store, text)


def _candidate_ter_fiscal_years(today: date | None = None) -> list[str]:
    d = today or date.today()
    start_year = d.year if d.month >= 4 else d.year - 1
    return [f"{y}-{y + 1}" for y in (start_year, start_year - 1, start_year + 1)]


def fetch_latest_ter(timeout: int = 45) -> str:
    """Automatically discover and download the latest monthly TER disclosure from AMFI's API."""
    months: list[dict] = []
    for fy in _candidate_ter_fiscal_years():
        url = f"{AMFI_TER_MONTHS_API}?year={fy}"
        try:
            raw_months = fetch_ter_text(url, timeout=timeout)
            parsed = json.loads(raw_months)
            if isinstance(parsed, list) and parsed:
                months = [m for m in parsed if isinstance(m, dict) and m.get("MonthNumber")]
                if months:
                    break
        except Exception:
            continue

    candidate_months = [m["MonthNumber"] for m in months[:2]] if months else []
    if not candidate_months:
        d = date.today()
        candidate_months = [f"{d.month:02d}-{d.year}"]

    last_err: Exception | None = None
    for month_num in candidate_months:
        # Try bulk Excel export endpoint first (returns all schemes for the month in 1 request)
        excel_url = (
            f"{AMFI_TER_DATA_API}?MF_ID=All&Month={month_num}&strCat=-1&strType=1&excel=true"
        )
        try:
            text = fetch_ter_text(excel_url, timeout=timeout)
            if _dict_rows_from_text(text):
                return text
        except Exception as exc:
            last_err = exc

        # Fallback to JSON endpoint
        json_url = (
            f"{AMFI_TER_DATA_API}?MF_ID=All&Month={month_num}"
            f"&strCat=-1&strType=1&page=1&pageSize=10000"
        )
        try:
            text = fetch_ter_text(json_url, timeout=timeout)
            if _dict_rows_from_text(text):
                return text
        except Exception as exc:
            last_err = exc

    if last_err is not None:
        raise last_err
    raise RuntimeError("No TER disclosure data returned from AMFI API")


def sync_latest_ter(
    store: MetadataStore,
    timeout: int = 45,
    *,
    only_codes: set[str] | None = None,
) -> TerLoadReport:
    """Download the latest official AMFI TER disclosure and load it into scheme_master."""
    text = fetch_latest_ter(timeout=timeout)
    return load_ter_into_master(store, text, only_codes=only_codes)

