"""Fund metrics from a NAV series (pandas Series indexed by DatetimeIndex, ascending)."""

import numpy as np
import pandas as pd

from ..config import get_settings

TRADING_DAYS = 252


def _validate(nav: pd.Series) -> None:
    if len(nav) < 2:
        raise ValueError("need at least 2 NAV points")
    if (nav <= 0).any():
        raise ValueError("NAV must be positive")
    if not nav.index.is_monotonic_increasing:
        raise ValueError("NAV index must be ascending")


def cagr(nav: pd.Series) -> float:
    """(last/first)^(1/years)-1. e.g. 100 -> 121 over 2y = 10%."""
    _validate(nav)
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    if years <= 0:
        raise ValueError("NAV span must be positive")
    return float((nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1)


def rolling_returns(nav: pd.Series, window_years: float) -> pd.Series:
    """Annualized return over each trailing window of `window_years` calendar years."""
    _validate(nav)
    window = pd.DateOffset(days=round(window_years * 365.25))
    out = {}
    for date, value in nav.items():
        start = date - window
        if start < nav.index[0]:
            continue
        base = nav.asof(start)
        out[date] = (value / base) ** (1 / window_years) - 1
    return pd.Series(out, dtype=float)


def _daily_returns(nav: pd.Series) -> pd.Series:
    _validate(nav)
    return nav.pct_change().dropna()


def annualized_volatility(nav: pd.Series) -> float:
    return float(_daily_returns(nav).std(ddof=1) * np.sqrt(TRADING_DAYS))


def max_drawdown(nav: pd.Series) -> float:
    """Most negative peak-to-trough fall as a fraction. e.g. 100,120,90 -> -0.25."""
    _validate(nav)
    return float((nav / nav.cummax() - 1).min())


def sharpe(nav: pd.Series, risk_free_rate: float | None = None) -> float:
    rf = get_settings().risk_free_rate if risk_free_rate is None else risk_free_rate
    r = _daily_returns(nav)
    excess = r - rf / TRADING_DAYS
    sd = r.std(ddof=1)
    return 0.0 if sd == 0 else float(excess.mean() / sd * np.sqrt(TRADING_DAYS))


def sortino(nav: pd.Series, risk_free_rate: float | None = None) -> float:
    rf = get_settings().risk_free_rate if risk_free_rate is None else risk_free_rate
    r = _daily_returns(nav)
    excess = r - rf / TRADING_DAYS
    downside = np.sqrt((np.minimum(excess, 0) ** 2).mean())
    return 0.0 if downside == 0 else float(excess.mean() / downside * np.sqrt(TRADING_DAYS))


def beta(nav: pd.Series, benchmark_nav: pd.Series) -> float:
    """Market sensitivity beta: Cov(r_fund, r_bench) / Var(r_bench)."""
    _validate(nav)
    _validate(benchmark_nav)
    r_fund = _daily_returns(nav)
    r_bench = _daily_returns(benchmark_nav)
    df = pd.DataFrame({"fund": r_fund, "bench": r_bench}).dropna()
    if len(df) < 2:
        return 1.0
    var_b = float(df["bench"].var(ddof=1))
    if var_b == 0 or np.isnan(var_b):
        return 1.0
    cov = float(df["fund"].cov(df["bench"]))
    return round(cov / var_b, 4)


def alpha(
    nav: pd.Series,
    benchmark_nav: pd.Series,
    risk_free_rate: float | None = None,
) -> float:
    """Annualized Jensen's alpha in percentage points: (CAGR_fund - [rf + beta * (CAGR_bench - rf)]) * 100."""
    _validate(nav)
    _validate(benchmark_nav)
    common_idx = nav.index.intersection(benchmark_nav.index)
    if len(common_idx) < 2:
        return 0.0
    f_series = nav.loc[common_idx]
    b_series = benchmark_nav.loc[common_idx]

    c_fund = cagr(f_series)
    c_bench = cagr(b_series)
    b = beta(f_series, b_series)
    rf = get_settings().risk_free_rate if risk_free_rate is None else risk_free_rate
    expected = rf + b * (c_bench - rf)
    return round((c_fund - expected) * 100, 2)


def tracking_error(nav: pd.Series, benchmark_nav: pd.Series) -> float:
    """Annualized std-dev of daily active returns: std(r_fund - r_bench) * sqrt(252).

    Worked example: if a fund tracks its benchmark identically on every day
    (nav == benchmark_nav), active return is 0 each day -> tracking_error = 0.0.
    """
    _validate(nav)
    _validate(benchmark_nav)
    r_fund = _daily_returns(nav)
    r_bench = _daily_returns(benchmark_nav)
    df = pd.DataFrame({"fund": r_fund, "bench": r_bench}).dropna()
    if len(df) < 2:
        return 0.0
    diff = df["fund"] - df["bench"]
    sd = float(diff.std(ddof=1))
    if np.isnan(sd) or sd == 0.0:
        return 0.0
    return float(sd * np.sqrt(TRADING_DAYS))


def calendar_returns(nav: pd.Series) -> dict[int, float]:
    """Point-to-point return for each calendar year present in `nav` as a fraction.

    Worked example: NAV of 100 on 2023-01-02 and 115 on 2023-12-29 -> {2023: 0.15}.
    """
    _validate(nav)
    out: dict[int, float] = {}
    years = sorted(set(nav.index.year))
    for yr in years:
        sub = nav[nav.index.year == yr]
        if len(sub) >= 2:
            out[int(yr)] = float(sub.iloc[-1] / sub.iloc[0] - 1)
    return out


def rolling_consistency(
    nav: pd.Series,
    window_years: float = 3,
    risk_free_rate: float | None = None,
) -> float:
    """Share (0.0 to 1.0) of rolling windows of `window_years` whose annualized return beats `rf`.

    Worked example: if 45 out of 50 rolling 3-year windows have return > 0.065 (rf),
    rolling_consistency = 45 / 50 = 0.90.
    """
    _validate(nav)
    rf = get_settings().risk_free_rate if risk_free_rate is None else risk_free_rate
    roll = rolling_returns(nav, window_years)
    if roll.empty:
        return 0.0
    return float((roll > rf).mean())


