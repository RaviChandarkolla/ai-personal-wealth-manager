"""Daily Stock Sync & Screening Service (persists to SQLite, runs Set 1 & Set 2 screens)."""

from __future__ import annotations

import json
import sqlite3
import statistics
import threading
import uuid
from datetime import UTC, date, datetime
from typing import Any

from wealth_advisor.audit.log import AuditLog
from wealth_advisor.data.stocks.provider import (
    StockDataProvider,
    StockFinancialYear,
    StockMasterItem,
    StockPricePoint,
    StockSectorMedian,
    StockSnapshot,
)
from wealth_advisor.data.stocks.synthetic import SyntheticStockProvider
from wealth_advisor.data.stocks.universe import load_nse_universe
from wealth_advisor.data.stocks.yfinance_provider import YFinanceStockProvider
from wealth_advisor.domain.stock_compare import StockCompareBundle
from wealth_advisor.domain.stock_rulesets import (
    StockScreenEvaluation,
    evaluate_stock_set1,
    evaluate_stock_set2,
)

_SYNC_LOCK = threading.Lock()


def _median_or_none(vals: list[float | None]) -> float | None:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return round(float(statistics.median(clean)), 4)


class StockSyncService:
    """Manages daily stock data synchronization, sector median calculation, and Set 1/Set 2 screening."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        data_mode: str = "synthetic",
        provider: StockDataProvider | None = None,
        max_rps: float = 2.0,
        stale_after_days: int = 3,
        snapshot_ttl_hours: int = 24,
        financials_ttl_days: int = 90,
    ) -> None:
        self.conn = conn
        self.data_mode = data_mode
        self.stale_after_days = stale_after_days
        self.snapshot_ttl_hours = snapshot_ttl_hours
        self.financials_ttl_days = financials_ttl_days
        if provider is not None:
            self.provider = provider
        elif data_mode in ("live", "real"):
            self.provider = YFinanceStockProvider(max_rps=max_rps)
        else:
            self.provider = SyntheticStockProvider()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def upsert_master_items(self, items: list[StockMasterItem]) -> None:
        now_iso = datetime.now(UTC).isoformat()
        with self.conn:
            for item in items:
                self.conn.execute(
                    """
                    INSERT INTO stock_master(symbol, isin, name, exchange, sector, industry, is_bank, is_active, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        isin=excluded.isin,
                        name=excluded.name,
                        exchange=excluded.exchange,
                        sector=excluded.sector,
                        industry=excluded.industry,
                        is_bank=excluded.is_bank,
                        is_active=excluded.is_active,
                        updated_at=excluded.updated_at
                    """,
                    (
                        item.symbol.upper(),
                        item.isin,
                        item.name,
                        item.exchange,
                        item.sector,
                        item.industry,
                        1 if item.is_bank else 0,
                        1 if item.is_active else 0,
                        now_iso,
                    ),
                )
            self._rebuild_fts()

    def _rebuild_fts(self) -> None:
        try:
            self.conn.execute("DELETE FROM stock_master_fts")
            self.conn.execute(
                """
                INSERT INTO stock_master_fts(symbol, name, sector, industry, isin)
                SELECT symbol, name, COALESCE(sector, ''), COALESCE(industry, ''), COALESCE(isin, '')
                FROM stock_master
                WHERE is_active = 1
                """
            )
        except sqlite3.OperationalError:
            pass

    def upsert_snapshot(self, snap: StockSnapshot) -> None:
        self.conn.execute(
            """
            INSERT INTO stock_snapshot(
                symbol, as_of_date, price, market_cap_cr, pe, pb, ps, ev_ebitda,
                dividend_yield, roe, roa, roce, debt_to_equity, current_ratio,
                interest_coverage, revenue_growth_yoy, earnings_growth_yoy,
                promoter_holding_pct, pledge_pct, auditor_flags, insider_net_buy_lakhs,
                avg_daily_traded_value_cr, return_1y, volatility_1y, max_drawdown_1y,
                source, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                as_of_date=excluded.as_of_date,
                price=excluded.price,
                market_cap_cr=excluded.market_cap_cr,
                pe=excluded.pe,
                pb=excluded.pb,
                ps=excluded.ps,
                ev_ebitda=excluded.ev_ebitda,
                dividend_yield=excluded.dividend_yield,
                roe=excluded.roe,
                roa=excluded.roa,
                roce=excluded.roce,
                debt_to_equity=excluded.debt_to_equity,
                current_ratio=excluded.current_ratio,
                interest_coverage=excluded.interest_coverage,
                revenue_growth_yoy=excluded.revenue_growth_yoy,
                earnings_growth_yoy=excluded.earnings_growth_yoy,
                promoter_holding_pct=excluded.promoter_holding_pct,
                pledge_pct=excluded.pledge_pct,
                auditor_flags=excluded.auditor_flags,
                insider_net_buy_lakhs=excluded.insider_net_buy_lakhs,
                avg_daily_traded_value_cr=excluded.avg_daily_traded_value_cr,
                return_1y=excluded.return_1y,
                volatility_1y=excluded.volatility_1y,
                max_drawdown_1y=excluded.max_drawdown_1y,
                source=excluded.source,
                raw_json=excluded.raw_json
            """,
            (
                snap.symbol.upper(),
                snap.as_of_date,
                snap.price,
                snap.market_cap_cr,
                snap.pe,
                snap.pb,
                snap.ps,
                snap.ev_ebitda,
                snap.dividend_yield,
                snap.roe,
                snap.roa,
                snap.roce,
                snap.debt_to_equity,
                snap.current_ratio,
                snap.interest_coverage,
                snap.revenue_growth_yoy,
                snap.earnings_growth_yoy,
                snap.promoter_holding_pct,
                snap.pledge_pct,
                snap.auditor_flags,
                snap.insider_net_buy_lakhs,
                snap.avg_daily_traded_value_cr,
                snap.return_1y,
                snap.volatility_1y,
                snap.max_drawdown_1y,
                snap.source,
                json.dumps(snap.raw_json) if snap.raw_json else None,
            ),
        )

    def upsert_financials(self, rows: list[StockFinancialYear]) -> None:
        for fy in rows:
            self.conn.execute(
                """
                INSERT INTO stock_financials(
                    symbol, fiscal_year, revenue_cr, ebitda_cr, ebit_cr, interest_cr,
                    pbt_cr, tax_cr, pat_cr, eps, dps, bvps, total_assets_cr, net_worth_cr,
                    total_debt_cr, current_assets_cr, current_liabilities_cr, inventory_cr,
                    receivables_cr, payables_cr, cash_and_equivalents_cr, cfo_cr, capex_cr,
                    fcf_cr, dividends_paid_cr, nii_cr, nim_pct, gnpa_pct, nnpa_pct,
                    casa_pct, car_pct, credit_cost_pct, provision_coverage_pct,
                    deposits_cr, advances_cr, as_of_date, source
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(symbol, fiscal_year) DO UPDATE SET
                    revenue_cr=excluded.revenue_cr,
                    ebitda_cr=excluded.ebitda_cr,
                    ebit_cr=excluded.ebit_cr,
                    interest_cr=excluded.interest_cr,
                    pbt_cr=excluded.pbt_cr,
                    tax_cr=excluded.tax_cr,
                    pat_cr=excluded.pat_cr,
                    eps=excluded.eps,
                    dps=excluded.dps,
                    bvps=excluded.bvps,
                    total_assets_cr=excluded.total_assets_cr,
                    net_worth_cr=excluded.net_worth_cr,
                    total_debt_cr=excluded.total_debt_cr,
                    current_assets_cr=excluded.current_assets_cr,
                    current_liabilities_cr=excluded.current_liabilities_cr,
                    inventory_cr=excluded.inventory_cr,
                    receivables_cr=excluded.receivables_cr,
                    payables_cr=excluded.payables_cr,
                    cash_and_equivalents_cr=excluded.cash_and_equivalents_cr,
                    cfo_cr=excluded.cfo_cr,
                    capex_cr=excluded.capex_cr,
                    fcf_cr=excluded.fcf_cr,
                    dividends_paid_cr=excluded.dividends_paid_cr,
                    nii_cr=excluded.nii_cr,
                    nim_pct=excluded.nim_pct,
                    gnpa_pct=excluded.gnpa_pct,
                    nnpa_pct=excluded.nnpa_pct,
                    casa_pct=excluded.casa_pct,
                    car_pct=excluded.car_pct,
                    credit_cost_pct=excluded.credit_cost_pct,
                    provision_coverage_pct=excluded.provision_coverage_pct,
                    deposits_cr=excluded.deposits_cr,
                    advances_cr=excluded.advances_cr,
                    as_of_date=excluded.as_of_date,
                    source=excluded.source
                """,
                (
                    fy.symbol.upper(),
                    fy.fiscal_year,
                    fy.revenue_cr,
                    fy.ebitda_cr,
                    fy.ebit_cr,
                    fy.interest_cr,
                    fy.pbt_cr,
                    fy.tax_cr,
                    fy.pat_cr,
                    fy.eps,
                    fy.dps,
                    fy.bvps,
                    fy.total_assets_cr,
                    fy.net_worth_cr,
                    fy.total_debt_cr,
                    fy.current_assets_cr,
                    fy.current_liabilities_cr,
                    fy.inventory_cr,
                    fy.receivables_cr,
                    fy.payables_cr,
                    fy.cash_and_equivalents_cr,
                    fy.cfo_cr,
                    fy.capex_cr,
                    fy.fcf_cr,
                    fy.dividends_paid_cr,
                    fy.nii_cr,
                    fy.nim_pct,
                    fy.gnpa_pct,
                    fy.nnpa_pct,
                    fy.casa_pct,
                    fy.car_pct,
                    fy.credit_cost_pct,
                    fy.provision_coverage_pct,
                    fy.deposits_cr,
                    fy.advances_cr,
                    fy.as_of_date,
                    fy.source,
                ),
            )

    def upsert_prices(self, rows: list[StockPricePoint]) -> None:
        for pt in rows:
            self.conn.execute(
                """
                INSERT INTO stock_prices(symbol, price_date, close, volume)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol, price_date) DO UPDATE SET
                    close=excluded.close,
                    volume=excluded.volume
                """,
                (pt.symbol.upper(), pt.price_date, pt.close, pt.volume),
            )

    def upsert_sector_median(self, med: StockSectorMedian) -> None:
        self.conn.execute(
            """
            INSERT INTO stock_sector_medians(
                sector, is_bank, peer_count, median_pe, median_pb, median_ps,
                median_ev_ebitda, median_roe, median_roce, median_debt_to_equity,
                median_nim_pct, median_gnpa_pct, median_nnpa_pct, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sector) DO UPDATE SET
                is_bank=excluded.is_bank,
                peer_count=excluded.peer_count,
                median_pe=excluded.median_pe,
                median_pb=excluded.median_pb,
                median_ps=excluded.median_ps,
                median_ev_ebitda=excluded.median_ev_ebitda,
                median_roe=excluded.median_roe,
                median_roce=excluded.median_roce,
                median_debt_to_equity=excluded.median_debt_to_equity,
                median_nim_pct=excluded.median_nim_pct,
                median_gnpa_pct=excluded.median_gnpa_pct,
                median_nnpa_pct=excluded.median_nnpa_pct,
                updated_at=excluded.updated_at
            """,
            (
                med.sector,
                1 if med.is_bank else 0,
                med.peer_count,
                med.median_pe,
                med.median_pb,
                med.median_ps,
                med.median_ev_ebitda,
                med.median_roe,
                med.median_roce,
                med.median_debt_to_equity,
                med.median_nim_pct,
                med.median_gnpa_pct,
                med.median_nnpa_pct,
                med.updated_at,
            ),
        )

    def upsert_screen_result(
        self,
        ev: StockScreenEvaluation,
        *,
        sync_run_id: str,
        evaluated_at: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO stock_screen_result(
                symbol, rule_set, passed, passed_count, evaluated_count, total_criteria,
                pass_ratio, composite_score, sub_scores_json, criteria_json, red_flags_json,
                valuation_json, sync_run_id, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, rule_set) DO UPDATE SET
                passed=excluded.passed,
                passed_count=excluded.passed_count,
                evaluated_count=excluded.evaluated_count,
                total_criteria=excluded.total_criteria,
                pass_ratio=excluded.pass_ratio,
                composite_score=excluded.composite_score,
                sub_scores_json=excluded.sub_scores_json,
                criteria_json=excluded.criteria_json,
                red_flags_json=excluded.red_flags_json,
                valuation_json=excluded.valuation_json,
                sync_run_id=excluded.sync_run_id,
                evaluated_at=excluded.evaluated_at
            """,
            (
                ev.symbol.upper(),
                ev.rule_set,
                1 if ev.passed else 0,
                ev.passed_count,
                ev.evaluated_count,
                ev.total_criteria,
                ev.pass_ratio,
                ev.composite_score,
                json.dumps(ev.sub_scores),
                json.dumps([c.to_dict() for c in ev.criteria]),
                json.dumps(ev.red_flags),
                json.dumps(ev.valuation_summary),
                sync_run_id,
                evaluated_at,
            ),
        )

    # ------------------------------------------------------------------
    # Read / Query helpers
    # ------------------------------------------------------------------
    def get_master(self, symbol: str) -> StockMasterItem | None:
        row = self.conn.execute(
            "SELECT * FROM stock_master WHERE symbol = ?",
            (symbol.upper(),),
        ).fetchone()
        if not row:
            return None
        return StockMasterItem(
            symbol=row["symbol"],
            isin=row["isin"],
            name=row["name"],
            exchange=row["exchange"],
            sector=row["sector"] or "Diversified",
            industry=row["industry"] or "General",
            is_bank=bool(row["is_bank"]),
            is_active=bool(row["is_active"]),
        )

    def get_snapshot(self, symbol: str) -> StockSnapshot | None:
        row = self.conn.execute(
            "SELECT * FROM stock_snapshot WHERE symbol = ?",
            (symbol.upper(),),
        ).fetchone()
        if not row:
            return None
        return self._row_to_snapshot(row)

    def _row_to_snapshot(self, row: sqlite3.Row) -> StockSnapshot:
        raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
        return StockSnapshot(
            symbol=row["symbol"],
            as_of_date=row["as_of_date"],
            price=row["price"],
            market_cap_cr=row["market_cap_cr"],
            pe=row["pe"],
            pb=row["pb"],
            ps=row["ps"],
            ev_ebitda=row["ev_ebitda"],
            dividend_yield=row["dividend_yield"],
            roe=row["roe"],
            roa=row["roa"],
            roce=row["roce"],
            debt_to_equity=row["debt_to_equity"],
            current_ratio=row["current_ratio"],
            interest_coverage=row["interest_coverage"],
            revenue_growth_yoy=row["revenue_growth_yoy"],
            earnings_growth_yoy=row["earnings_growth_yoy"],
            promoter_holding_pct=row["promoter_holding_pct"],
            pledge_pct=row["pledge_pct"],
            auditor_flags=int(row["auditor_flags"] or 0),
            insider_net_buy_lakhs=float(row["insider_net_buy_lakhs"] or 0.0),
            avg_daily_traded_value_cr=row["avg_daily_traded_value_cr"],
            return_1y=row["return_1y"],
            volatility_1y=row["volatility_1y"],
            max_drawdown_1y=row["max_drawdown_1y"],
            source=row["source"] or "synthetic",
            raw_json=raw,
        )

    def get_financials(self, symbol: str) -> list[StockFinancialYear]:
        rows = self.conn.execute(
            "SELECT * FROM stock_financials WHERE symbol = ? ORDER BY fiscal_year ASC",
            (symbol.upper(),),
        ).fetchall()
        return [
            StockFinancialYear(
                symbol=r["symbol"],
                fiscal_year=int(r["fiscal_year"]),
                revenue_cr=r["revenue_cr"],
                ebitda_cr=r["ebitda_cr"],
                ebit_cr=r["ebit_cr"],
                interest_cr=r["interest_cr"],
                pbt_cr=r["pbt_cr"],
                tax_cr=r["tax_cr"],
                pat_cr=r["pat_cr"],
                eps=r["eps"],
                dps=r["dps"],
                bvps=r["bvps"],
                total_assets_cr=r["total_assets_cr"],
                net_worth_cr=r["net_worth_cr"],
                total_debt_cr=r["total_debt_cr"],
                current_assets_cr=r["current_assets_cr"],
                current_liabilities_cr=r["current_liabilities_cr"],
                inventory_cr=r["inventory_cr"],
                receivables_cr=r["receivables_cr"],
                payables_cr=r["payables_cr"],
                cash_and_equivalents_cr=r["cash_and_equivalents_cr"],
                cfo_cr=r["cfo_cr"],
                capex_cr=r["capex_cr"],
                fcf_cr=r["fcf_cr"],
                dividends_paid_cr=r["dividends_paid_cr"],
                nii_cr=r["nii_cr"],
                nim_pct=r["nim_pct"],
                gnpa_pct=r["gnpa_pct"],
                nnpa_pct=r["nnpa_pct"],
                casa_pct=r["casa_pct"],
                car_pct=r["car_pct"],
                credit_cost_pct=r["credit_cost_pct"],
                provision_coverage_pct=r["provision_coverage_pct"],
                deposits_cr=r["deposits_cr"],
                advances_cr=r["advances_cr"],
                as_of_date=r["as_of_date"] or "",
                source=r["source"] or "synthetic",
            )
            for r in rows
        ]

    def get_prices(self, symbol: str) -> list[StockPricePoint]:
        rows = self.conn.execute(
            "SELECT * FROM stock_prices WHERE symbol = ? ORDER BY price_date ASC",
            (symbol.upper(),),
        ).fetchall()
        return [
            StockPricePoint(
                symbol=r["symbol"],
                price_date=r["price_date"],
                close=float(r["close"]),
                volume=float(r["volume"]) if r["volume"] is not None else None,
            )
            for r in rows
        ]

    def get_sector_median(self, sector: str) -> StockSectorMedian | None:
        row = self.conn.execute(
            "SELECT * FROM stock_sector_medians WHERE sector = ?",
            (sector,),
        ).fetchone()
        if not row:
            return None
        return StockSectorMedian(
            sector=row["sector"],
            is_bank=bool(row["is_bank"]),
            peer_count=int(row["peer_count"] or 0),
            median_pe=row["median_pe"],
            median_pb=row["median_pb"],
            median_ps=row["median_ps"],
            median_ev_ebitda=row["median_ev_ebitda"],
            median_roe=row["median_roe"],
            median_roce=row["median_roce"],
            median_debt_to_equity=row["median_debt_to_equity"],
            median_nim_pct=row["median_nim_pct"],
            median_gnpa_pct=row["median_gnpa_pct"],
            median_nnpa_pct=row["median_nnpa_pct"],
            updated_at=row["updated_at"] or "",
        )

    def get_stock_bundle(self, symbol: str) -> StockCompareBundle | None:
        master = self.get_master(symbol)
        snap = self.get_snapshot(symbol)
        if master is None or snap is None:
            return None
        fins = self.get_financials(symbol)
        prices = self.get_prices(symbol)
        sec_med = self.get_sector_median(master.sector)
        return StockCompareBundle(
            master=master,
            snapshot=snap,
            financials=fins,
            prices=prices,
            sector_median=sec_med,
        )

    def get_sector_peers(
        self,
        sector: str,
        *,
        exclude_symbol: str | None = None,
        rule_set: str = "set2",
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        rs = rule_set if rule_set in ("set1", "set2") else "set2"
        rows = self.conn.execute(
            """
            SELECT m.symbol, m.name, m.sector, m.is_bank,
                   s.price, s.market_cap_cr, s.pe, s.pb, s.roe, s.roce,
                   sr.passed, sr.passed_count, sr.evaluated_count, sr.composite_score
            FROM stock_master m
            JOIN stock_snapshot s ON s.symbol = m.symbol
            LEFT JOIN stock_screen_result sr ON sr.symbol = m.symbol AND sr.rule_set = ?
            WHERE m.sector = ? AND m.is_active = 1 AND (? IS NULL OR m.symbol != ?)
            ORDER BY COALESCE(sr.composite_score, 0) DESC, COALESCE(s.market_cap_cr, 0) DESC
            LIMIT ?
            """,
            (rs, sector, exclude_symbol, exclude_symbol, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Sector Median Calculation & Full Screening Pass
    # ------------------------------------------------------------------
    def recompute_sector_medians_and_screens(
        self,
        *,
        sync_run_id: str,
        min_market_cap_cr: float | None = None,
        min_traded_value_cr: float | None = None,
    ) -> tuple[int, int]:
        """Recompute sector medians across all synced stocks and evaluate Set 1 & Set 2."""
        masters = self.conn.execute(
            "SELECT symbol, sector, is_bank FROM stock_master WHERE is_active = 1"
        ).fetchall()
        master_by_sym = {
            r["symbol"]: (r["sector"] or "Diversified", bool(r["is_bank"])) for r in masters
        }

        snaps_rows = self.conn.execute("SELECT * FROM stock_snapshot").fetchall()
        snaps_by_sym = {r["symbol"]: self._row_to_snapshot(r) for r in snaps_rows}

        fins_by_sym: dict[str, list[StockFinancialYear]] = {}
        for sym in snaps_by_sym:
            fins_by_sym[sym] = self.get_financials(sym)

        # Group by sector to compute medians
        by_sector: dict[str, list[str]] = {}
        for sym in snaps_by_sym:
            sec, _ = master_by_sym.get(sym, ("Diversified", False))
            by_sector.setdefault(sec, []).append(sym)

        now_iso = datetime.now(UTC).isoformat()
        sector_medians: dict[str, StockSectorMedian] = {}

        with self.conn:
            for sec, syms in by_sector.items():
                is_bank_sec = any(master_by_sym.get(s, ("", False))[1] for s in syms)
                pes = [snaps_by_sym[s].pe for s in syms]
                pbs = [snaps_by_sym[s].pb for s in syms]
                pss = [snaps_by_sym[s].ps for s in syms]
                evs = [snaps_by_sym[s].ev_ebitda for s in syms]
                roes = [snaps_by_sym[s].roe for s in syms]
                roces = [snaps_by_sym[s].roce for s in syms]
                des = [snaps_by_sym[s].debt_to_equity for s in syms]

                nims: list[float | None] = []
                gnpas: list[float | None] = []
                nnpas: list[float | None] = []
                for s in syms:
                    fys = fins_by_sym.get(s, [])
                    if fys:
                        latest = fys[-1]
                        nims.append(latest.nim_pct)
                        gnpas.append(latest.gnpa_pct)
                        nnpas.append(latest.nnpa_pct)

                med = StockSectorMedian(
                    sector=sec,
                    is_bank=is_bank_sec,
                    peer_count=len(syms),
                    median_pe=_median_or_none(pes),
                    median_pb=_median_or_none(pbs),
                    median_ps=_median_or_none(pss),
                    median_ev_ebitda=_median_or_none(evs),
                    median_roe=_median_or_none(roes),
                    median_roce=_median_or_none(roces),
                    median_debt_to_equity=_median_or_none(des),
                    median_nim_pct=_median_or_none(nims),
                    median_gnpa_pct=_median_or_none(gnpas),
                    median_nnpa_pct=_median_or_none(nnpas),
                    updated_at=now_iso,
                )
                sector_medians[sec] = med
                self.upsert_sector_median(med)

            set1_pass = 0
            set2_pass = 0
            for sym, snap in snaps_by_sym.items():
                sec, is_b = master_by_sym.get(sym, ("Diversified", False))
                fys = fins_by_sym.get(sym, [])
                sec_med = sector_medians.get(sec)

                ev1 = evaluate_stock_set1(
                    snap,
                    fys,
                    sec_med,
                    is_bank=is_b,
                    min_market_cap_cr=min_market_cap_cr,
                    min_traded_value_cr=min_traded_value_cr,
                )
                ev2 = evaluate_stock_set2(
                    snap,
                    fys,
                    sec_med,
                    is_bank=is_b,
                    min_market_cap_cr=min_market_cap_cr,
                    min_traded_value_cr=min_traded_value_cr,
                )
                if ev1.passed:
                    set1_pass += 1
                if ev2.passed:
                    set2_pass += 1

                self.upsert_screen_result(ev1, sync_run_id=sync_run_id, evaluated_at=now_iso)
                self.upsert_screen_result(ev2, sync_run_id=sync_run_id, evaluated_at=now_iso)

        return set1_pass, set2_pass

    # ------------------------------------------------------------------
    # Main Sync Entry Point
    # ------------------------------------------------------------------
    def sync(
        self,
        *,
        mode: str = "full",
        force: bool = False,
        sector: str | None = None,
        index_tag: str | None = None,
        limit: int | None = None,
        min_market_cap_cr: float | None = None,
        min_traded_value_cr: float | None = None,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run the Stock Sync & Screening job.

        Modes:
          - 'full': Fetch universe + snapshots + 5Y financials + prices, compute sector medians, run Set 1 & Set 2.
          - 'retry_failed': Re-fetch only symbols that failed or lack snapshots in the latest run.
          - 'rescreen': Skip network fetch; recompute sector medians and re-run Set 1 & Set 2 screens on cached data.
        """
        today_str = date.today().isoformat()

        # Check daily idempotency guard (if mode == 'full' and not force and no custom symbols)
        if mode == "full" and not force and not symbols:
            existing = self.conn.execute(
                """
                SELECT * FROM stock_sync_run
                WHERE run_date = ? AND status = 'completed' AND data_mode = ? AND succeeded_symbols > 0
                ORDER BY completed_at DESC LIMIT 1
                """,
                (today_str, self.data_mode),
            ).fetchone()
            snap_count = self.conn.execute("SELECT COUNT(*) AS c FROM stock_snapshot").fetchone()["c"]
            if existing is not None and snap_count > 0:
                status = self.get_status()
                status["already_synced_today"] = True
                return status

        if not _SYNC_LOCK.acquire(blocking=False):
            status = self.get_status()
            status["sync_in_progress"] = True
            return status

        run_id = f"sync_{uuid.uuid4().hex[:12]}"
        started_at = datetime.now(UTC).isoformat()

        try:
            # Ensure universe is loaded
            universe = self.provider.list_universe()
            if universe:
                self.upsert_master_items(universe)

            if mode == "rescreen":
                all_Count = self.conn.execute(
                    "SELECT COUNT(*) AS c FROM stock_snapshot"
                ).fetchone()["c"]
                set1_pass, set2_pass = self.recompute_sector_medians_and_screens(
                    sync_run_id=run_id,
                    min_market_cap_cr=min_market_cap_cr,
                    min_traded_value_cr=min_traded_value_cr,
                )
                completed_at = datetime.now(UTC).isoformat()
                with self.conn:
                    self.conn.execute(
                        """
                        INSERT INTO stock_sync_run(
                            run_id, run_date, mode, data_mode, status, total_symbols,
                            processed_symbols, succeeded_symbols, failed_symbols,
                            set1_pass_count, set2_pass_count, min_market_cap_cr,
                            min_traded_value_cr, failed_details_json, started_at, completed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            today_str,
                            "rescreen",
                            self.data_mode,
                            "completed",
                            all_Count,
                            all_Count,
                            all_Count,
                            0,
                            set1_pass,
                            set2_pass,
                            min_market_cap_cr,
                            min_traded_value_cr,
                            "[]",
                            started_at,
                            completed_at,
                        ),
                    )
                AuditLog(self.conn).append(
                    run_id,
                    "stock_sync",
                    "rescreen_completed",
                    {
                        "total_symbols": all_Count,
                        "set1_pass_count": set1_pass,
                        "set2_pass_count": set2_pass,
                        "min_market_cap_cr": min_market_cap_cr,
                    },
                )
                status = self.get_status()
                status["already_synced_today"] = False
                return status

            # Determine target symbols for 'full' or 'retry_failed'
            if symbols:
                target_symbols = [s.upper() for s in symbols]
            elif mode == "retry_failed":
                last_run = self.conn.execute(
                    "SELECT failed_details_json FROM stock_sync_run ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
                failed_syms: list[str] = []
                if last_run and last_run["failed_details_json"]:
                    try:
                        parsed = json.loads(last_run["failed_details_json"])
                        failed_syms = [item["symbol"] for item in parsed if "symbol" in item]
                    except (ValueError, TypeError, KeyError):
                        failed_syms = []
                missing_rows = self.conn.execute(
                    """
                    SELECT m.symbol FROM stock_master m
                    LEFT JOIN stock_snapshot s ON s.symbol = m.symbol
                    WHERE m.is_active = 1 AND s.symbol IS NULL
                    """
                ).fetchall()
                target_symbols = sorted(set(failed_syms + [r["symbol"] for r in missing_rows]))
            else:
                rows = self.conn.execute(
                    "SELECT symbol, sector FROM stock_master WHERE is_active = 1 ORDER BY symbol"
                ).fetchall()
                target_symbols = [
                    r["symbol"]
                    for r in rows
                    if not sector or (r["sector"] or "").lower() == sector.lower()
                ]
                if index_tag:
                    allowed = {u.symbol for u in load_nse_universe(index_tag=index_tag)}
                    target_symbols = [s for s in target_symbols if s in allowed]
                if limit is not None and limit > 0:
                    target_symbols = target_symbols[:limit]

            total_symbols = len(target_symbols)
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO stock_sync_run(
                        run_id, run_date, mode, data_mode, status, total_symbols,
                        processed_symbols, succeeded_symbols, failed_symbols,
                        set1_pass_count, set2_pass_count, min_market_cap_cr,
                        min_traded_value_cr, failed_details_json, started_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        run_id,
                        today_str,
                        mode,
                        self.data_mode,
                        "running",
                        total_symbols,
                        0,
                        0,
                        0,
                        0,
                        0,
                        min_market_cap_cr,
                        min_traded_value_cr,
                        "[]",
                        started_at,
                    ),
                )

            processed = 0
            succeeded = 0
            failed_details: list[dict[str, str]] = []

            for sym in target_symbols:
                processed += 1
                try:
                    snap = self.provider.fetch_snapshot(sym)
                    if snap is None:
                        failed_details.append({"symbol": sym, "error": "No snapshot returned"})
                        continue

                    # Optional pre-filter after quote step, before fetching full statements
                    if min_market_cap_cr is not None and (snap.market_cap_cr or 0.0) < min_market_cap_cr:
                        with self.conn:
                            self.conn.execute("DELETE FROM stock_snapshot WHERE symbol = ?", (sym,))
                            self.conn.execute("DELETE FROM stock_screen_result WHERE symbol = ?", (sym,))
                        continue
                    if (
                        min_traded_value_cr is not None
                        and (snap.avg_daily_traded_value_cr or 0.0) < min_traded_value_cr
                    ):
                        with self.conn:
                            self.conn.execute("DELETE FROM stock_snapshot WHERE symbol = ?", (sym,))
                            self.conn.execute("DELETE FROM stock_screen_result WHERE symbol = ?", (sym,))
                        continue

                    # Skip refetching financials if cached statements are still fresh
                    need_fins = True
                    if not force:
                        existing_fins = self.get_financials(sym)
                        if existing_fins and existing_fins[-1].as_of_date:
                            try:
                                fin_dt = date.fromisoformat(existing_fins[-1].as_of_date[:10])
                                if (date.today() - fin_dt).days < self.financials_ttl_days:
                                    need_fins = False
                            except ValueError:
                                need_fins = True

                    fins = self.provider.fetch_financials(sym) if need_fins else []
                    prices = self.provider.fetch_prices(sym)
                    with self.conn:
                        self.upsert_snapshot(snap)
                        if fins:
                            self.upsert_financials(fins)
                        if prices:
                            self.upsert_prices(prices)
                    succeeded += 1
                except Exception as exc:  # noqa: BLE001
                    failed_details.append({"symbol": sym, "error": str(exc)})

            set1_pass, set2_pass = self.recompute_sector_medians_and_screens(
                sync_run_id=run_id,
                min_market_cap_cr=min_market_cap_cr,
                min_traded_value_cr=min_traded_value_cr,
            )

            completed_at = datetime.now(UTC).isoformat()
            with self.conn:
                self.conn.execute(
                    """
                    UPDATE stock_sync_run
                    SET status = 'completed',
                        processed_symbols = ?,
                        succeeded_symbols = ?,
                        failed_symbols = ?,
                        set1_pass_count = ?,
                        set2_pass_count = ?,
                        failed_details_json = ?,
                        completed_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        processed,
                        succeeded,
                        len(failed_details),
                        set1_pass,
                        set2_pass,
                        json.dumps(failed_details),
                        completed_at,
                        run_id,
                    ),
                )

            AuditLog(self.conn).append(
                run_id,
                "stock_sync",
                "sync_completed",
                {
                    "mode": mode,
                    "data_mode": self.data_mode,
                    "total_symbols": total_symbols,
                    "succeeded_symbols": succeeded,
                    "failed_symbols": len(failed_details),
                    "set1_pass_count": set1_pass,
                    "set2_pass_count": set2_pass,
                    "min_market_cap_cr": min_market_cap_cr,
                    "min_traded_value_cr": min_traded_value_cr,
                },
            )

            status = self.get_status()
            status["already_synced_today"] = False
            return status
        finally:
            _SYNC_LOCK.release()

    def ensure_seeded(self) -> dict[str, Any]:
        """Ensure that in synthetic mode (or empty DB in synthetic mode), stocks are synced."""
        count = self.conn.execute("SELECT COUNT(*) AS c FROM stock_snapshot").fetchone()["c"]
        if count == 0 and self.data_mode == "synthetic":
            return self.sync(mode="full", force=True)
        return self.get_status()

    def ensure_stock_cached(
        self,
        symbol: str,
        *,
        force: bool = False,
        force_refresh: bool = False,
    ) -> StockCompareBundle | None:
        """Return cached StockCompareBundle for `symbol`, fetching live/synthetic if missing or forced."""
        sym = symbol.upper().strip()
        if not (force or force_refresh):
            bundle = self.get_stock_bundle(sym)
            if bundle is not None:
                return bundle
        # Ensure master record exists
        if self.get_master(sym) is None:
            from wealth_advisor.data.stocks.universe import load_nse_universe

            uni = {u.symbol: u for u in self.provider.list_universe()}
            if sym not in uni:
                for u in load_nse_universe():
                    if u.symbol == sym:
                        uni[sym] = u
                        break
            if sym in uni:
                self.upsert_master_items([uni[sym]])
            else:
                self.upsert_master_items(
                    [
                        StockMasterItem(
                            symbol=sym,
                            isin=None,
                            name=sym,
                            exchange="NSE",
                            sector="Diversified",
                            industry="Diversified",
                            is_bank="BANK" in sym,
                        )
                    ]
                )
        snap = self.provider.fetch_snapshot(sym)
        if snap is None:
            return self.get_stock_bundle(sym)
        fins = self.provider.fetch_financials(sym)
        prices = self.provider.fetch_prices(sym)
        with self.conn:
            self.upsert_snapshot(snap)
            if fins:
                self.upsert_financials(fins)
            if prices:
                self.upsert_prices(prices)
        self.recompute_sector_medians_and_screens(sync_run_id="on_demand")
        return self.get_stock_bundle(sym)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM stock_sync_run WHERE run_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        failed_list = []
        if row["failed_details_json"]:
            try:
                failed_list = json.loads(row["failed_details_json"])
            except (ValueError, TypeError):
                failed_list = []
        return {
            "job_id": row["run_id"],
            "run_id": row["run_id"],
            "run_date": row["run_date"],
            "mode": row["mode"],
            "data_mode": row["data_mode"],
            "status": row["status"],
            "done": row["processed_symbols"],
            "total": row["total_symbols"],
            "succeeded": row["succeeded_symbols"],
            "failed": row["failed_symbols"],
            "passed_set1": row["set1_pass_count"],
            "passed_set2": row["set2_pass_count"],
            "min_market_cap_cr": row["min_market_cap_cr"],
            "min_traded_value_cr": row["min_traded_value_cr"],
            "failed_details": failed_list,
            "started": row["started_at"],
            "finished": row["completed_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "finished_at": row["completed_at"],
        }

    # ------------------------------------------------------------------
    # Status & Listing APIs
    # ------------------------------------------------------------------
    def get_status(self) -> dict[str, Any]:
        today_str = date.today().isoformat()
        # Latest completed run pointer moves only when a run completes
        last_completed = self.conn.execute(
            "SELECT * FROM stock_sync_run WHERE status = 'completed' ORDER BY completed_at DESC, started_at DESC LIMIT 1"
        ).fetchone()
        running_row = self.conn.execute(
            "SELECT * FROM stock_sync_run WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        last_run = last_completed or running_row

        total_master = self.conn.execute(
            "SELECT COUNT(*) AS c FROM stock_master WHERE is_active = 1"
        ).fetchone()["c"]
        total_synced = self.conn.execute(
            "SELECT COUNT(*) AS c FROM stock_snapshot"
        ).fetchone()["c"]
        set1_pass = self.conn.execute(
            "SELECT COUNT(*) AS c FROM stock_screen_result WHERE rule_set = 'set1' AND passed = 1"
        ).fetchone()["c"]
        set2_pass = self.conn.execute(
            "SELECT COUNT(*) AS c FROM stock_screen_result WHERE rule_set = 'set2' AND passed = 1"
        ).fetchone()["c"]

        sectors_rows = self.conn.execute(
            "SELECT DISTINCT sector FROM stock_master WHERE is_active = 1 AND sector IS NOT NULL ORDER BY sector"
        ).fetchall()
        sectors = [r["sector"] for r in sectors_rows]

        synced_today = False
        is_stale = False
        run_info: dict[str, Any] | None = None
        if last_run is not None:
            synced_today = (
                last_run["run_date"] == today_str and last_run["status"] == "completed"
            )
            try:
                r_dt = date.fromisoformat(str(last_run["run_date"])[:10])
                is_stale = (date.today() - r_dt).days > self.stale_after_days
            except ValueError:
                is_stale = False
            failed_list = []
            if last_run["failed_details_json"]:
                try:
                    failed_list = json.loads(last_run["failed_details_json"])
                except (ValueError, TypeError):
                    failed_list = []
            run_info = {
                "job_id": last_run["run_id"],
                "run_id": last_run["run_id"],
                "run_date": last_run["run_date"],
                "mode": last_run["mode"],
                "data_mode": last_run["data_mode"],
                "status": last_run["status"],
                "total_symbols": last_run["total_symbols"],
                "processed_symbols": last_run["processed_symbols"],
                "succeeded_symbols": last_run["succeeded_symbols"],
                "failed_symbols": last_run["failed_symbols"],
                "set1_pass_count": last_run["set1_pass_count"],
                "set2_pass_count": last_run["set2_pass_count"],
                "min_market_cap_cr": last_run["min_market_cap_cr"],
                "min_traded_value_cr": last_run["min_traded_value_cr"],
                "failed_details": failed_list,
                "started_at": last_run["started_at"],
                "completed_at": last_run["completed_at"],
                "finished_at": last_run["completed_at"],
            }

        active_job = self.get_job(running_row["run_id"]) if running_row is not None else None

        return {
            "status": running_row["status"] if running_row else (run_info["status"] if run_info else "idle"),
            "job_id": (running_row["run_id"] if running_row else (run_info["run_id"] if run_info else None)),
            "done": (running_row["processed_symbols"] if running_row else (run_info["processed_symbols"] if run_info else 0)),
            "total": (running_row["total_symbols"] if running_row else (run_info["total_symbols"] if run_info else total_master)),
            "failed": (running_row["failed_symbols"] if running_row else (run_info["failed_symbols"] if run_info else 0)),
            "passed_set1": set1_pass,
            "passed_set2": set2_pass,
            "started": (running_row["started_at"] if running_row else (run_info["started_at"] if run_info else None)),
            "finished": (running_row["completed_at"] if running_row else (run_info["completed_at"] if run_info else None)),
            "min_market_cap_cr": run_info["min_market_cap_cr"] if run_info else None,
            "min_traded_value_cr": run_info["min_traded_value_cr"] if run_info else None,
            "synced_today": synced_today,
            "has_synced_data": total_synced > 0,
            "is_stale": is_stale,
            "data_mode": self.data_mode,
            "total_universe": total_master,
            "total_synced": total_synced,
            "set1_pass_count": set1_pass,
            "set2_pass_count": set2_pass,
            "sectors": sectors,
            "last_run": run_info,
            "latest_run": run_info,
            "active_job": active_job,
        }

    def list_stocks(
        self,
        *,
        query: str | None = None,
        sector: str | None = None,
        index_tag: str | None = None,
        rule_set: str = "set2",
        passed_only: bool = False,
        is_bank: bool | None = None,
        min_market_cap_cr: float | None = None,
        min_traded_value_cr: float | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List/search synced stocks with Set 1 & Set 2 screening badges and optional filters."""
        rs = rule_set if rule_set in ("set1", "set2") else "set2"
        universe_by_sym = {
            u.symbol: u for u in load_nse_universe(sector=sector, index_tag=index_tag)
        }

        fts_symbols: set[str] | None = None
        if query and query.strip():
            q = query.strip()
            fts_symbols = set()
            # Substring match + FTS match
            like_pat = f"%{q.upper()}%"
            like_rows = self.conn.execute(
                """
                SELECT symbol FROM stock_master
                WHERE is_active = 1
                  AND (UPPER(symbol) LIKE ? OR UPPER(name) LIKE ? OR UPPER(sector) LIKE ? OR UPPER(COALESCE(isin, '')) LIKE ?)
                """,
                (like_pat, like_pat, like_pat, like_pat),
            ).fetchall()
            for r in like_rows:
                fts_symbols.add(r["symbol"])

        rows = self.conn.execute(
            """
            SELECT m.symbol, m.isin, m.name, m.exchange, m.sector, m.industry, m.is_bank,
                   s.as_of_date, s.price, s.market_cap_cr, s.pe, s.pb, s.roe, s.roce,
                   s.debt_to_equity, s.dividend_yield, s.avg_daily_traded_value_cr,
                   s.return_1y, s.volatility_1y, s.max_drawdown_1y, s.source,
                   sr1.passed AS set1_passed, sr1.passed_count AS set1_passed_count,
                   sr1.evaluated_count AS set1_evaluated_count, sr1.composite_score AS set1_score,
                   sr2.passed AS set2_passed, sr2.passed_count AS set2_passed_count,
                   sr2.evaluated_count AS set2_evaluated_count, sr2.composite_score AS set2_score,
                   sr_active.passed AS active_passed, sr_active.passed_count AS active_passed_count,
                   sr_active.evaluated_count AS active_evaluated_count,
                   sr_active.total_criteria AS active_total_criteria,
                   sr_active.pass_ratio AS active_pass_ratio,
                   sr_active.composite_score AS active_composite_score,
                   sr_active.sub_scores_json AS active_sub_scores_json,
                   sr_active.red_flags_json AS active_red_flags_json,
                   sr_active.valuation_json AS active_valuation_json
            FROM stock_master m
            JOIN stock_snapshot s ON s.symbol = m.symbol
            LEFT JOIN stock_screen_result sr1 ON sr1.symbol = m.symbol AND sr1.rule_set = 'set1'
            LEFT JOIN stock_screen_result sr2 ON sr2.symbol = m.symbol AND sr2.rule_set = 'set2'
            LEFT JOIN stock_screen_result sr_active ON sr_active.symbol = m.symbol AND sr_active.rule_set = ?
            WHERE m.is_active = 1
            ORDER BY COALESCE(sr_active.composite_score, 0) DESC, COALESCE(s.market_cap_cr, 0) DESC
            """,
            (rs,),
        ).fetchall()

        items: list[dict[str, Any]] = []
        seen_symbols: set[str] = set()
        for r in rows:
            sym = r["symbol"]
            if index_tag and sym not in universe_by_sym:
                continue
            if fts_symbols is not None and sym not in fts_symbols:
                continue
            if sector and r["sector"] != sector:
                continue
            if is_bank is not None and bool(r["is_bank"]) != is_bank:
                continue
            seen_symbols.add(sym)

            # If custom min_market_cap_cr or min_traded_value_cr is supplied on the fly, re-evaluate dynamically
            passed = bool(r["active_passed"])
            passed_count = int(r["active_passed_count"] or 0)
            evaluated_count = int(r["active_evaluated_count"] or 0)
            total_criteria = int(r["active_total_criteria"] or 0)
            composite_score = float(r["active_composite_score"] or 0.0)
            sub_scores = json.loads(r["active_sub_scores_json"]) if r["active_sub_scores_json"] else {}
            red_flags = json.loads(r["active_red_flags_json"]) if r["active_red_flags_json"] else []
            valuation = json.loads(r["active_valuation_json"]) if r["active_valuation_json"] else {}

            if min_market_cap_cr is not None or min_traded_value_cr is not None:
                bundle = self.get_stock_bundle(sym)
                if bundle is not None:
                    ev = (
                        evaluate_stock_set1(
                            bundle.snapshot,
                            bundle.financials,
                            bundle.sector_median,
                            is_bank=bundle.master.is_bank,
                            min_market_cap_cr=min_market_cap_cr,
                            min_traded_value_cr=min_traded_value_cr,
                        )
                        if rs == "set1"
                        else evaluate_stock_set2(
                            bundle.snapshot,
                            bundle.financials,
                            bundle.sector_median,
                            is_bank=bundle.master.is_bank,
                            min_market_cap_cr=min_market_cap_cr,
                            min_traded_value_cr=min_traded_value_cr,
                        )
                    )
                    passed = ev.passed
                    passed_count = ev.passed_count
                    evaluated_count = ev.evaluated_count
                    total_criteria = ev.total_criteria
                    composite_score = ev.composite_score
                    sub_scores = ev.sub_scores
                    red_flags = ev.red_flags
                    valuation = ev.valuation_summary

            if passed_only and not passed:
                continue

            items.append(
                {
                    "symbol": sym,
                    "isin": r["isin"],
                    "name": r["name"],
                    "exchange": r["exchange"],
                    "sector": r["sector"],
                    "industry": r["industry"],
                    "is_bank": bool(r["is_bank"]),
                    "as_of_date": r["as_of_date"],
                    "price": r["price"],
                    "market_cap_cr": r["market_cap_cr"],
                    "pe": r["pe"],
                    "pb": r["pb"],
                    "roe": r["roe"],
                    "roce": r["roce"],
                    "debt_to_equity": r["debt_to_equity"],
                    "dividend_yield": r["dividend_yield"],
                    "avg_daily_traded_value_cr": r["avg_daily_traded_value_cr"],
                    "return_1y": r["return_1y"],
                    "volatility_1y": r["volatility_1y"],
                    "max_drawdown_1y": r["max_drawdown_1y"],
                    "source": r["source"],
                    "rule_set": rs,
                    "passed": passed,
                    "passed_count": passed_count,
                    "evaluated_count": evaluated_count,
                    "total_criteria": total_criteria,
                    "composite_score": composite_score,
                    "sub_scores": sub_scores,
                    "red_flags": red_flags,
                    "valuation": valuation,
                    "set1_summary": {
                        "passed": bool(r["set1_passed"]),
                        "passed_count": int(r["set1_passed_count"] or 0),
                        "evaluated_count": int(r["set1_evaluated_count"] or 0),
                        "composite_score": float(r["set1_score"] or 0.0),
                    },
                    "set2_summary": {
                        "passed": bool(r["set2_passed"]),
                        "passed_count": int(r["set2_passed_count"] or 0),
                        "evaluated_count": int(r["set2_evaluated_count"] or 0),
                        "composite_score": float(r["set2_score"] or 0.0),
                    },
                }
            )

        # Fallback: include unsynced bundled NSE universe entries when not filtering by passed_only or numeric thresholds
        if not passed_only and min_market_cap_cr is None and min_traded_value_cr is None:
            q_up = query.strip().upper() if query and query.strip() else None
            for u_sym, u in universe_by_sym.items():
                if u_sym in seen_symbols:
                    continue
                if is_bank is not None and u.is_bank != is_bank:
                    continue
                if q_up and not (
                    q_up in u.symbol.upper()
                    or q_up in u.name.upper()
                    or q_up in u.sector.upper()
                    or (u.isin and q_up in u.isin.upper())
                ):
                    continue
                items.append(
                    {
                        "symbol": u.symbol,
                        "isin": u.isin,
                        "name": u.name,
                        "exchange": u.exchange,
                        "sector": u.sector,
                        "industry": u.industry,
                        "is_bank": u.is_bank,
                        "as_of_date": None,
                        "price": None,
                        "market_cap_cr": None,
                        "pe": None,
                        "pb": None,
                        "roe": None,
                        "roce": None,
                        "debt_to_equity": None,
                        "dividend_yield": None,
                        "avg_daily_traded_value_cr": None,
                        "return_1y": None,
                        "volatility_1y": None,
                        "max_drawdown_1y": None,
                        "source": "unsynced_universe",
                        "rule_set": rs,
                        "passed": False,
                        "passed_count": 0,
                        "evaluated_count": 0,
                        "total_criteria": 0,
                        "composite_score": 0.0,
                        "sub_scores": {},
                        "red_flags": [],
                        "valuation": {},
                        "set1_summary": {
                            "passed": False,
                            "passed_count": 0,
                            "evaluated_count": 0,
                            "composite_score": 0.0,
                        },
                        "set2_summary": {
                            "passed": False,
                            "passed_count": 0,
                            "evaluated_count": 0,
                            "composite_score": 0.0,
                        },
                    }
                )

        total = len(items)
        sliced = items[offset : offset + limit]
        return {
            "total": total,
            "rule_set": rs,
            "items": sliced,
        }
