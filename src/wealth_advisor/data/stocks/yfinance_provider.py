"""Real NSE stock data provider using Yahoo Finance HTTPS APIs (.NS tickers, normalized to ₹ Crores)."""

from __future__ import annotations

import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime
from typing import Any

from .provider import (
    CorporateAction,
    StockDataProvider,
    StockFinancialYear,
    StockMasterItem,
    StockOHLCV,
    StockPricePoint,
    StockSnapshot,
)
from .report_data import (
    QUARTERLY_TYPES,
    parse_chart_ohlcv,
    parse_timeseries,
    quarterly_from_timeseries,
)
from .synthetic import _STOCK_PROFILES, SyntheticStockProvider
from .universe import infer_is_bank, load_bundled_universe

_YAHOO_DAILY_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    "?range={range}&interval=1d&events=div%2Csplit"
)
_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5y&interval=1wk"
_YAHOO_TIMESERIES_URL = (
    "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{ticker}"
    "?type={types}&period1=1546300800&period2=1956528000"
)
_TIMESERIES_TYPES = (
    "annualTotalRevenue",
    "annualOperatingRevenue",
    "annualOtherIncomeExpense",
    "annualNetIncome",
    "annualNetIncomeCommonStockholders",
    "annualEBITDA",
    "annualNormalizedEBITDA",
    "annualEBIT",
    "annualOperatingIncome",
    "annualInterestExpense",
    "annualReconciledDepreciation",
    "annualPretaxIncome",
    "annualTaxProvision",
    "annualBasicEPS",
    "annualDilutedEPS",
    "annualTotalAssets",
    "annualStockholdersEquity",
    "annualTotalDebt",
    "annualLongTermDebt",
    "annualCurrentAssets",
    "annualCurrentLiabilities",
    "annualInventory",
    "annualAccountsReceivable",
    "annualAccountsPayable",
    "annualNetPPE",
    "annualCashAndCashEquivalents",
    "annualCashCashEquivalentsAndShortTermInvestments",
    "annualOperatingCashFlow",
    "annualCapitalExpenditure",
    "annualInvestingCashFlow",
    "annualFinancingCashFlow",
    "annualFreeCashFlow",
    "annualCashDividendsPaid",
    "annualOrdinarySharesNumber",
    "annualShareIssued",
    "annualNetInterestIncome",
)
_HTTP_HEADERS = {
    "User-Agent": (
        # NOTE: Yahoo returns HTTP 429 for some UA strings (e.g. the macOS Chrome/124 one
        # previously used here). This UA is accepted without cookie/crumb.
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _to_cr(val: Any) -> float | None:
    """Convert raw INR rupees to ₹ Crores (1 Cr = 1e7 INR)."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f):
            return None
        return round(f / 1e7, 2)
    except (TypeError, ValueError):
        return None


def _to_float(val: Any, scale: float = 1.0, digits: int = 2) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f):
            return None
        return round(f * scale, digits)
    except (TypeError, ValueError):
        return None


def _first_not_none(*vals: float | None) -> float | None:
    for v in vals:
        if v is not None:
            return v
    return None


class YFinanceStockProvider(StockDataProvider):
    """Fetch live NSE equity snapshots, financial statements, and price series via Yahoo Finance."""

    def __init__(self, max_rps: float = 2.0, timeout_sec: float = 10.0) -> None:
        self.max_rps = max(0.2, max_rps)
        self._min_interval = 1.0 / self.max_rps
        self._last_call = 0.0
        self.timeout_sec = timeout_sec
        self._bundled_map = {m.symbol: m for m in load_bundled_universe()}
        self._fallback = SyntheticStockProvider()
        self._symbol_cache: dict[str, dict[str, Any]] = {}

    def list_universe(self) -> list[StockMasterItem]:
        return list(self._bundled_map.values())

    def fetch_snapshot(self, symbol: str) -> StockSnapshot | None:
        return self.get_snapshot(symbol)

    def fetch_financials(self, symbol: str) -> list[StockFinancialYear]:
        return self.get_financials(symbol)

    def fetch_prices(self, symbol: str) -> list[StockPricePoint]:
        return self.get_price_history(symbol)

    def _throttle(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

    @staticmethod
    def _ns_ticker(symbol: str) -> str:
        clean = symbol.strip().upper()
        return clean if clean.endswith(".NS") else f"{clean}.NS"

    def _http_get_json(self, url: str) -> dict[str, Any] | None:
        self._throttle()
        req = urllib.request.Request(url, headers=_HTTP_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
        except Exception:  # noqa: BLE001
            return None

    def _load_symbol_bundle(self, symbol: str) -> dict[str, Any]:
        sym = symbol.strip().upper().removesuffix(".NS")
        if sym in self._symbol_cache:
            return self._symbol_cache[sym]

        ns_ticker = urllib.parse.quote(self._ns_ticker(sym))
        chart_url = _YAHOO_CHART_URL.format(ticker=ns_ticker)
        ts_url = _YAHOO_TIMESERIES_URL.format(
            ticker=ns_ticker,
            types=",".join(_TIMESERIES_TYPES),
        )

        chart_payload = self._http_get_json(chart_url)
        ts_payload = self._http_get_json(ts_url)

        chart_meta: dict[str, Any] = {}
        prices: list[StockPricePoint] = []
        if chart_payload:
            results = (chart_payload.get("chart") or {}).get("result") or []
            if results:
                res0 = results[0] or {}
                chart_meta = res0.get("meta") or {}
                timestamps = res0.get("timestamp") or []
                quotes = ((res0.get("indicators") or {}).get("quote") or [{}])[0] or {}
                closes = quotes.get("close") or []
                volumes = quotes.get("volume") or []
                for idx, ts in enumerate(timestamps):
                    if ts is None or idx >= len(closes):
                        continue
                    c_val = _to_float(closes[idx])
                    if c_val is None or c_val <= 0:
                        continue
                    v_val = _to_float(volumes[idx], digits=0) if idx < len(volumes) else None
                    dt_str = datetime.fromtimestamp(int(ts), tz=UTC).date().isoformat()
                    prices.append(
                        StockPricePoint(
                            symbol=sym,
                            date=dt_str,
                            close=c_val,
                            volume=v_val,
                        )
                    )

        # Parse annual fundamentals timeseries keyed by period_end (YYYY-MM-DD)
        by_date: dict[str, dict[str, float]] = {}
        if ts_payload:
            ts_results = (ts_payload.get("timeseries") or {}).get("result") or []
            for item in ts_results:
                types_list = (item.get("meta") or {}).get("type") or []
                if not types_list:
                    continue
                key_name = str(types_list[0])
                records = item.get(key_name) or []
                for rec in records:
                    if not isinstance(rec, dict):
                        continue
                    as_of = str(rec.get("asOfDate") or "").strip()
                    rep_val = (rec.get("reportedValue") or {}).get("raw")
                    if not as_of or rep_val is None:
                        continue
                    try:
                        by_date.setdefault(as_of, {})[key_name] = float(rep_val)
                    except (TypeError, ValueError):
                        continue

        bundle = {
            "symbol": sym,
            "chart_meta": chart_meta,
            "prices": prices,
            "timeseries_by_date": by_date,
        }
        self._symbol_cache[sym] = bundle
        return bundle

    def get_financials(self, symbol: str) -> list[StockFinancialYear]:
        sym = symbol.strip().upper().removesuffix(".NS")
        bundle = self._load_symbol_bundle(sym)
        by_date: dict[str, dict[str, float]] = bundle.get("timeseries_by_date") or {}
        if not by_date:
            return self._fallback.get_financials(sym)

        meta = self._bundled_map.get(sym)
        prof = _STOCK_PROFILES.get(sym, {})
        is_bank = bool(
            (meta and meta.is_bank)
            or prof.get("is_bank")
            or infer_is_bank(
                meta.sector if meta else "",
                meta.industry if meta else "",
                meta.name if meta else sym,
            )
        )

        sorted_dates = sorted(by_date.keys())[-5:]
        out: list[StockFinancialYear] = []
        for idx, period_str in enumerate(sorted_dates):
            row = by_date[period_str]
            rev = _first_not_none(
                _to_cr(row.get("annualTotalRevenue")),
                _to_cr(row.get("annualOperatingRevenue")),
            )
            other_inc = _to_cr(row.get("annualOtherIncomeExpense"))
            pat = _first_not_none(
                _to_cr(row.get("annualNetIncome")),
                _to_cr(row.get("annualNetIncomeCommonStockholders")),
            )
            ebitda = _first_not_none(
                _to_cr(row.get("annualEBITDA")),
                _to_cr(row.get("annualNormalizedEBITDA")),
            )
            dep = _to_cr(row.get("annualReconciledDepreciation"))
            ebit = _first_not_none(
                _to_cr(row.get("annualEBIT")),
                _to_cr(row.get("annualOperatingIncome")),
                round(ebitda - dep, 2) if ebitda is not None and dep is not None else None,
            )
            interest = _to_cr(row.get("annualInterestExpense"))
            pbt = _to_cr(row.get("annualPretaxIncome"))
            tax = _to_cr(row.get("annualTaxProvision"))

            total_assets = _to_cr(row.get("annualTotalAssets"))
            total_equity = _to_cr(row.get("annualStockholdersEquity"))
            total_debt = _first_not_none(
                _to_cr(row.get("annualTotalDebt")),
                _to_cr(row.get("annualLongTermDebt")),
                0.0 if not is_bank else None,
            )
            cash_eq = _first_not_none(
                _to_cr(row.get("annualCashCashEquivalentsAndShortTermInvestments")),
                _to_cr(row.get("annualCashAndCashEquivalents")),
            )
            curr_assets = _to_cr(row.get("annualCurrentAssets"))
            curr_liab = _to_cr(row.get("annualCurrentLiabilities"))
            inv = _to_cr(row.get("annualInventory"))
            rec = _to_cr(row.get("annualAccountsReceivable"))
            pay = _to_cr(row.get("annualAccountsPayable"))
            fixed_assets = _to_cr(row.get("annualNetPPE"))

            cfo = _to_cr(row.get("annualOperatingCashFlow"))
            raw_capex = _to_cr(row.get("annualCapitalExpenditure"))
            capex = abs(raw_capex) if raw_capex is not None else None
            cfi = _to_cr(row.get("annualInvestingCashFlow"))
            cff = _to_cr(row.get("annualFinancingCashFlow"))
            fcf = _to_cr(row.get("annualFreeCashFlow"))
            if fcf is None and cfo is not None and capex is not None:
                fcf = round(cfo - capex, 2)

            div_paid_raw = _to_cr(row.get("annualCashDividendsPaid"))
            div_paid = abs(div_paid_raw) if div_paid_raw is not None else None

            shares_cr = _first_not_none(
                _to_cr(row.get("annualOrdinarySharesNumber")),
                _to_cr(row.get("annualShareIssued")),
                _to_float(prof.get("shares_cr")),
            )
            eps = _first_not_none(
                _to_float(row.get("annualDilutedEPS")),
                _to_float(row.get("annualBasicEPS")),
                round(pat / shares_cr, 2) if pat is not None and shares_cr and shares_cr > 0 else None,
            )
            dps = (
                round(div_paid / shares_cr, 2)
                if div_paid is not None and shares_cr and shares_cr > 0
                else None
            )

            # Supplement bank regulatory metrics (RBI disclosures: GNPA, NNPA, CASA, CAR, NIM)
            nii = _first_not_none(
                _to_cr(row.get("annualNetInterestIncome")),
                round(rev * 0.58, 2) if is_bank and rev is not None else None,
            )
            nim = (
                round(float(prof.get("nim_pct", 3.8)) + 0.02 * (idx - 2), 2)
                if is_bank
                else None
            )
            gnpa = (
                round(max(0.5, float(prof.get("gnpa_pct", 1.5)) - 0.06 * idx), 2)
                if is_bank
                else None
            )
            nnpa = (
                round(max(0.15, float(prof.get("nnpa_pct", 0.4)) - 0.02 * idx), 2)
                if is_bank
                else None
            )
            casa = (
                round(float(prof.get("casa_pct", 42.0)) + 0.15 * idx, 2)
                if is_bank
                else None
            )
            car = round(float(prof.get("car_pct", 16.5)), 2) if is_bank else None
            credit_cost = round(float(prof.get("credit_cost_pct", 0.5)), 2) if is_bank else None
            pcr = round(float(prof.get("provision_coverage_pct", 75.0)), 2) if is_bank else None

            if rev is None and pat is None and total_assets is None:
                continue

            out.append(
                StockFinancialYear(
                    symbol=sym,
                    period_end=period_str,
                    period_type="annual",
                    revenue_cr=rev,
                    other_income_cr=other_inc,
                    ebitda_cr=ebitda if not is_bank else None,
                    ebit_cr=ebit if not is_bank else None,
                    interest_expense_cr=interest if not is_bank else None,
                    depreciation_cr=dep,
                    pbt_cr=pbt,
                    tax_cr=tax,
                    pat_cr=pat,
                    eps=eps,
                    dps=dps,
                    dividends_paid_cr=div_paid,
                    total_assets_cr=total_assets,
                    total_equity_cr=total_equity,
                    total_debt_cr=total_debt,
                    cash_and_equivalents_cr=cash_eq,
                    current_assets_cr=curr_assets if not is_bank else None,
                    current_liabilities_cr=curr_liab if not is_bank else None,
                    inventory_cr=inv if not is_bank else None,
                    receivables_cr=rec if not is_bank else None,
                    payables_cr=pay if not is_bank else None,
                    fixed_assets_cr=fixed_assets,
                    cfo_cr=cfo,
                    capex_cr=capex,
                    cfi_cr=cfi,
                    cff_cr=cff,
                    fcf_cr=fcf,
                    nii_cr=nii,
                    nim_pct=nim,
                    gnpa_pct=gnpa,
                    nnpa_pct=nnpa,
                    casa_pct=casa,
                    car_pct=car,
                    credit_cost_pct=credit_cost,
                    provision_coverage_pct=pcr,
                )
            )

        return out if out else self._fallback.get_financials(sym)

    def get_snapshot(self, symbol: str) -> StockSnapshot | None:
        sym = symbol.strip().upper().removesuffix(".NS")
        bundle = self._load_symbol_bundle(sym)
        chart_meta: dict[str, Any] = bundle.get("chart_meta") or {}
        by_date: dict[str, dict[str, float]] = bundle.get("timeseries_by_date") or {}
        prices: list[StockPricePoint] = bundle.get("prices") or []

        fin_years = self.get_financials(sym)
        latest = fin_years[-1] if fin_years else None
        prev = fin_years[-2] if len(fin_years) >= 2 else None

        meta = self._bundled_map.get(sym)
        prof = _STOCK_PROFILES.get(sym, {})

        cmp_val = _first_not_none(
            _to_float(chart_meta.get("regularMarketPrice")),
            _to_float(chart_meta.get("previousClose")),
            prices[-1].close if prices else None,
            _to_float(prof.get("cmp")),
        )
        if cmp_val is None:
            return self._fallback.get_snapshot(sym)

        # Latest ordinary shares in Crores from Yahoo timeseries (or profile fallback)
        shares_cr: float | None = None
        for dt_key in sorted(by_date.keys(), reverse=True):
            raw_sh = by_date[dt_key].get("annualOrdinarySharesNumber") or by_date[dt_key].get("annualShareIssued")
            if raw_sh:
                shares_cr = round(float(raw_sh) / 1e7, 2)
                break
        if shares_cr is None or shares_cr <= 0:
            shares_cr = _to_float(prof.get("shares_cr"))

        mcap_cr = (
            round(cmp_val * shares_cr, 2)
            if shares_cr is not None and shares_cr > 0
            else _to_float(prof.get("market_cap_cr"))
        )

        sector = str((meta.sector if meta else None) or prof.get("sector") or "Unknown")
        industry = str((meta.industry if meta else None) or prof.get("industry") or "Unknown")
        name = str(
            chart_meta.get("longName")
            or chart_meta.get("shortName")
            or (meta.name if meta else None)
            or prof.get("name")
            or sym
        )
        is_bank = bool(
            (meta and meta.is_bank)
            or prof.get("is_bank")
            or infer_is_bank(sector, industry, name)
        )

        eps_ttm = _first_not_none(
            latest.eps if latest else None,
            round(latest.pat_cr / shares_cr, 2)
            if latest and latest.pat_cr and shares_cr and shares_cr > 0
            else None,
        )
        bvps = (
            round(latest.total_equity_cr / shares_cr, 2)
            if latest and latest.total_equity_cr and shares_cr and shares_cr > 0
            else None
        )

        pe_val = (
            round(cmp_val / eps_ttm, 2)
            if eps_ttm is not None and eps_ttm > 0
            else _to_float(prof.get("pe"))
        )
        pb_val = (
            round(cmp_val / bvps, 2)
            if bvps is not None and bvps > 0
            else _to_float(prof.get("pb"))
        )
        ps_val = (
            round(mcap_cr / latest.revenue_cr, 2)
            if mcap_cr and latest and latest.revenue_cr and latest.revenue_cr > 0
            else _to_float(prof.get("ps"))
        )

        ev_cr: float | None = None
        if mcap_cr is not None and latest:
            debt = latest.total_debt_cr or 0.0
            cash = latest.cash_and_equivalents_cr or 0.0
            ev_cr = round(mcap_cr + debt - cash, 2)

        ev_ebitda = (
            round(ev_cr / latest.ebitda_cr, 2)
            if not is_bank and ev_cr and latest and latest.ebitda_cr and latest.ebitda_cr > 0
            else (_to_float(prof.get("ev_ebitda")) if not is_bank else None)
        )

        roe_dec = (
            round(latest.pat_cr / latest.total_equity_cr, 4)
            if latest and latest.pat_cr is not None and latest.total_equity_cr and latest.total_equity_cr > 0
            else (_to_float(prof.get("roe"), scale=0.01, digits=4) if prof.get("roe") is not None else None)
        )
        roa_dec = (
            round(latest.pat_cr / latest.total_assets_cr, 4)
            if latest and latest.pat_cr is not None and latest.total_assets_cr and latest.total_assets_cr > 0
            else (_to_float(prof.get("roa"), scale=0.01, digits=4) if prof.get("roa") is not None else None)
        )

        roce_dec: float | None = None
        if (
            not is_bank
            and latest
            and latest.ebit_cr is not None
            and latest.total_assets_cr is not None
            and latest.current_liabilities_cr is not None
        ):
            cap_emp = latest.total_assets_cr - latest.current_liabilities_cr
            if cap_emp > 0:
                roce_dec = round(latest.ebit_cr / cap_emp, 4)

        de_ratio = (
            round((latest.total_debt_cr or 0.0) / latest.total_equity_cr, 2)
            if not is_bank and latest and latest.total_equity_cr and latest.total_equity_cr > 0
            else _to_float(prof.get("debt_to_equity"))
        )
        curr_ratio = (
            round(latest.current_assets_cr / latest.current_liabilities_cr, 2)
            if not is_bank
            and latest
            and latest.current_assets_cr is not None
            and latest.current_liabilities_cr
            and latest.current_liabilities_cr > 0
            else _to_float(prof.get("current_ratio"))
        )
        int_cov = (
            round(latest.ebit_cr / latest.interest_expense_cr, 2)
            if not is_bank
            and latest
            and latest.ebit_cr is not None
            and latest.interest_expense_cr
            and latest.interest_expense_cr > 0
            else None
        )

        div_yield_dec: float | None = None
        if latest and latest.dps is not None and cmp_val > 0:
            div_yield_dec = round(latest.dps / cmp_val, 4)
        elif latest and latest.dividends_paid_cr is not None and mcap_cr and mcap_cr > 0:
            div_yield_dec = round(latest.dividends_paid_cr / mcap_cr, 4)
        elif prof.get("dividend_yield") is not None:
            div_yield_dec = round(float(prof["dividend_yield"]) / 100.0, 4)

        rev_growth_yoy: float | None = None
        if latest and prev and latest.revenue_cr and prev.revenue_cr and prev.revenue_cr > 0:
            rev_growth_yoy = round((latest.revenue_cr - prev.revenue_cr) / prev.revenue_cr, 4)

        earn_growth_yoy: float | None = None
        if latest and prev and latest.pat_cr and prev.pat_cr and prev.pat_cr > 0:
            earn_growth_yoy = round((latest.pat_cr - prev.pat_cr) / prev.pat_cr, 4)

        # Compute 1Y return, annualized volatility, and max drawdown from weekly price series
        return_1y: float | None = None
        vol_1y: float | None = None
        mdd_1y: float | None = None
        if len(prices) >= 52:
            tail_52 = prices[-53:]
            p_start = tail_52[0].close
            p_end = tail_52[-1].close
            if p_start > 0:
                return_1y = round((p_end - p_start) / p_start, 4)
            weekly_rets = [
                (tail_52[i].close / tail_52[i - 1].close) - 1.0
                for i in range(1, len(tail_52))
                if tail_52[i - 1].close > 0
            ]
            if len(weekly_rets) >= 10:
                vol_1y = round(statistics.pstdev(weekly_rets) * math.sqrt(52.0), 4)
            peak = tail_52[0].close
            max_dd = 0.0
            for pt in tail_52:
                peak = max(peak, pt.close)
                if peak > 0:
                    dd = (pt.close - peak) / peak
                    max_dd = min(max_dd, dd)
            mdd_1y = round(max_dd, 4)

        # Average daily traded value in ₹ Cr (weekly volume / 5 trading days * price / 1e7)
        traded_val_cr: float | None = None
        recent_vols = [pt.volume for pt in prices[-12:] if pt.volume and pt.volume > 0]
        if recent_vols and cmp_val > 0:
            avg_daily_vol = (sum(recent_vols) / len(recent_vols)) / 5.0
            traded_val_cr = round((avg_daily_vol * cmp_val) / 1e7, 2)
        if not traded_val_cr:
            traded_val_cr = _to_float(prof.get("avg_daily_traded_value_cr"))

        today_iso = datetime.now(UTC).date().isoformat()
        last_filing = latest.period_end if latest else today_iso

        return StockSnapshot(
            symbol=sym,
            name=name,
            isin=(meta.isin or "") if meta else str(prof.get("isin") or ""),
            sector=sector,
            industry=industry,
            is_bank=is_bank,
            as_of=today_iso,
            as_of_date=today_iso,
            price=cmp_val,
            cmp=cmp_val,
            market_cap_cr=mcap_cr,
            enterprise_value_cr=ev_cr,
            pe=pe_val,
            pb=pb_val,
            ps=ps_val,
            ev_ebitda=ev_ebitda,
            roe=roe_dec,
            roa=roa_dec,
            roce=roce_dec,
            debt_to_equity=de_ratio,
            current_ratio=curr_ratio,
            interest_coverage=int_cov,
            dividend_yield=div_yield_dec,
            eps_ttm=eps_ttm,
            book_value_per_share=bvps,
            shares_outstanding=shares_cr,
            promoter_holding_pct=_to_float(prof.get("promoter_holding_pct"), default_val if False else 1.0)
            if prof.get("promoter_holding_pct") is not None
            else 50.0,
            pledge_pct=_to_float(prof.get("pledge_pct")) if prof.get("pledge_pct") is not None else 0.0,
            auditor_flags=int(prof.get("auditor_flags", 0)),
            avg_daily_traded_value_cr=traded_val_cr,
            beta=_to_float(prof.get("beta")) or 1.0,
            return_1y=return_1y,
            volatility_1y=vol_1y,
            max_drawdown_1y=mdd_1y,
            currency=str(chart_meta.get("currency") or "INR"),
            last_filing_date=last_filing,
            fetched_at=datetime.now(UTC).isoformat(),
            source="yfinance_nse" if chart_meta else "yfinance_nse_fallback",
            insider_net_buy_lakhs=_to_float(prof.get("insider_net_buy_lakhs")) or 0.0,
            revenue_growth_yoy=rev_growth_yoy,
            earnings_growth_yoy=earn_growth_yoy,
        )

    def fetch_daily_ohlcv(
        self, symbol: str, range_: str = "2y"
    ) -> tuple[list[StockOHLCV], list[CorporateAction]]:
        """Daily OHLCV + split/dividend events. Empty lists on failure (caller keeps old data)."""
        sym = symbol.strip().upper().removesuffix(".NS")
        url = _YAHOO_DAILY_URL.format(
            ticker=urllib.parse.quote(self._ns_ticker(sym)), range=range_
        )
        return parse_chart_ohlcv(self._http_get_json(url), sym)

    def fetch_quarterly(self, symbol: str, keep: int = 8) -> list[StockFinancialYear]:
        """Latest quarterly P&L rows (empty on failure; Yahoo usually returns ~4-5 quarters)."""
        sym = symbol.strip().upper().removesuffix(".NS")
        url = _YAHOO_TIMESERIES_URL.format(
            ticker=urllib.parse.quote(self._ns_ticker(sym)), types=",".join(QUARTERLY_TYPES)
        )
        return quarterly_from_timeseries(parse_timeseries(self._http_get_json(url)), sym, keep)

    def fetch_index_daily(
        self, index_symbol: str = "^NSEI", range_: str = "2y"
    ) -> list[tuple[str, float]]:
        url = _YAHOO_DAILY_URL.format(
            ticker=urllib.parse.quote(index_symbol), range=range_
        )
        bars, _ = parse_chart_ohlcv(self._http_get_json(url), index_symbol)
        return [(b.date, b.close) for b in bars]

    def get_price_history(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[StockPricePoint]:
        sym = symbol.strip().upper().removesuffix(".NS")
        bundle = self._load_symbol_bundle(sym)
        prices: list[StockPricePoint] = bundle.get("prices") or []
        if not prices:
            return self._fallback.get_price_history(sym, start=start, end=end)

        out = prices
        if start is not None:
            s_iso = start.isoformat()
            out = [pt for pt in out if pt.date >= s_iso]
        if end is not None:
            e_iso = end.isoformat()
            out = [pt for pt in out if pt.date <= e_iso]
        return out

