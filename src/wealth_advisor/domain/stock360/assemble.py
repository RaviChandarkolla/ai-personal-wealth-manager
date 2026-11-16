"""Deterministic assembly of the 360° sections from fetched data (no LLM).

Every number is computed here; the LLM only adds commentary later. Anything that is not
available becomes an explicit 'unavailable' / 'requires_live_verification' metric.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any

from ...data.stocks.provider import (
    CorporateAction,
    StockFinancialYear,
    StockOHLCV,
    StockSnapshot,
)
from ...data.web_research import WebFact
from . import fundamentals as fnd
from . import intrinsic as iv
from . import technicals as ta
from . import valuation as val
from .adapters import cycle_position, normalized_eps, pick_adapter
from .checklists import (
    TriggerContext,
    buffett_checklist,
    consensus_action,
    lynch_category,
)
from .cycle_phase import cycle_phase
from .decomposition import decompose_return, mean_reversion_price
from .market import market_metrics, nifty_below_200dma, period_return
from .mechanical import MechInputs, build_mechanical
from .metric import computed
from .models import Metric, Section, SectionTable
from .projections import project
from .targets import long_term_targets, short_term_targets, tranche_plan
from .tax import hypothetical_position, tax_on_sale, wait_for_ltcg
from .template import SECTION_BY_ID


@dataclass
class Params:
    wacc: float = 0.12
    terminal_growth: float = 0.045
    hurdle: float = 0.12
    exit_pe_cap: float = 20.0
    graham_multiplier: float = 22.5
    cash_conversion_flag: float = 0.70
    max_weight_pct: float = 10.0
    stcg_pct: float = 20.0
    ltcg_pct: float = 12.5
    ltcg_exemption: float = 125000.0
    tax_rates_as_of: str = ""


@dataclass
class ReportContext:
    symbol: str
    snapshot: StockSnapshot
    annual: list[StockFinancialYear]
    quarterly: list[StockFinancialYear] = field(default_factory=list)
    bars: list[StockOHLCV] = field(default_factory=list)
    index: list[tuple[str, float]] = field(default_factory=list)
    actions: list[CorporateAction] = field(default_factory=list)
    manual: dict[str, dict[str, Any]] = field(default_factory=dict)
    peers: list[StockSnapshot] = field(default_factory=list)
    web_facts: list[WebFact] = field(default_factory=list)
    holding: dict[str, Any] | None = None  # {"avg_cost": float, "buy_date": "YYYY-MM-DD", "qty": float}
    today: date = field(default_factory=date.today)
    params: Params = field(default_factory=Params)
    data_warnings: list[str] = field(default_factory=list)


@dataclass
class Assembled:
    sections: list[Section]
    mech_inputs: MechInputs
    trigger_ctx: TriggerContext | None
    adapter: str
    cmp: float | None
    mech: Any = None
    score: float = 0.0
    warnings: list[str] = field(default_factory=list)
    values: dict[str, Any] = field(default_factory=dict)  # for charts / the analyst


def _section(sid: str) -> Section:
    sp = SECTION_BY_ID[sid]
    return Section(id=sid, title=sp.title, part=sp.part)  # type: ignore[arg-type]


def finish(sec: Section) -> Section:
    """Set status/missing from the metrics (LLM-only sections stay 'partial' until commentary)."""
    bad = [m for m in sec.metrics if m.status in ("unavailable", "requires_live_verification")]
    sec.missing = [m.label for m in bad]
    if not sec.metrics and not sec.tables:
        sec.status = "partial"
    elif len(bad) == len(sec.metrics) and not sec.tables:
        sec.status = "unavailable"
    else:
        sec.status = "partial" if bad else "complete"
    return sec


def _f(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


class _Builder:
    def __init__(self, ctx: ReportContext) -> None:
        self.c = ctx
        self.sym = ctx.symbol.upper()
        self.p = ctx.params
        self.snap = ctx.snapshot
        self.rows = fnd.sort_fy(ctx.annual)
        self.last = self.rows[-1] if self.rows else None
        self.bars = ctx.bars
        self.closes = [b.close for b in ctx.bars]
        self.warnings: list[str] = list(ctx.data_warnings)
        self.v: dict[str, Any] = {}
        self.adapter = pick_adapter(self.snap.sector, self.snap.industry, self.snap.is_bank)
        self.cmp = self.closes[-1] if self.closes else (self.snap.cmp or self.snap.price)
        self.asof = ctx.bars[-1].date if ctx.bars else (self.snap.as_of_date or str(ctx.today))

    # ------------------------------------------------------------ helpers
    def m(self, key: str, label: str, value: Any, unit: str = "", **kw: Any) -> Metric:
        kw.setdefault("as_of", self.asof)
        return computed(self.sym, key, label, value, unit, **kw)

    def unverified(self, key: str, label: str, why: str = "needs live exchange/filing data") -> Metric:
        return Metric(id=f"{self.sym}:{key}", label=label, value=None,
                      status="requires_live_verification", formula=why)

    def web(self, topic: str, name: str) -> WebFact | None:
        hits = [f for f in self.c.web_facts if f.topic == topic and f.name == name]
        return max(hits, key=lambda f: f.as_of) if hits else None

    def sourced(self, key: str, label: str, unit: str, manual_key: str | None = None,
                topic: str | None = None, name: str | None = None) -> Metric:
        """manual input > web fact (cited, dated) > explicit 'requires live verification'."""
        if manual_key and manual_key in self.c.manual:
            mi = self.c.manual[manual_key]
            val_ = mi["value"]
            return Metric(id=f"{self.sym}:{key}", label=label, value=val_ if isinstance(val_, (int, float, str)) else str(val_),
                          unit=unit, status="manual", as_of=date.fromisoformat(mi["as_of"][:10]),
                          source="manual", url=mi.get("source_url", ""))
        if topic and name:
            wf = self.web(topic, name)
            if wf is not None:
                return Metric(id=f"{self.sym}:{key}", label=label, value=wf.value, unit=wf.unit or unit,
                              status="verified", as_of=wf.as_of, source=f"web:{wf.publisher or 'search'}",
                              url=wf.source_url)
        return self.unverified(key, label)

    # ------------------------------------------------------------ build
    def build(self) -> Assembled:
        self._prepare()
        secs = [self.step0(), self.s1(), self.s2(), self.s3(), self.s4(), self.s5(), self.s6(), self.s7(),
                self.s8(), self.s9(), self.s10(), self.s11(), self.s12(), self.s13(), self.s14(),
                self.s15(), self.s16(), self.s17()]
        for s in secs:
            finish(s)
        self.v["charts"] = self._charts(secs)
        return Assembled(sections=secs, mech_inputs=self.mi, trigger_ctx=self.tctx, adapter=self.adapter,
                         cmp=self.cmp, mech=self.mech, score=self.mech.score, warnings=self.warnings, values=self.v)

    def _charts(self, secs: list[Section]) -> dict[str, Any]:
        """Structured, JSON-light chart data (no fabricated values; missing pieces are omitted)."""
        from . import technicals as _ta

        out: dict[str, Any] = {}
        n = 120
        bars = self.bars[-n:]
        if len(bars) >= 20:
            cl = self.closes
            ser: dict[str, list[float | None]] = {}
            for k in (20, 50, 200):
                es = _ta.ema_series(cl, k)
                full: list[float | None] = [None] * (k - 1) + [round(x, 2) for x in es] if es else [None] * len(cl)
                ser[f"ema{k}"] = full[-len(bars):]
            out["price"] = {
                "dates": [str(b.date) for b in bars], "close": [round(b.close, 2) for b in bars], **ser,
                "levels": {k: round(v, 2) for k, v in self.levels.items() if v},
                "cmp": _f(self.cmp),
            }
        pes = [(str(d), round(v, 2)) for d, v in self.hist["pe"] if v is not None]
        if pes:
            vals = [v for _, v in pes]
            out["pe_band"] = {
                "points": [{"period": d, "pe": v} for d, v in pes], "median": _f(self.med_pe),
                "low": min(vals), "high": max(vals), "current": _f(self.pe),
            }
        dec = self.v.get("decomposition")
        if dec:
            out["decomposition"] = {k: _f(dec.get(k)) for k in ("from_eps_pct", "from_rerating_pct") if dec.get(k) is not None}
        cons: dict[str, Any] = {}
        for sec in secs:
            if sec.id in ("s8", "s11"):
                for m in sec.metrics:
                    if isinstance(m.value, (int, float)) and m.status in ("verified", "computed", "estimated", "manual"):
                        cons[m.id.rsplit(":", 1)[-1]] = m.value
        keep = {k: cons[k] for k in ("buy_count", "hold_count", "sell_count", "target_price_mean") if k in cons}
        if keep:
            keep["cmp"] = _f(self.cmp)
            out["consensus"] = keep
        return out

    # ------------------------------------------------------------ shared numbers
    def _prepare(self) -> None:
        from ...data.stocks.report_data import ttm

        p, r, last, sn, cmp_ = self.p, self.rows, self.last, self.snap, self.cmp
        q_eps, _ = ttm(self.c.quarterly, "eps")
        self.eps_ttm = q_eps if q_eps is not None else sn.eps_ttm
        self.eps_src = "sum of last 4 quarters" if q_eps is not None else "Yahoo snapshot EPS (TTM)"
        self.pe = val.pe(cmp_, self.eps_ttm)
        self.eps_cagr = fnd.series_cagr(r, "eps", 3)
        self.rev_cagr = fnd.series_cagr(r, "revenue_cr", 3)
        self.sust = val.sustainable_growth_pct(r, self.eps_cagr)
        self.peg = val.peg(self.pe, self.sust)
        self.shares = sn.shares_outstanding
        self.net_debt = fnd.net_debt(last) if last else None
        self.mcap = sn.market_cap_cr or (cmp_ * self.shares if cmp_ and self.shares else None)
        self.bvps = (last.bvps if last and last.bvps else None) or sn.book_value_per_share

        # technicals
        c = self.closes
        hi = [b.high for b in self.bars]
        lo = [b.low for b in self.bars]
        vol = [b.volume or 0.0 for b in self.bars]
        self.t = {
            "sma20": ta.sma(c, 20), "sma50": ta.sma(c, 50), "sma100": ta.sma(c, 100), "sma200": ta.sma(c, 200),
            "ema50": ta.ema(c, 50), "ema200": ta.ema(c, 200), "rsi": ta.rsi(c),
            "macd": ta.macd(c), "atr": ta.atr(hi, lo, c) if len(c) > 15 else None,
            "adx": ta.adx(hi, lo, c) if len(c) > 29 else None,
            "stage": ta.weinstein_stage(c), "cross": ta.cross_state(c),
        }
        self.pivots = ta.floor_pivots(hi[-2], lo[-2], c[-2]) if len(c) >= 2 else {}
        yr = self.bars[-252:]
        self.hi52 = max((b.high for b in yr), default=None)
        self.lo52 = min((b.low for b in yr), default=None)
        self.levels: dict[str, float] = {k: v for k, v in self.pivots.items() if k != "P"}
        if self.hi52:
            self.levels["52W high"] = self.hi52
        if self.lo52:
            self.levels["52W low"] = self.lo52
        for k in ("sma50", "sma200"):
            if self.t[k]:
                self.levels[k.upper()] = self.t[k]

        # intrinsic value
        g_dcf = min(max(self.rev_cagr or 0.0, 0.0), 25.0) / 100
        fcf0 = last.fcf_cr if last else None
        self.dcf_growth = g_dcf
        dcf_ps = rdcf = None
        if self.adapter != "bfsi" and fcf0 and fcf0 > 0 and self.shares:
            eq = iv.dcf_equity_value(fcf0, g_dcf, p.wacc, p.terminal_growth, 5, self.net_debt or 0.0)
            dcf_ps = eq / self.shares if eq else None
            if self.mcap:
                rdcf = iv.reverse_dcf_growth(self.mcap, fcf0, self.net_debt or 0.0, p.wacc, p.terminal_growth)
        self.dcf_ps, self.rdcf = dcf_ps, rdcf
        lyn = iv.lynch_fair_values(self.eps_ttm, self.sust)
        self.lynch = lyn
        self.graham = iv.graham_number(self.eps_ttm, self.bvps, p.graham_multiplier)
        self.iv_values = {"dcf": dcf_ps, "lynch_peg1": lyn.get(1.0) if lyn else None, "graham": self.graham}
        self.comp_iv = iv.composite_iv(self.iv_values)
        self.over = val.overvaluation_verdict(cmp_, self.comp_iv) if cmp_ else ("Not available", None, None)

        # own history multiples & peers
        prices = [(b.date, b.close) for b in self.bars]
        hist = val.own_history_multiples(r, prices)
        self.hist = hist
        self.med_pe, self.med_pe_note = val.history_median(hist["pe"])
        self.med_pb, _ = val.history_median(hist["pb"])
        _, self.peer_med = val.peer_table(self.sym, self.c.peers)

        # projections
        cyc = self.adapter == "cyclical"
        self.proj = project(r, 5, mid_cycle=cyc)
        self.cycle = cycle_position(r)
        eps3 = None
        if self.proj and len(self.proj.rows) >= 3:
            eps3 = self.proj.rows[2].eps
        rs3 = None
        if len(c) > 63 and self.c.index:
            from .market import align

            sa, ia = align(self.bars, self.c.index)
            a, b = period_return(sa, 63), period_return(ia, 63)
            rs3 = None if a is None or b is None else a - b

        # mechanical verdict
        roes = [fnd.roe_pct(x) for x in r]
        d_e = (last.total_debt_cr / last.net_worth_cr
               if last and last.total_debt_cr is not None and last.net_worth_cr else None)
        icov = sn.interest_coverage
        if icov is None and last and last.ebit_cr is not None and (last.interest_cr or last.interest_expense_cr):
            icov = last.ebit_cr / (last.interest_cr or last.interest_expense_cr)  # type: ignore[operator]
        self.mi = MechInputs(
            roe_pct=fnd.roe_pct(last) if last else None, roce_pct=fnd.roce_pct(last) if last else None,
            roe_years_above_15=None if not roes or any(x is None for x in roes) else all(x > 15 for x in roes),  # type: ignore[operator]
            cash_conversion=fnd.cash_conversion_ratio(r),
            other_income_pct_pbt=fnd.other_income_pct_pbt(last) if last else None,
            premium_to_iv_pct=self.over[1], peg=self.peg, rev_cagr_pct=self.rev_cagr, eps_cagr_pct=self.eps_cagr,
            debt_to_equity=d_e, net_debt_to_ebitda=fnd.net_debt_to_ebitda(last) if last else None,
            interest_coverage=icov, weinstein_stage=self.t["stage"],
            above_200dma=None if not self.t["sma200"] or not cmp_ else cmp_ > self.t["sma200"],
            rsi=self.t["rsi"], is_bank=self.adapter == "bfsi",
        )
        self.tctx = (TriggerContext(
            cmp=cmp_, rows=r, composite_iv=self.comp_iv, graham_number=self.graham, peg=self.peg,
            cyclical=cyc, cycle=self.cycle[0], is_bank=self.adapter == "bfsi", closes=c, volumes=vol,
            nifty_below_200dma=nifty_below_200dma(self.c.index) if self.c.index else None, rs_3m=rs3,
            eps_3y_projected=eps3, median_pe_5y=self.med_pe) if cmp_ else None)
        self.mech = build_mechanical(self.mi, self.tctx)

    # ------------------------------------------------------------ Part 1
    def step0(self) -> Section:
        s = _section("step0")
        rows: list[list[Any]] = []

        def add(item: str, d: str | None, src: str) -> None:
            age = (self.c.today - date.fromisoformat(d[:10])).days if d else None
            rows.append([item, d or "Not available", src, age])

        add("Daily prices (OHLCV)", self.bars[-1].date if self.bars else None, "yahoo_chart")
        add("Snapshot / ratios", self.snap.as_of_date or self.snap.as_of or None, "yahoo")
        add("Latest annual financials", self.last.period_end if self.last else None, "yahoo_timeseries")
        q = fnd.sort_fy(self.c.quarterly)
        add("Latest quarterly results", q[-1].period_end if q else None, "yahoo_timeseries")
        add("Nifty 50 index", self.c.index[-1][0] if self.c.index else None, "yahoo_chart")
        for k, mi in self.c.manual.items():
            add(f"Manual input: {k}", mi["as_of"], "manual")
        for f in self.c.web_facts[:12]:
            add(f"Web: {f.topic}/{f.name}", str(f.as_of), f"web:{f.publisher or f.source_url[:40]}")
        s.tables.append(SectionTable(title="Data freshness audit",
                                     columns=["Item", "As of", "Source", "Age (days)"], rows=rows))
        splits = [a for a in self.c.actions if a.type in ("split", "bonus")
                  and a.date >= str(self.c.today - timedelta(days=730))]
        s.metrics.append(self.m("step0:recent_splits", "Splits/bonuses in last 2 years", len(splits), "",
                                formula="count of yahoo corporate actions of type split/bonus"))
        if splits:
            s.tables.append(SectionTable(
                title="Corporate actions (prices are split-adjusted)", columns=["Date", "Type", "Detail"],
                rows=[[a.date, a.type, a.detail or str(a.value)] for a in splits]))
        for t in ("corporate_action",):
            ev = [f for f in self.c.web_facts if f.topic == t]
            if not ev:
                s.metrics.append(self.unverified("step0:demerger_check", "Demerger / merger check",
                                                 "no web evidence retrieved"))
        s.metrics.append(self.m("step0:mode", "Data mode", self.snap.source if hasattr(self.snap, "source") else "n/a",
                                status="computed"))
        return s

    def s1(self) -> Section:
        s = _section("s1")
        sn = self.snap
        s.metrics += market_metrics(self.sym, self.bars, self.c.index, self.mcap, sn.enterprise_value_cr)
        s.metrics += [
            Metric(id=f"{self.sym}:s1:sector", label="Sector", value=sn.sector, status="verified", source="yahoo"),
            Metric(id=f"{self.sym}:s1:industry", label="Industry", value=sn.industry, status="verified", source="yahoo"),
            self.unverified("s1:index_membership", "Index membership (Nifty 50/Next 50/Midcap)",
                            "requires NSE index constituent lists"),
        ]
        promoter = self.sourced("s1:promoter_pct", "Promoter holding", "%", "promoter_pct", "shareholding", "promoter_pct")
        if promoter.status == "requires_live_verification" and sn.promoter_holding_pct is not None:
            promoter = self.m("s1:promoter_pct", "Promoter holding", sn.promoter_holding_pct, "%",
                              status="verified", source="yahoo_snapshot")
        pledge = self.sourced("s1:pledge_pct", "Promoter pledge", "%", "pledge_pct", "shareholding", "pledge_pct")
        if pledge.status == "requires_live_verification" and sn.pledge_pct is not None:
            pledge = self.m("s1:pledge_pct", "Promoter pledge", sn.pledge_pct, "%", status="verified",
                            source="yahoo_snapshot")
        s.metrics += [promoter, pledge,
                      self.sourced("s1:fii_pct", "FII holding", "%", "fii_pct", "shareholding", "fii_pct"),
                      self.sourced("s1:dii_pct", "DII holding", "%", "dii_pct", "shareholding", "dii_pct"),
                      self.sourced("s1:public_pct", "Public holding", "%", "public_pct", "shareholding", "public_pct"),
                      self.unverified("s1:delivery_pct", "Delivery volume %", "requires NSE bhavcopy")]
        hist = self.c.manual.get("shareholding_history")
        if hist and isinstance(hist["value"], list):
            rows = [[h.get("quarter"), h.get("promoter_pct"), h.get("fii_pct"), h.get("dii_pct"), h.get("public_pct"),
                     h.get("pledge_pct")] for h in hist["value"] if isinstance(h, dict)]
            if rows:
                s.tables.append(SectionTable(
                    title=f"Shareholding by quarter % (manual, as of {hist['as_of'][:10]})",
                    columns=["Quarter", "Promoter", "FII", "DII", "Public", "Pledge"], rows=rows))
        return s

    def s2(self) -> Section:
        s = _section("s2")
        r = self.rows
        if r:
            s.tables.append(SectionTable(
                title="Income statement trend (₹ Cr)",
                columns=["FY", "Revenue", "OPM %", "PAT", "EPS", "Other inc % PBT"],
                rows=[[x.period_end[:4], x.revenue_cr, _f(fnd.opm_pct(x)), x.pat_cr, x.eps,
                       _f(fnd.other_income_pct_pbt(x))] for x in r]))
            s.tables.append(SectionTable(
                title="Balance sheet & cash flow trend (₹ Cr)",
                columns=["FY", "Net worth", "Debt", "Cash", "CFO", "FCF", "ROE %", "ROCE %"],
                rows=[[x.period_end[:4], x.net_worth_cr, x.total_debt_cr, x.cash_and_equivalents_cr, x.cfo_cr,
                       x.fcf_cr, _f(fnd.roe_pct(x)), _f(fnd.roce_pct(x))] for x in r]))
        last = self.last
        mk = self.m
        s.metrics += [
            mk("s2:rev_cagr", "Revenue CAGR (3Y)", self.rev_cagr, "%", formula="(rev_t/rev_t-3)^(1/3)-1",
               as_of=last.period_end if last else None),
            mk("s2:eps_cagr", "EPS CAGR (3Y)", self.eps_cagr, "%", formula="(eps_t/eps_t-3)^(1/3)-1"),
            mk("s2:other_income_pct_pbt", "Other income % of PBT", fnd.other_income_pct_pbt(last) if last else None, "%",
               formula="other income / PBT"),
            mk("s2:cash_conversion", "Cash conversion (CFO/PAT, 3Y)", fnd.cash_conversion_ratio(r), "x",
               formula="sum(CFO)/sum(PAT) over 3 years"),
            mk("s2:roe", "ROE", fnd.roe_pct(last) if last else None, "%", formula="PAT / net worth"),
            mk("s2:roce", "ROCE", fnd.roce_pct(last) if last else None, "%", formula="EBIT / capital employed"),
            mk("s2:roic", "ROIC", fnd.roic_pct(last) if last else None, "%", formula="NOPAT / invested capital"),
            mk("s2:net_debt_ebitda", "Net debt / EBITDA", fnd.net_debt_to_ebitda(last) if last else None, "x"),
            mk("s2:quick_ratio", "Quick ratio", fnd.quick_ratio(last) if last else None, "x"),
            mk("s2:ccc", "Cash conversion cycle", fnd.cash_conversion_cycle(last) if last else None, "days"),
            mk("s2:interest_coverage", "Interest coverage", self.mi.interest_coverage, "x"),
            self.sourced("s2:order_book", "Order book (₹ Cr)", "₹ Cr", "order_book_cr", "concall", "order_book_cr"),
            self.sourced("s2:contingent", "Contingent liabilities (₹ Cr)", "₹ Cr", "contingent_liabilities_cr"),
            self.sourced("s2:goodwill", "Goodwill (₹ Cr)", "₹ Cr", "goodwill_cr"),
        ]
        if self.adapter == "bfsi" and last:
            for k, lab, v in (("nim", "NIM %", last.nim_pct), ("gnpa", "GNPA %", last.gnpa_pct),
                              ("nnpa", "NNPA %", last.nnpa_pct), ("casa", "CASA %", last.casa_pct),
                              ("car", "CAR %", last.car_pct)):
                s.metrics.append(mk(f"s2:{k}", lab, v, "%", source="yahoo_timeseries"))
        return s

    # ------------------------------------------------------------ Part 2
    def s3(self) -> Section:
        s = _section("s3")
        sn, mk = self.snap, self.m
        pe_ = self.pe
        s.metrics += [
            mk("s3:pe_ttm", "P/E (TTM)", pe_, "x", formula=f"CMP / EPS ({self.eps_src})"),
            mk("s3:pb", "P/B", val.pb(self.cmp, self.bvps), "x", formula="CMP / BVPS"),
            mk("s3:ps", "P/S", val.ps(self.mcap, self.last.revenue_cr if self.last else None), "x"),
            mk("s3:ev_ebitda", "EV/EBITDA", val.ev_ebitda(sn.enterprise_value_cr, self.last.ebitda_cr if self.last else None), "x"),
            mk("s3:div_yield", "Dividend yield", val.dividend_yield_pct(self.last.dps if self.last else None, self.cmp), "%"),
            mk("s3:peg", "PEG", self.peg, "", formula="P/E / sustainable growth %"),
            mk("s3:sustainable_growth", "Sustainable growth", self.sust, "%"),
            mk("s3:median_pe", "Own median P/E", self.med_pe, "x", formula=self.med_pe_note),
            mk("s3:median_pb", "Own median P/B", self.med_pb, "x"),
            mk("s3:pe_vs_own", "P/E vs own median", val.relative_to(pe_, self.med_pe), "%"),
            mk("s3:pe_vs_peers", "P/E vs sector peer median", val.relative_to(pe_, self.peer_med.get("pe")), "%"),
            mk("s3:cycle_position", "Earnings-cycle position", f"{self.cycle[0]}", "",
               formula="latest OPM percentile within available years"),
            mk("s3:overvaluation", "Valuation verdict", self.over[0], "", formula="CMP vs composite IV, band ±15%"),
            mk("s3:premium_to_iv", "CMP premium/(discount) to IV", _f(self.over[1]), "%"),
        ]
        top, _ = val.peer_table(self.sym, self.c.peers)
        if top:
            s.tables.append(SectionTable(
                title="Top peers by market cap", columns=["Symbol", "P/E", "P/B", "ROE %", "Mkt cap ₹ Cr"],
                rows=[[p.symbol, p.pe, p.pb, _f((p.roe or 0) * 100) if p.roe is not None else None,
                       p.market_cap_cr] for p in top]))
        return s

    def s4(self) -> Section:
        s = _section("s4")
        mk, p = self.m, self.p
        na = "not_applicable"
        bfsi = self.adapter == "bfsi"
        s.metrics += [
            mk("s4:reverse_dcf", "Reverse DCF implied 10Y growth",
               None if self.rdcf is None else self.rdcf * 100, "%",
               formula=f"growth g such that DCF(FCF, g, WACC {p.wacc:.0%}, terminal {p.terminal_growth:.1%}) = market EV",
               status=na if bfsi else None),
            mk("s4:dcf", "DCF value per share", self.dcf_ps, "₹",
               formula=f"5Y fading-growth DCF, WACC {p.wacc:.0%}, terminal {p.terminal_growth:.1%}, growth {self.dcf_growth:.1%}",
               status=na if bfsi else None),
            mk("s4:lynch_fair", "Lynch fair value (PEG 1.0)", self.iv_values["lynch_peg1"], "₹",
               formula="EPS × sustainable growth % / 1.0"),
            mk("s4:graham", "Graham number", self.graham, "₹", formula=f"sqrt({p.graham_multiplier} × EPS × BVPS)"),
            mk("s4:composite_iv", "Composite intrinsic value", self.comp_iv, "₹",
               formula="median of available methods (DCF, Lynch, Graham)"),
        ]
        if self.rdcf is not None:
            s.metrics.append(mk("s4:reverse_dcf_label", "Reverse DCF assessment",
                                iv.reverse_dcf_label(self.rdcf, (self.rev_cagr or 0) / 100 if self.rev_cagr else None)))
        s.tables.append(SectionTable(
            title="Intrinsic value methods vs CMP", columns=["Method", "Value ₹", "CMP premium %"],
            rows=[[k, _f(v), _f(iv.premium_pct(self.cmp, v)) if self.cmp else None]
                  for k, v in self.iv_values.items()] + [["composite", _f(self.comp_iv),
                                                         _f(iv.premium_pct(self.cmp, self.comp_iv)) if self.cmp else None]]))
        return s

    def s5(self) -> Section:
        s = _section("s5")
        mk = self.m
        dec = None
        base_px = None
        if self.last and self.bars:
            base_px = val.price_on_or_before([(b.date, b.close) for b in self.bars], self.last.period_end)
        if base_px and self.cmp and self.last:
            dec = decompose_return(base_px, self.cmp, self.last.eps, self.eps_ttm)
        ret = None if not base_px or not self.cmp else (self.cmp / base_px - 1) * 100
        s.metrics.append(mk("s5:price_return", "Price return since last FY end", ret, "%",
                            formula="CMP / close at FY end - 1", as_of=self.asof))
        for k, lab in (("from_eps_pct", "from EPS growth"), ("from_rerating_pct", "from P/E re-rating")):
            s.metrics.append(mk(f"s5:{k}", f"Return {lab}", None if not dec else dec.get(k), "pp",
                                formula="log-split of (1+EPS growth)(1+P/E change)-1"))
        s.metrics.append(mk("s5:mean_reversion_price", "Price at own median P/E",
                            mean_reversion_price(self.eps_ttm, self.med_pe), "₹", formula="EPS TTM × own median P/E"))
        self.v["decomposition"] = dec
        return s

    def s6(self) -> Section:
        return _section("s6")

    def s7(self) -> Section:
        s = _section("s7")
        s.metrics += [
            self.m("s7:auditor_flags", "Auditor flags (Yahoo)", self.snap.auditor_flags, "", status="verified",
                   source="yahoo_snapshot"),
            self.sourced("s7:governance_events", "Governance events (SEBI/auditor/RPT)", "", None, "governance", "events"),
        ]
        return s

    # ------------------------------------------------------------ Part 3
    def s8(self) -> Section:
        s = _section("s8")
        if self.proj:
            s.tables.append(SectionTable(
                title="Base-case projections (mechanical, constant margins)",
                columns=["Year", "Revenue ₹ Cr", "OPM %", "EBITDA ₹ Cr", "PAT ₹ Cr", "EPS ₹"],
                rows=[[x.year_label, x.revenue_cr, x.opm_pct, x.ebitda_cr, x.pat_cr, x.eps] for x in self.proj.rows[:3]]))
            for k, v in self.proj.assumptions.items():
                s.metrics.append(self.m(f"s8:assump_{k}", f"Assumption: {k}", v, "", formula="projection assumption",
                                        status="estimated"))
        else:
            s.metrics.append(self.m("s8:projection", "Projections", None, ""))
        s.metrics.append(self.sourced("s8:consensus_eps_fy1", "Consensus EPS FY+1 (not mixed with ours)", "₹", "consensus_eps_fy1",
                                      "analyst", "consensus_eps_fy1"))
        s.metrics.append(self.sourced("s8:consensus_eps_fy2", "Consensus EPS FY+2 (not mixed with ours)", "₹", "consensus_eps_fy2",
                                      "analyst", "consensus_eps_fy2"))
        s.metrics.append(self.sourced("s8:industry_cagr", "Industry CAGR", "%", "industry_cagr_pct", "industry", "industry_cagr_pct"))
        s.metrics.append(self.sourced("s8:tam", "Addressable market (₹ Cr)", "₹ Cr", "tam_cr", "industry", "tam_cr"))
        s.metrics.append(self.sourced("s8:guidance", "Management guidance", "", "guidance", "concall", "guidance"))
        return s

    def s9(self) -> Section:
        s = _section("s9")
        mk = self.m
        beta = next((x.value for x in market_metrics(self.sym, self.bars, self.c.index) if x.id.endswith("s1:beta")), None)
        s.metrics += [
            mk("s9:beta", "Beta (1Y vs Nifty 50)", beta, "", formula="cov/var"),
            mk("s9:debt_equity", "Debt / equity", self.mi.debt_to_equity, "x"),
            mk("s9:atr_pct", "ATR as % of price", None if not self.t["atr"] or not self.cmp else self.t["atr"] / self.cmp * 100,
               "%", formula="ATR(14) / CMP"),
            mk("s9:drawdown", "Drawdown from 52W high", None if not self.hi52 or not self.cmp else (self.cmp / self.hi52 - 1) * 100,
               "%"),
        ]
        return s

    def s10(self) -> Section:
        s = _section("s10")
        mk, t, cmp_ = self.m, self.t, self.cmp
        for k in ("sma20", "sma50", "sma100", "sma200", "ema50", "ema200"):
            s.metrics.append(mk(f"s10:{k}", k.upper(), _f(t[k]), "₹", formula=f"{k[:3].upper()} {k[3:]} closes"))
        macd = t["macd"]
        s.metrics += [
            mk("s10:rsi", "RSI (14)", _f(t["rsi"]), "", formula="Wilder RSI"),
            mk("s10:macd", "MACD line", _f(macd[0]) if macd else None, "", formula="EMA12-EMA26"),
            mk("s10:macd_signal", "MACD signal", _f(macd[1]) if macd else None),
            mk("s10:macd_hist", "MACD histogram", _f(macd[2]) if macd else None),
            mk("s10:atr", "ATR (14)", _f(t["atr"]), "₹", formula="Wilder ATR"),
            mk("s10:adx", "ADX (14)", _f(t["adx"][0]) if t["adx"] else None),
            mk("s10:stage", "Weinstein stage", t["stage"], "", formula="30-week MA slope and price position"),
            mk("s10:cross", "50/200 SMA cross", f"{t['cross'][0]}" + (f" ({t['cross'][1]} bars ago)" if t["cross"][1] is not None else ""),
               ""),
            mk("s10:price_vs_200", "Price vs SMA200", None if not t["sma200"] or not cmp_ else (cmp_ / t["sma200"] - 1) * 100, "%"),
        ]
        if len(self.bars) > 21:
            vols = [b.volume or 0.0 for b in self.bars]
            avg20 = sum(vols[-20:]) / 20
            up, dn = ta.up_down_volume(self.closes, vols)
            s.metrics += [mk("s10:vol_vs_avg", "Latest volume vs 20D avg", _f(vols[-1] / avg20) if avg20 else None, "x"),
                          mk("s10:updown_vol", "Up/down volume ratio (20D)", _f(up / dn) if dn else None, "x")]
        s.tables.append(SectionTable(
            title="Support & resistance (floor pivots, 52W, SMAs)", columns=["Level", "Price ₹"],
            rows=[[k, _f(v)] for k, v in sorted(self.levels.items(), key=lambda kv: -kv[1])]))
        s.metrics.append(self.unverified("s10:chart_patterns", "Chart patterns", "requires visual/web analysis"))
        return s

    def s11(self) -> Section:
        s = _section("s11")
        for k, lab in (("buy_count", "Analysts: Buy"), ("hold_count", "Analysts: Hold"), ("sell_count", "Analysts: Sell"),
                       ("target_price_mean", "Mean analyst target ₹")):
            s.metrics.append(self.sourced(f"s11:{k}", lab, "", k, "analyst", k))
        s.metrics += [self.unverified("s11:fii_dii_flows", "FII/DII flows (latest)", "requires NSDL/NSE daily flows"),
                      self.unverified("s11:block_deals", "Block / bulk deals (60 days)", "requires exchange disclosures")]
        return s

    # ------------------------------------------------------------ Part 4
    def s12(self) -> Section:
        s = _section("s12")
        p, mk = self.p, self.m
        mos = self.over[2]
        items = buffett_checklist(self.rows, self.eps_cagr, mos)
        s.tables.append(SectionTable(title="Mechanical checklist (moat & management are judgement items)",
                                     columns=["Item", "Pass"],
                                     rows=[[a, "Not evaluable" if b is None else ("Yes" if b else "No")] for a, b in items]))
        eps5 = self.proj.rows[4].eps if self.proj and len(self.proj.rows) >= 5 else None
        exit_pe = min(self.med_pe, p.exit_pe_cap) if self.med_pe else None
        self.buffett_entry = iv.buffett_entry(eps5, exit_pe, p.hurdle, 5)
        s.metrics.append(mk("s12:entry_price", "Buffett entry price", self.buffett_entry, "₹",
                            formula=f"EPS(FY+5) × min(median P/E, {p.exit_pe_cap:g}) / (1+{p.hurdle:.0%})^5"))
        return s

    def s13(self) -> Section:
        s = _section("s13")
        cat = lynch_category(self.sust, self.adapter == "cyclical", self.rows)
        s.metrics.append(self.m("s13:category", "Lynch category", cat, "", formula="growth bands + sector/turnaround rules"))
        for peg_, price in (self.lynch or {}).items():
            s.metrics.append(self.m(f"s13:peg_{peg_:g}", f"Price at PEG {peg_:g}", price, "₹", formula="EPS × growth% / PEG"))
        if not self.lynch:
            s.metrics.append(self.m("s13:peg_entries", "PEG entry prices", None, "₹"))
        return s

    def s14(self) -> Section:
        s = _section("s14")
        mk, cmp_ = self.m, self.cmp
        if cmp_:
            st = short_term_targets(cmp_, self.t["atr"], self.levels)
            if st:
                s.tables.append(SectionTable(title="Short-term targets (ATR-based, snapped to S/R)",
                                             columns=["Horizon", "Bear ₹", "Base ₹", "Bull ₹", "Snapped to"],
                                             rows=[[h] + [next(x.price for x in st if x.horizon == h and x.scenario == sc)
                                                          for sc in ("bear", "base", "bull")]
                                                   + [", ".join(x.level for x in st if x.horizon == h and x.level)]
                                                   for h in dict.fromkeys(x.horizon for x in st)]))
            else:
                s.metrics.append(mk("s14:short_term", "Short-term targets", None, "₹"))
        eps_by = {}
        if self.proj:
            for y in (1, 2, 3, 5):
                eps_by[y] = self.proj.rows[y - 1].eps if len(self.proj.rows) >= y else None
        pes = [v for _, v in self.hist["pe"] if v is not None]
        lt = long_term_targets(eps_by, self.med_pe, min(pes) if pes else None, self.peer_med.get("pe"))
        s.tables.append(SectionTable(title="Long-term targets (EPS × P/E)", columns=["Years", "Bear ₹", "Base ₹", "Bull ₹"],
                                     rows=[[y, d["bear"], d["base"], d["bull"]] for y, d in lt.items()]))
        base1 = (lt.get(1) or {}).get("base")
        supports = [v for k, v in self.levels.items() if v and cmp_ and v < cmp_]
        swing = min((b.low for b in self.bars[-60:]), default=None)
        plan = tranche_plan(cmp_, self.t["atr"], supports, swing, self.t["sma200"],
                            getattr(self, "buffett_entry", None), base1, self.mech.score, self.p.max_weight_pct) if cmp_ else None
        if plan:
            s.tables.append(SectionTable(
                title="Entry plan", columns=["Item", "Value"],
                rows=[["Tranche prices ₹", ", ".join(str(x) for x in plan["tranches"])],  # type: ignore[attr-defined]
                      ["Stop ₹", plan["stop"]], ["Stop %", plan["stop_pct"]],
                      ["R:R at CMP", plan.get("rr_at_cmp")], ["R:R at tranche 1", plan.get("rr_at_tranche1")],
                      [f"Position size % (cap {self.p.max_weight_pct:g})", plan["position_pct"]]]))
        else:
            s.metrics.append(mk("s14:entry_plan", "Entry plan", None, ""))
        self.v["targets_lt"] = lt
        return s

    def s15(self) -> Section:
        s = _section("s15")
        s.metrics += [
            self.m("s15:score", "Quant score (0-100)", self.mech.score, "", formula="weighted pillars; missing pillars excluded"),
            self.m("s15:stars", "Stars (out of 5)", self.mech.stars, "", formula="score / 20"),
            self.m("s15:mech_action", "Mechanical action", self.mech.action, "", formula="score bands + legend sell signals"),
        ]
        return s

    def s16(self) -> Section:
        s = _section("s16")
        pe_hist = [v for _, v in self.hist["pe"] if v is not None]
        from .cycle_phase import pct_rank

        pr = pct_rank(self.pe, pe_hist) if len(pe_hist) >= 3 else None
        low3 = min(self.closes[-756:]) if len(self.closes) > 100 else None
        from_low = None if not low3 or not self.cmp else (self.cmp / low3 - 1) * 100
        ph = cycle_phase(self.t["stage"], pr, from_low, None)
        for k, v in ph.items():
            s.metrics.append(self.m(f"s16:{k}", f"Cycle phase {k}", v, "", formula="stage + P/E percentile + return from low",
                                    status="estimated"))
        s.metrics.append(self.unverified("s16:inst_flows", "Institutional vs public holding change",
                                         "requires 2+ quarters of shareholding"))
        return s

    def s17(self) -> Section:
        s = _section("s17")
        m = self.mech
        s.tables.append(SectionTable(
            title="Legend sell triggers (D = data rule, J = judgement, W = web)",
            columns=["Legend", "Trigger", "Kind", "Fired", "Detail"],
            rows=[[t.legend, t.trigger, t.kind, "Not evaluable" if t.fired is None else ("Yes" if t.fired else "No"), t.detail]
                  for t in m.triggers]))
        s.metrics += [
            self.m("s17:sell_signals", "Legends signalling SELL/REDUCE", m.sell_signals, "", formula="count of legends with SELL or REDUCE"),
            self.m("s17:evaluable", "Evaluable legends", m.evaluable_legends, ""),
            self.m("s17:consensus", "Consensus action", consensus_action(m.sell_signals), ""),
        ]
        cmp_, hp, today = self.cmp, self.c.holding, self.c.today
        rates = {"stcg_pct": self.p.stcg_pct, "ltcg_pct": self.p.ltcg_pct, "exemption": self.p.ltcg_exemption}
        tax: dict[str, Any] | None = None
        if cmp_ and hp and hp.get("avg_cost") and hp.get("buy_date"):
            bd = date.fromisoformat(str(hp["buy_date"])[:10])
            qty = float(hp.get("qty") or 1)
            tax = dict(tax_on_sale(float(hp["avg_cost"]), cmp_, qty, bd, today, **rates))
            tax.update({f"wait_{k}": v for k, v in wait_for_ltcg(float(hp["avg_cost"]) * qty, cmp_ * qty, bd, today, **rates).items()})
            tax["basis"] = "user-provided cost and buy date"
        elif cmp_:
            hyp = hypothetical_position(cmp_, today, stcg_pct=self.p.stcg_pct, ltcg_pct=self.p.ltcg_pct,
                                        exemption=self.p.ltcg_exemption)
            tax = dict(hyp)
            tax["basis"] = "ASSUMPTION: ₹1 lakh position, +20% gain, 180 days held (no holding provided)"
        if tax:
            s.tables.append(SectionTable(title=f"Tax illustration (rates as of {self.p.tax_rates_as_of or 'n/a'}; not tax advice)",
                                         columns=["Item", "Value"], rows=[[k, v] for k, v in tax.items()]))
        self.v["tax"] = tax
        return s


def assemble(ctx: ReportContext) -> Assembled:
    return _Builder(ctx).build()


__all__ = ["Assembled", "Params", "ReportContext", "assemble", "finish", "median", "normalized_eps"]
