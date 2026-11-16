"""Sections 12, 13, 17: Buffett checklist, Lynch category, legend sell triggers (mechanical part).

All rules are the documented ones from the 360° plan. A trigger that can't be evaluated has
fired=None ("Not evaluable") and is never counted as HOLD.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from ...data.stocks.provider import StockFinancialYear
from . import fundamentals as fnd
from .models import LegendTrigger

LEGENDS = ("Buffett", "Lynch", "Graham", "Fisher", "Munger", "Livermore", "Jhunjhunwala")


# ---------------- Buffett / Lynch ----------------

def buffett_checklist(
    rows: list[StockFinancialYear], eps_cagr_pct: float | None,
    margin_of_safety_pct: float | None, opm_vs_peer_median: float | None = None,
) -> list[tuple[str, bool | None]]:
    """Mechanical items only; judgement items (moat, management) are left to the LLM."""
    r = fnd.sort_fy(rows)
    roes = [fnd.roe_pct(x) for x in r]
    roe_ok = None if not roes or any(x is None for x in roes) else all(x > 15 for x in roes)  # type: ignore[operator]
    de = None
    if r and r[-1].total_debt_cr is not None and r[-1].net_worth_cr and r[-1].net_worth_cr > 0:
        de = r[-1].total_debt_cr / r[-1].net_worth_cr
    return [
        ("ROE > 15% in every year available", roe_ok),
        ("Net debt/equity < 0.5", None if de is None else de < 0.5),
        ("Operating margin above peer median", None if opm_vs_peer_median is None else opm_vs_peer_median > 0),
        ("EPS CAGR > 10%", None if eps_cagr_pct is None else eps_cagr_pct > 10),
        ("Positive margin of safety", None if margin_of_safety_pct is None else margin_of_safety_pct > 0),
    ]


def lynch_category(
    growth_pct: float | None, cyclical: bool, rows: list[StockFinancialYear]
) -> str:
    """Slow <10 / Stalwart 10-20 / Fast 20-50+ ; Cyclical from sector flag; Turnaround when PAT
    turned positive within 2 years. Asset play needs judgement (left to the LLM)."""
    r = fnd.sort_fy(rows)
    if len(r) >= 3 and r[-3].pat_cr is not None and r[-3].pat_cr <= 0 and (r[-1].pat_cr or 0) > 0:
        return "Turnaround"
    if cyclical:
        return "Cyclical"
    if growth_pct is None:
        return "Requires judgement"
    if growth_pct < 10:
        return "Slow grower"
    if growth_pct < 20:
        return "Stalwart"
    return "Fast grower"


# ---------------- Legend triggers ----------------

@dataclass
class TriggerContext:
    cmp: float
    rows: list[StockFinancialYear]
    composite_iv: float | None = None
    graham_number: float | None = None
    peg: float | None = None
    cyclical: bool = False
    cycle: str = "Insufficient history"
    is_bank: bool = False
    closes: list[float] = field(default_factory=list)
    volumes: list[float] = field(default_factory=list)
    nifty_below_200dma: bool | None = None
    rs_3m: float | None = None
    sector_rs_3m: float | None = None
    eps_3y_projected: float | None = None
    median_pe_5y: float | None = None


def _t(legend: str, trigger: str, kind: str, fired: bool | None, detail: str = "") -> LegendTrigger:
    return LegendTrigger(legend=legend, trigger=trigger, kind=kind, fired=fired, detail=detail)  # type: ignore[arg-type]


def _down_n(vals: list[float | None], n: int) -> bool | None:
    """True if the last n changes are all declines; None when not enough data."""
    v = vals[-(n + 1):]
    if len(v) < n + 1 or any(x is None for x in v):
        return None
    return all(v[i + 1] < v[i] for i in range(n))  # type: ignore[operator]


def evaluate_triggers(c: TriggerContext) -> list[LegendTrigger]:
    r = fnd.sort_fy(c.rows)
    out: list[LegendTrigger] = []
    cmp_ = c.cmp

    # Buffett
    roce = [fnd.roce_pct(x) for x in r]
    f_roce = None
    if len([x for x in roce if x is not None]) >= 4:
        vals = [x for x in roce if x is not None]
        med = median(vals[-4:])
        f_roce = vals[-1] < med and vals[-2] < med
    out.append(_t("Buffett", "Industry economics worsened: ROCE below 4Y median 2 years running", "D", f_roce))
    out.append(_t("Buffett", "Valuation absurdly above intrinsic value (CMP > 1.5 x IV)", "D",
                  None if not c.composite_iv else cmp_ > 1.5 * c.composite_iv))
    for t in ("Moat deteriorated", "Thesis wrong", "Management untrustworthy", "Better opportunity exists"):
        out.append(_t("Buffett", t, "J", None))

    # Lynch
    f_peg = None if c.peg is None and not c.cyclical else ((c.peg or 0) > 2.0 or (c.cyclical and c.cycle == "Peak"))
    out.append(_t("Lynch", "PEG > 2.0 (cyclicals: margins at peak)", "D", f_peg))
    opm = [fnd.opm_pct(x) for x in r]
    opm_down = _down_n(opm, 2)
    wc = _down_n([-(fnd.receivable_days(x)) if fnd.receivable_days(x) is not None else None for x in r], 1)
    de = [x.total_debt_cr / x.net_worth_cr if x.total_debt_cr is not None and x.net_worth_cr and x.net_worth_cr > 0
          else None for x in r]
    de_up = None if len(de) < 2 or de[-1] is None or de[-2] is None else de[-1] > de[-2]
    wc_worse = (wc is True) or de_up
    f_det = None if opm_down is None or (wc is None and de_up is None) else bool(opm_down and wc_worse)
    out.append(_t("Lynch", "Fundamentals deteriorating: OPM down 2 periods and receivable days or D/E up", "D", f_det))
    out.append(_t("Lynch", "Overcrowded / hype / insiders selling", "W", None))

    # Graham
    g = c.graham_number
    out.append(_t("Graham", "CMP at or above Graham Number", "D", None if g is None else cmp_ >= g))
    out.append(_t("Graham", "CMP >= 1.5 x Graham Number", "D", None if g is None else cmp_ >= 1.5 * g))
    pat_down, opm_dn = _down_n([x.pat_cr for x in r], 2), opm_down
    f_earn = None if pat_down is None and opm_dn is None else bool(pat_down or opm_dn)
    out.append(_t("Graham", "Earnings or margins down 2 consecutive periods", "D", f_earn))
    if c.is_bank or not r:
        out.append(_t("Graham", "D/E > 1.0 or current ratio < 1.5 (non-banks)", "D", None))
    else:
        last = r[-1]
        cr = None if not last.current_liabilities_cr or last.current_assets_cr is None else \
            last.current_assets_cr / last.current_liabilities_cr
        d_e = de[-1]
        parts = [p for p in ((d_e is not None and d_e > 1.0) if d_e is not None else None,
                             (cr < 1.5) if cr is not None else None) if p is not None]
        out.append(_t("Graham", "D/E > 1.0 or current ratio < 1.5 (non-banks)", "D", any(parts) if parts else None))
    out.append(_t("Graham", "Margin of safety gone (CMP > composite IV)", "D",
                  None if not c.composite_iv else cmp_ > c.composite_iv))

    # Fisher: judgement only (do not sell on high P/E alone)
    out.append(_t("Fisher", "Mistake / deterioration / far better opportunity", "J", None,
                  "Do not sell on high P/E alone"))

    # Munger
    gm = _down_n(opm, 3)
    out.append(_t("Munger", "Pricing power proxy: operating margin down 3 years", "D", gm))
    for t in ("Incentives misaligned", "Moat eroding", "Opportunity cost / inversion test"):
        out.append(_t("Munger", t, "J", None))

    # Livermore
    cl, vo = c.closes, c.volumes
    f_dist = None
    if len(cl) >= 51 and len(vo) == len(cl):
        dma50 = sum(cl[-50:]) / 50
        up = sum(vo[i] for i in range(len(cl) - 20, len(cl)) if cl[i] > cl[i - 1])
        dn = sum(vo[i] for i in range(len(cl) - 20, len(cl)) if cl[i] < cl[i - 1])
        f_dist = cl[-1] < dma50 and dn > up
    out.append(_t("Livermore", "Below 50-DMA with down-volume > up-volume (20d)", "D", f_dist))
    out.append(_t("Livermore", "Pivot broken: close below last swing low",
                  "D", None if len(cl) < 66 else cl[-1] < min(cl[-65:-5])))
    out.append(_t("Livermore", "Extended: > 40% above 200-DMA", "D",
                  None if len(cl) < 200 else cl[-1] > 1.4 * (sum(cl[-200:]) / 200)))
    out.append(_t("Livermore", "RS divergence: 3M RS < 0 while sector > 0", "D",
                  None if c.rs_3m is None or c.sector_rs_3m is None else c.rs_3m < 0 < c.sector_rs_3m))
    out.append(_t("Livermore", "Broad market bear phase (Nifty below 200-DMA)", "D", c.nifty_below_200dma))

    # Rakesh Jhunjhunwala
    f_rj = None
    if c.eps_3y_projected and c.median_pe_5y and c.eps_3y_projected > 0:
        f_rj = cmp_ > c.eps_3y_projected * c.median_pe_5y
    out.append(_t("Jhunjhunwala", "Valuation > 3 years ahead of earnings", "D", f_rj))
    comp = None
    if r:
        last = r[-1]
        v = fnd.roe_pct(last) if c.is_bank else fnd.roce_pct(last)
        comp = None if v is None else v < 18
    out.append(_t("Jhunjhunwala", "ROCE (ROE for banks) no longer > 18%", "D", comp))
    out.append(_t("Jhunjhunwala", "Macro story weakened / execution / governance", "W", None))
    out.append(_t("Jhunjhunwala", "Crowded retail trade", "W", None))
    return out


def legend_verdicts(triggers: list[LegendTrigger]) -> dict[str, str]:
    """SELL when >= 2 D triggers fire, REDUCE when 1, else HOLD; 'NOT EVALUABLE' when the legend
    has no evaluable D trigger."""
    out: dict[str, str] = {}
    for lg in LEGENDS:
        d = [t for t in triggers if t.legend == lg and t.kind == "D" and t.fired is not None]
        if not d:
            out[lg] = "NOT EVALUABLE"
            continue
        n = sum(1 for t in d if t.fired)
        out[lg] = "SELL" if n >= 2 else "REDUCE" if n == 1 else "HOLD"
    return out


def sell_signal_count(verdicts: dict[str, str]) -> tuple[int, int]:
    """(legends saying SELL/REDUCE, evaluable legends)."""
    ev = [v for v in verdicts.values() if v != "NOT EVALUABLE"]
    return sum(1 for v in ev if v in ("SELL", "REDUCE")), len(ev)


def consensus_action(signals: int) -> str:
    """SELL 5-7 | REDUCE 3-4 | HOLD 1-2 | ADD 0."""
    return "SELL" if signals >= 5 else "REDUCE" if signals >= 3 else "HOLD" if signals >= 1 else "ADD"
