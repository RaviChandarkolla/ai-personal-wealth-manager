"""Technical indicators on daily OHLCV (Section 10). Pure functions, no external libs."""

from __future__ import annotations


def sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def ema_series(values: list[float], n: int) -> list[float]:
    if len(values) < n:
        return []
    k = 2 / (n + 1)
    out = [sum(values[:n]) / n]
    for v in values[n:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def ema(values: list[float], n: int) -> float | None:
    s = ema_series(values, n)
    return s[-1] if s else None


def rsi(closes: list[float], n: int = 14) -> float | None:
    """Wilder RSI."""
    if len(closes) <= n:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gain = sum(max(d, 0) for d in deltas[:n]) / n
    loss = sum(max(-d, 0) for d in deltas[:n]) / n
    for d in deltas[n:]:
        gain = (gain * (n - 1) + max(d, 0)) / n
        loss = (loss * (n - 1) + max(-d, 0)) / n
    if loss == 0:
        return 100.0
    return 100 - 100 / (1 + gain / loss)


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram) of the latest bar, or None."""
    f, s = ema_series(closes, fast), ema_series(closes, slow)
    if not s:
        return None
    f = f[len(f) - len(s):]
    line = [a - b for a, b in zip(f, s, strict=True)]
    sig = ema_series(line, signal)
    if not sig:
        return None
    return line[-1], sig[-1], line[-1] - sig[-1]


def _true_ranges(h: list[float], low: list[float], c: list[float]) -> list[float]:
    return [max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1])) for i in range(1, len(c))]


def atr(h: list[float], low: list[float], c: list[float], n: int = 14) -> float | None:
    tr = _true_ranges(h, low, c)
    if len(tr) < n:
        return None
    a = sum(tr[:n]) / n
    for t in tr[n:]:
        a = (a * (n - 1) + t) / n
    return a


def adx(h: list[float], low: list[float], c: list[float], n: int = 14):
    """Wilder ADX. Returns (adx, +DI, -DI) or None."""
    if len(c) < 2 * n + 1:
        return None
    tr = _true_ranges(h, low, c)
    pdm, mdm = [], []
    for i in range(1, len(c)):
        up, dn = h[i] - h[i - 1], low[i - 1] - low[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
    s_tr, s_p, s_m = sum(tr[:n]), sum(pdm[:n]), sum(mdm[:n])
    dxs: list[float] = []
    pdi = mdi = 0.0
    for i in range(n, len(tr) + 1):
        if i > n:
            s_tr = s_tr - s_tr / n + tr[i - 1]
            s_p = s_p - s_p / n + pdm[i - 1]
            s_m = s_m - s_m / n + mdm[i - 1]
        pdi = 100 * s_p / s_tr if s_tr else 0.0
        mdi = 100 * s_m / s_tr if s_tr else 0.0
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
    if len(dxs) < n:
        return None
    a = sum(dxs[:n]) / n
    for d in dxs[n:]:
        a = (a * (n - 1) + d) / n
    return a, pdi, mdi


def floor_pivots(high: float, low: float, close: float) -> dict[str, float]:
    p = (high + low + close) / 3
    return {
        "P": p,
        "R1": 2 * p - low, "S1": 2 * p - high,
        "R2": p + (high - low), "S2": p - (high - low),
        "R3": high + 2 * (p - low), "S3": low - 2 * (high - p),
    }


def cross_state(closes: list[float], fast: int = 50, slow: int = 200) -> tuple[str, int | None]:
    """('golden'|'death'|'none', bars since last cross) from SMA fast vs slow."""
    if len(closes) < slow + 1:
        return "none", None
    last: tuple[str, int] | None = None
    prev = None
    for i in range(slow, len(closes) + 1):
        w = closes[:i]
        diff = sum(w[-fast:]) / fast - sum(w[-slow:]) / slow
        if prev is not None and prev <= 0 < diff:
            last = ("golden", len(closes) - i)
        elif prev is not None and prev >= 0 > diff:
            last = ("death", len(closes) - i)
        prev = diff
    return last if last else ("none", None)


def up_down_volume(closes: list[float], volumes: list[float], n: int = 20) -> tuple[float, float]:
    up = dn = 0.0
    for i in range(max(1, len(closes) - n), len(closes)):
        if closes[i] > closes[i - 1]:
            up += volumes[i]
        elif closes[i] < closes[i - 1]:
            dn += volumes[i]
    return up, dn


def weinstein_stage(closes: list[float], weeks: int = 30) -> str | None:
    """Stage 1-4 from the 30-week MA (150 trading days) slope and price position."""
    n = weeks * 5
    if len(closes) < n + 20:
        return None
    ma_now = sum(closes[-n:]) / n
    ma_prev = sum(closes[-n - 20:-20]) / n
    slope = (ma_now - ma_prev) / ma_prev
    price = closes[-1]
    flat = abs(slope) < 0.01
    if flat:
        if abs(price / ma_now - 1) < 0.05:
            return "Stage 1"
        return "Stage 3" if price > ma_now else "Stage 1"
    if slope > 0:
        if price < ma_now or price < 0.95 * max(closes[-n:]):
            return "Stage 3"  # rising MA but price rolling over from its high
        if len(closes) >= n + 40:
            ma_pp = sum(closes[-n - 40:-40]) / n
            prev_slope = (ma_prev - ma_pp) / ma_pp
            if prev_slope > 0 and slope < 0.5 * prev_slope:
                return "Stage 3"  # rise is flattening
        return "Stage 2"
    return "Stage 4" if price < ma_now else "Stage 3"
