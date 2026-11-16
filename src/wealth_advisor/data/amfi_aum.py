"""AMFI Quarterly Average AUM parser and loader for scheme_master."""

from datetime import date
import json
import logging
from pathlib import Path
import urllib.parse
import urllib.request

from pydantic import BaseModel

from .amfi_ter import (
    _dict_rows_from_text,
    _find_col,
    _normalize_scheme_prefix,
    _parse_date,
    _parse_float,
    _rows_from_xlsx_bytes,
)
from .store import MetadataStore

log = logging.getLogger(__name__)

AMFI_AUM_URL = "https://www.amfiindia.com/aum-data/average-aum"
AMFI_AUM_API_URL = "https://www.amfiindia.com/api/average-aum-schemewise"


class AumRecord(BaseModel):
    code: str = ""
    isin: str = ""
    scheme_name: str = ""
    aum_cr: float
    as_of: date


class AumLoadReport(BaseModel):
    rows_parsed: int = 0
    rows_matched: int = 0
    unmatched: list[str] = []
    rejected: list[str] = []


def fetch_aum_text(url: str = AMFI_AUM_URL, timeout: int = 30) -> str:
    if not url.startswith("https://"):
        raise ValueError("only https URLs are allowed")
    req = urllib.request.Request(url, headers={"User-Agent": "wealth-advisor/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _http_get_json(url: str, timeout: int = 45) -> dict | list:
    if not url.startswith("https://"):
        raise ValueError("only https URLs are allowed")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "wealth-advisor/1.0",
            "Accept": "application/json, text/plain, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def fetch_latest_aum(timeout: int = 45) -> str:
    """Automatically discover and download the latest quarterly Scheme-wise Average AUM JSON from AMFI."""
    fy_url = f"{AMFI_AUM_API_URL}?strType=Categorywise&MF_ID=0"
    fy_payload = _http_get_json(fy_url, timeout=timeout)
    fy_list = fy_payload.get("data", []) if isinstance(fy_payload, dict) else []
    if not isinstance(fy_list, list):
        raise RuntimeError("Unexpected response from AMFI average-aum-schemewise financial years endpoint")

    for fy_item in fy_list:
        if not isinstance(fy_item, dict):
            continue
        fy_id = fy_item.get("id")
        if fy_id is None:
            continue
        periods_url = (
            f"{AMFI_AUM_API_URL}?"
            + urllib.parse.urlencode({"fyId": fy_id, "strType": "Categorywise", "MF_ID": 0})
        )
        periods_payload = _http_get_json(periods_url, timeout=timeout)
        periods_data = periods_payload.get("data", {}) if isinstance(periods_payload, dict) else {}
        periods = periods_data.get("periods", []) if isinstance(periods_data, dict) else []
        for p_item in periods:
            if not isinstance(p_item, dict):
                continue
            period_id = p_item.get("id")
            period_label = (p_item.get("period") or "").strip()
            if period_id is None:
                continue
            data_url = (
                f"{AMFI_AUM_API_URL}?"
                + urllib.parse.urlencode(
                    {
                        "strType": "Categorywise",
                        "fyId": fy_id,
                        "periodId": period_id,
                        "MF_ID": 0,
                    }
                )
            )
            data_payload = _http_get_json(data_url, timeout=timeout)
            if isinstance(data_payload, dict):
                groups = data_payload.get("data")
                if isinstance(groups, list) and any(
                    isinstance(g, dict) and g.get("schemes") for g in groups
                ):
                    if period_label and "period" not in data_payload:
                        data_payload["period"] = period_label
                    return json.dumps(data_payload)

    raise RuntimeError("Could not find any populated quarterly Average AUM dataset from AMFI API")


def _parse_nested_amfi_aum_json(
    text: str,
    default_as_of: date | None = None,
) -> tuple[list[AumRecord], list[str]] | None:
    """Parse AMFI's nested `/api/average-aum-schemewise` JSON structure if applicable."""
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    groups = payload.get("data")
    if not isinstance(groups, list) or not any(isinstance(g, dict) and "schemes" in g for g in groups):
        return None

    period_s = payload.get("period") or payload.get("as_of")
    as_of = _parse_date(str(period_s) if period_s else None, default_as_of)

    records: list[AumRecord] = []
    rejected: list[str] = []
    idx = 0
    for group in groups:
        if not isinstance(group, dict):
            continue
        schemes = group.get("schemes") or []
        if not isinstance(schemes, list):
            continue
        for s in schemes:
            idx += 1
            if not isinstance(s, dict):
                continue
            code_val = s.get("AMFI_Code") or s.get("amfi_code") or s.get("scheme_code") or ""
            code = str(code_val).strip()
            name = str(s.get("SchemeNAVName") or s.get("scheme_name") or "").strip()
            isin = str(s.get("ISIN") or s.get("isin") or "").strip()
            aum_obj = s.get("AverageAumForTheMonth") or {}
            try:
                if isinstance(aum_obj, dict):
                    base_lakh = (
                        _parse_float(
                            str(
                                aum_obj.get(
                                    "ExcludingFundOfFundsDomesticButIncludingFundOfFundsOverseas", 0
                                )
                            )
                        )
                        or 0.0
                    )
                    fof_lakh = _parse_float(str(aum_obj.get("FundOfFundsDomestic", 0))) or 0.0
                    total_lakh = base_lakh + fof_lakh
                else:
                    total_lakh = _parse_float(str(aum_obj)) or 0.0

                aum_cr = round(total_lakh / 100.0, 2)
                if aum_cr <= 0:
                    rejected.append(f"scheme {idx} ({code or name}): non-positive AUM")
                    continue
                records.append(
                    AumRecord(
                        code=code,
                        isin=isin,
                        scheme_name=name,
                        aum_cr=aum_cr,
                        as_of=as_of,
                    )
                )
            except ValueError:
                rejected.append(f"scheme {idx} ({code or name}): non-numeric AUM")
    return records, rejected


def parse_aum_text(text: str, default_as_of: date | None = None) -> tuple[list[AumRecord], list[str]]:
    """Parse AMFI Average AUM JSON/CSV/HTML/text into AumRecord items and rejection strings."""
    nested = _parse_nested_amfi_aum_json(text, default_as_of=default_as_of)
    if nested is not None:
        return nested

    raw_rows = _dict_rows_from_text(text)
    records: list[AumRecord] = []
    rejected: list[str] = []

    for idx, row in enumerate(raw_rows, start=2):
        code = (_find_col(row, "amfi code", "amfi_code", "scheme code", "code", "scheme_code") or "").strip()
        isin = (_find_col(row, "isin", "isin_growth", "isin div payout/ isin growth") or "").strip()
        name = (
            _find_col(
                row,
                "scheme nav name",
                "schemenavname",
                "scheme name",
                "scheme_name",
                "name",
                "scheme",
            )
            or ""
        ).strip()
        as_of_s = _find_col(row, "aum_as_of", "as_of", "as of", "date", "quarter", "period")
        as_of = _parse_date(as_of_s, default_as_of)

        # Check if AUM is given in Crores or Lakhs
        aum_cr_s = _find_col(row, "aum_cr", "aum (cr)", "aum_crores", "average aum (cr)", "total aum (cr)")
        aum_lakh_s = _find_col(
            row,
            "aum_lakhs",
            "average aum (rs. in lakhs)",
            "aum (rs. in lakhs)",
            "excluding fund of funds - domestic but including fund of funds - overseas",
            "excludingfundoffundsdomesticbutincludingfundoffundsoverseas",
        )
        fof_lakh_s = _find_col(row, "fund of funds - domestic", "fundoffundsdomestic")
        generic_aum_s = _find_col(row, "average aum", "aum", "aaum")

        try:
            aum_cr: float | None = None
            if aum_cr_s is not None:
                aum_cr = _parse_float(aum_cr_s)
            elif aum_lakh_s is not None:
                base_lakh = _parse_float(aum_lakh_s) or 0.0
                fof_lakh = (_parse_float(fof_lakh_s) or 0.0) if fof_lakh_s is not None else 0.0
                if base_lakh > 0 or fof_lakh > 0:
                    aum_cr = round((base_lakh + fof_lakh) / 100.0, 2)
            elif generic_aum_s is not None:
                aum_cr = _parse_float(generic_aum_s)

            if aum_cr is None or aum_cr <= 0:
                rejected.append(f"row {idx}: invalid or non-positive AUM")
                continue

            records.append(
                AumRecord(
                    code=code,
                    isin=isin,
                    scheme_name=name,
                    aum_cr=aum_cr,
                    as_of=as_of,
                )
            )
        except ValueError:
            rejected.append(f"row {idx}: non-numeric AUM")

    return records, rejected


def load_aum_into_master(
    store: MetadataStore,
    text: str,
    default_as_of: date | None = None,
    *,
    only_codes: set[str] | None = None,
) -> AumLoadReport:
    """Parse AUM data and update matching rows in scheme_master."""
    records, rejected = parse_aum_text(text, default_as_of=default_as_of)
    report = AumLoadReport(rows_parsed=len(records) + len(rejected), rejected=rejected)
    if not records:
        return report

    master_rows = store.conn.execute(
        "SELECT code, isin_growth, isin_reinvest, name FROM scheme_master"
    ).fetchall()
    by_code: dict[str, list[str]] = {}
    by_isin: dict[str, list[str]] = {}
    by_exact_name: dict[str, list[str]] = {}
    by_prefix: dict[str, list[str]] = {}
    code_to_prefix: dict[str, str] = {}

    for r in master_rows:
        c = r["code"]
        by_code.setdefault(c, []).append(c)
        for isin_col in ("isin_growth", "isin_reinvest"):
            val = (r[isin_col] or "").strip()
            if val and val != "-":
                by_isin.setdefault(val, []).append(c)
        raw_name = (r["name"] or "").strip().lower()
        if raw_name:
            by_exact_name.setdefault(raw_name, []).append(c)
            norm = _normalize_scheme_prefix(raw_name)
            if norm:
                by_prefix.setdefault(norm, []).append(c)
                code_to_prefix[c] = norm

    variant_aum: dict[str, tuple[float, str]] = {}
    prefix_uncode_aum: dict[str, tuple[float, str]] = {}

    for rec in records:
        matched_codes: list[str] = []
        matched_by_prefix_only = False
        if rec.code and rec.code in by_code:
            matched_codes = by_code[rec.code]
        elif rec.isin and rec.isin in by_isin:
            matched_codes = by_isin[rec.isin]
        elif rec.scheme_name:
            low = rec.scheme_name.strip().lower()
            if low in by_exact_name:
                matched_codes = by_exact_name[low]
            else:
                norm = _normalize_scheme_prefix(low)
                matched_codes = by_prefix.get(norm, [])
                matched_by_prefix_only = True

        if not matched_codes:
            label = rec.code or rec.isin or rec.scheme_name or "unknown"
            report.unmatched.append(label)
            continue

        as_of_iso = rec.as_of.isoformat()
        if matched_by_prefix_only:
            norm = _normalize_scheme_prefix(rec.scheme_name.strip().lower())
            prev_val, _ = prefix_uncode_aum.get(norm, (0.0, as_of_iso))
            prefix_uncode_aum[norm] = (round(prev_val + rec.aum_cr, 2), as_of_iso)
        else:
            for mc in matched_codes:
                variant_aum[mc] = (round(rec.aum_cr, 2), as_of_iso)
        report.rows_matched += 1

    # Aggregate variant AUMs at the scheme-prefix level so every plan/option reflects Total Scheme AAUM
    prefix_totals: dict[str, tuple[float, str]] = dict(prefix_uncode_aum)
    for mc, (val, as_of_iso) in variant_aum.items():
        norm = code_to_prefix.get(mc)
        if norm:
            prev_val, _ = prefix_totals.get(norm, (0.0, as_of_iso))
            prefix_totals[norm] = (round(prev_val + val, 2), as_of_iso)

    updates: dict[str, tuple[float, str, str]] = {}
    for norm, (total_val, as_of_iso) in prefix_totals.items():
        for mc in by_prefix.get(norm, []):
            updates[mc] = (round(total_val, 2), as_of_iso, mc)
    for mc, (val, as_of_iso) in variant_aum.items():
        if mc not in updates:
            updates[mc] = (round(val, 2), as_of_iso, mc)

    if only_codes is not None:
        updates = {mc: u for mc, u in updates.items() if mc in only_codes}
        report.rows_matched = len(updates)

    if updates:
        store.conn.executemany(
            "UPDATE scheme_master SET aum_cr = ?, aum_as_of = ? WHERE code = ?",
            list(updates.values()),
        )
        store.conn.executemany(
            "UPDATE scheme SET aum_cr = ? WHERE code = ?",
            [(u[0], u[2]) for u in updates.values()],
        )
        store.conn.commit()
    return report


def load_aum_file(store: MetadataStore, path: str | Path) -> AumLoadReport:
    p = Path(path)
    raw = p.read_bytes()
    if raw.startswith(b"PK\x03\x04"):
        rows = _rows_from_xlsx_bytes(raw)
        text = json.dumps(rows)
    else:
        text = raw.decode("utf-8", errors="replace")
    return load_aum_into_master(store, text)


def sync_latest_aum(
    store: MetadataStore,
    timeout: int = 45,
    *,
    only_codes: set[str] | None = None,
) -> AumLoadReport:
    """Fetch the latest official AMFI quarterly Average AUM disclosure and load it into scheme_master."""
    text = fetch_latest_aum(timeout=timeout)
    return load_aum_into_master(store, text, only_codes=only_codes)

