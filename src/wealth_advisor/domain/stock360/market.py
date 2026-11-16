"""Section 1: market data from daily OHLCV and the Nifty series. Pure functions."""

from __future__ import annotations

from statistics import mean

from ...data.stocks.provider import StockOHLCV
from .metric import computed, safe_div
from .models import Metric

# trading-day lookbacks
LOOKBACKS = {"1w": 5, "1m": 21, "3m": 63, "6m": 126, "1y": 252, "3y": 756}


def period_return(closes: list[float], days: int) -> float | None:
    """% return over `days` bars; None when history is shorter (never extrapolated)."""
    if len(closes) <= days:
        return None
    return (closes[-1] / closes[-1 - days] - 1) * 100


def beta(stock: list[float], index: list[float], n: int = 252) -> float | None:
    k = min(n, len(stock) - 1, len(index) - 1)
    if k < 60:
        return None
    s = [stock[-k + i] / stock[-k + i - 1] - 1 for i in range(k)]
    m = [index[-k + i] / index[-k + i - 1] - 1 for i in range(k)]
    ms, mm = mean(s), mean(m)
    var = sum((x - mm) ** 2 for x in m)
    return None if var == 0 else sum((a - ms) * (b - mm) for a, b in zip(s, m, strict=True)) / var


def align(bars: list[StockOHLCV], index: list[tuple[str, float]]) -> tuple[list[float], list[float]]:
    """Closes of stock and index on their common dates."""
    idx = dict(index)
    pairs = [(b.close, idx[b.date]) for b in bars if b.date in idx]
    return [p[0] for p in pairs], [p[1] for p in pairs]


def market_metrics(
    sym: str,
    bars: list[StockOHLCV],
    index: list[tuple[str, float]],
    market_cap_cr: float | None = None,
    ev_cr: float | None = None,
) -> list[Metric]:
    if not bars:
        return [computed(sym, "s1:cmp", "CMP", None, "₹")]
    last = bars[-1]
    asof = last.date
    year = bars[-252:]
    hi, lo = max(b.high for b in year), min(b.low for b in year)
    out = [
        computed(sym, "s1:cmp", "CMP", last.close, "₹", as_of=asof, source="yahoo_chart"),
        computed(sym, "s1:high52", "52W High", hi, "₹", as_of=asof, formula="max(high, 252 bars)"),
        computed(sym, "s1:low52", "52W Low", lo, "₹", as_of=asof, formula="min(low, 252 bars)"),
        computed(sym, "s1:pct_from_high", "% from 52W High", (last.close / hi - 1) * 100, "%",
                 as_of=asof, formula="CMP / 52W high - 1"),
        computed(sym, "s1:pct_from_low", "% above 52W Low", (last.close / lo - 1) * 100, "%",
                 as_of=asof, formula="CMP / 52W low - 1"),
        computed(sym, "s1:market_cap", "Market cap", market_cap_cr, "₹ Cr", as_of=asof,
                 source="yahoo_chart", status="verified" if market_cap_cr is not None else None),
        computed(sym, "s1:ev", "Enterprise value", ev_cr, "₹ Cr", as_of=asof,
                 source="yahoo_chart", status="verified" if ev_cr is not None else None),
    ]
    closes = [b.close for b in bars]
    s_al, i_al = align(bars, index)
    for key, d in LOOKBACKS.items():
        r = period_return(closes, d)
        out.append(computed(sym, f"s1:ret_{key}", f"Return {key.upper()}", r, "%", as_of=asof,
                            formula=f"close / close[-{d}] - 1"))
    for key in ("3m", "6m", "1y"):
        d = LOOKBACKS[key]
        rs, ri = period_return(s_al, d), period_return(i_al, d)
        out.append(computed(sym, f"s1:rs_{key}", f"Relative strength vs Nifty 50 ({key.upper()})",
                            None if rs is None or ri is None else rs - ri, "pp", as_of=asof,
                            formula="stock return - Nifty 50 return"))
    out.append(computed(sym, "s1:beta", "Beta (1Y daily vs Nifty 50)", beta(s_al, i_al), "",
                        as_of=asof, formula="cov(stock, index) / var(index)"))
    tv = [b.close * b.volume for b in bars[-20:] if b.volume]
    out.append(computed(sym, "s1:avg_traded_value", "20D avg traded value",
                        mean(tv) / 1e7 if len(tv) == 20 else None, "₹ Cr", as_of=asof,
                        formula="mean(close x volume, 20d)"))
    return out


def nifty_below_200dma(index: list[tuple[str, float]]) -> bool | None:
    closes = [c for _, c in index]
    if len(closes) < 200:
        return None
    return closes[-1] < sum(closes[-200:]) / 200


__all__ = ["align", "beta", "market_metrics", "nifty_below_200dma", "period_return", "safe_div"]
