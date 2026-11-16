"""Section 3: multiples, own-history medians, peers, overvaluation verdict. Pure functions."""

from __future__ import annotations

from bisect import bisect_right
from statistics import median

from ...data.stocks.provider import StockFinancialYear, StockSnapshot
from .fundamentals import sort_fy
from .metric import safe_div


def pe(price: float | None, eps: float | None) -> float | None:
    return None if price is None or eps is None or eps <= 0 else price / eps


def pb(price: float | None, bvps: float | None) -> float | None:
    return None if price is None or bvps is None or bvps <= 0 else price / bvps


def ps(market_cap_cr: float | None, revenue_cr: float | None) -> float | None:
    return None if not market_cap_cr or not revenue_cr or revenue_cr <= 0 else market_cap_cr / revenue_cr


def ev_ebitda(ev_cr: float | None, ebitda_cr: float | None) -> float | None:
    return None if not ev_cr or ebitda_cr is None or ebitda_cr <= 0 else ev_cr / ebitda_cr


def dividend_yield_pct(dps: float | None, price: float | None) -> float | None:
    v = safe_div(dps, price)
    return None if v is None else v * 100


def sustainable_growth_pct(rows: list[StockFinancialYear], eps_cagr_pct: float | None) -> float | None:
    """min(3Y EPS CAGR, retention x ROE, 30%). Needs EPS CAGR and an ROE; None otherwise."""
    r = sort_fy(rows)
    if eps_cagr_pct is None or not r:
        return None
    last = r[-1]
    if not last.net_worth_cr or last.net_worth_cr <= 0 or last.pat_cr is None or last.pat_cr <= 0:
        return None
    roe = last.pat_cr / last.net_worth_cr
    payout = 0.0
    if last.dividends_paid_cr is not None:
        payout = min(max(abs(last.dividends_paid_cr) / last.pat_cr, 0.0), 1.0)
    return min(eps_cagr_pct, roe * (1 - payout) * 100, 30.0)


def peg(pe_ttm: float | None, growth_pct: float | None) -> float | None:
    return None if pe_ttm is None or growth_pct is None or growth_pct <= 0 else pe_ttm / growth_pct


def peg_band(v: float | None) -> str:
    if v is None:
        return "Not available"
    return "<1.0 attractive" if v < 1.0 else "1.0-1.5 fair" if v < 1.5 else "1.5-2.0 full" if v <= 2.0 else ">2.0 expensive"


def price_on_or_before(series: list[tuple[str, float]], d: str) -> float | None:
    """Close on the last trading date <= d (series sorted by date); None if d precedes it."""
    i = bisect_right([x[0] for x in series], d)
    return series[i - 1][1] if i else None


def own_history_multiples(
    rows: list[StockFinancialYear], prices: list[tuple[str, float]]
) -> dict[str, list[tuple[str, float | None]]]:
    """P/E, P/B at each FY end that has price history: price at FY end / that year's EPS, BVPS.

    Split-adjusted Yahoo closes make this consistent with Yahoo EPS. Years outside the price
    history are omitted (so the label can state the years actually available)."""
    out: dict[str, list[tuple[str, float | None]]] = {"pe": [], "pb": []}
    for r in sort_fy(rows):
        p = price_on_or_before(prices, r.period_end)
        if p is None or (prices and r.period_end < prices[0][0]):
            continue
        out["pe"].append((r.period_end, pe(p, r.eps)))
        out["pb"].append((r.period_end, pb(p, r.bvps)))
    return out


def history_median(points: list[tuple[str, float | None]]) -> tuple[float | None, str]:
    vals = [v for _, v in points if v is not None]
    if not vals:
        return None, "no history"
    return median(vals), f"median of {len(vals)} FY-end value(s)"


def relative_to(value: float | None, ref: float | None) -> float | None:
    """% premium (+) or discount (-) of value vs reference."""
    return None if value is None or ref is None or ref <= 0 else (value / ref - 1) * 100


def peer_table(
    subject: str, peers: list[StockSnapshot], top: int = 3
) -> tuple[list[StockSnapshot], dict[str, float | None]]:
    """Top `top` peers by market cap (excluding subject) and sector medians of key multiples."""
    others = [p for p in peers if p.symbol != subject and p.market_cap_cr]
    others.sort(key=lambda p: p.market_cap_cr or 0, reverse=True)

    def med(attr: str) -> float | None:
        vals = [getattr(p, attr) for p in others if getattr(p, attr) not in (None, 0)]
        return median(vals) if len(vals) >= 3 else None

    return others[:top], {k: med(k) for k in ("pe", "pb", "ps", "ev_ebitda", "roe", "roce")}


def overvaluation_verdict(cmp_: float, composite_iv: float | None) -> tuple[str, float | None, float | None]:
    """(verdict, % CMP vs IV, margin of safety %). Band +-15%."""
    if composite_iv is None or composite_iv <= 0:
        return "Not available", None, None
    prem = (cmp_ / composite_iv - 1) * 100
    mos = (1 - cmp_ / composite_iv) * 100
    label = "Overvalued" if prem > 15 else "Undervalued" if prem < -15 else "Fairly valued"
    return label, prem, mos
