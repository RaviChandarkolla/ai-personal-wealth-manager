"""Precompute per-fund stats (M2 metrics) and within-category percentiles."""

import pandas as pd

from ..config import get_settings
from ..domain.metrics import (
    alpha,
    annualized_volatility,
    beta,
    cagr,
    max_drawdown,
    rolling_returns,
    sharpe,
    sortino,
)
from .store import FundStats, MetadataStore


def _trailing_cagr(nav: pd.Series, years: int) -> float | None:
    start = nav.index[-1] - pd.DateOffset(years=years)
    if nav.index[0] > start + pd.Timedelta(days=10):
        return None
    window = nav[nav.index >= start]
    return cagr(window) * 100 if len(window) >= 2 else None


def _percentile(value: float | None, peers: list[float]) -> float | None:
    """Share of peers below the value (ties count half), 0-100. e.g. best of 4 -> 87.5."""
    if value is None or not peers:
        return None
    below = sum(p < value for p in peers)
    equal = sum(p == value for p in peers)
    return round(100 * (below + 0.5 * equal) / len(peers), 2)


def _percentile_low_vol(value: float | None, peers: list[float]) -> float | None:
    """Share of peers with higher volatility (ties count half), 0-100. Lower volatility ranks higher."""
    if value is None or not peers:
        return None
    higher = sum(p > value for p in peers)
    equal = sum(p == value for p in peers)
    return round(100 * (higher + 0.5 * equal) / len(peers), 2)


def compute_all_stats(store: MetadataStore, as_of: "date | None" = None) -> list[FundStats]:
    from datetime import date as _date  # noqa: F401

    s = get_settings()
    funds = store.query_funds()
    raw: dict[str, FundStats] = {}
    nav_cache: dict[str, pd.Series] = {}

    for fund in funds:
        nav = store.nav_series(fund.code)
        if as_of is not None and len(nav) > 0:
            nav = nav[nav.index <= pd.Timestamp(as_of)]
        if len(nav) < 30:
            continue
        nav_cache[fund.code] = nav

        roll = rolling_returns(nav, 3) if (nav.index[-1] - nav.index[0]).days > 3 * 366 else None
        raw[fund.code] = FundStats(
            code=fund.code,
            as_of=nav.index[-1].date(),
            n_points=len(nav),
            cagr_3y=_trailing_cagr(nav, 3),
            cagr_5y=_trailing_cagr(nav, 5),
            roll_3y_median=float(roll.median() * 100) if roll is not None and len(roll) else None,
            volatility=annualized_volatility(nav) * 100,
            max_drawdown=max_drawdown(nav) * 100,
            sharpe=sharpe(nav),
            sortino=sortino(nav),
        )

    # Compute beta and alpha against benchmark proxy
    for fund in funds:
        if fund.code not in raw:
            continue
        proxy_code = s.benchmark_proxy_codes.get(fund.category)
        if not proxy_code:
            continue
        if proxy_code == fund.code:
            raw[fund.code].beta = 1.0
            raw[fund.code].alpha = 0.0
            continue

        proxy_nav = nav_cache.get(proxy_code)
        if proxy_nav is None:
            proxy_nav = store.nav_series(proxy_code)
            if len(proxy_nav) >= 30:
                nav_cache[proxy_code] = proxy_nav
            elif "DEMO040" in raw:
                demo_proxy = "DEMO040"
                if demo_proxy == fund.code:
                    raw[fund.code].beta = 1.0
                    raw[fund.code].alpha = 0.0
                    continue
                proxy_nav = nav_cache.get(demo_proxy)
                if proxy_nav is None:
                    proxy_nav = store.nav_series(demo_proxy)
                    if len(proxy_nav) >= 30:
                        nav_cache[demo_proxy] = proxy_nav

        fund_nav = nav_cache[fund.code]
        if proxy_nav is not None and len(proxy_nav) >= 30:
            # Trailing 3-year window if available
            start_3y = fund_nav.index[-1] - pd.DateOffset(years=3)
            f_3y = fund_nav[fund_nav.index >= start_3y]
            p_3y = proxy_nav[proxy_nav.index >= start_3y]
            if len(f_3y) >= 30 and len(p_3y) >= 30:
                calc_f, calc_p = f_3y, p_3y
            else:
                calc_f, calc_p = fund_nav, proxy_nav
            try:
                raw[fund.code].beta = beta(calc_f, calc_p)
                raw[fund.code].alpha = alpha(calc_f, calc_p, s.risk_free_rate)
            except Exception:
                pass

    by_cat: dict[str, list[str]] = {}
    for fund in funds:
        if fund.code in raw:
            by_cat.setdefault(fund.category, []).append(fund.code)
    for codes in by_cat.values():
        roll_vals = [raw[c].roll_3y_median for c in codes if raw[c].roll_3y_median is not None]
        c5_vals = [raw[c].cagr_5y for c in codes if raw[c].cagr_5y is not None]
        vol_vals = [raw[c].volatility for c in codes if raw[c].volatility is not None]
        for c in codes:
            raw[c].pct_roll_3y = _percentile(raw[c].roll_3y_median, roll_vals)  # type: ignore[arg-type]
            raw[c].pct_cagr_5y = _percentile(raw[c].cagr_5y, c5_vals)  # type: ignore[arg-type]
            raw[c].pct_low_vol = _percentile_low_vol(raw[c].volatility, vol_vals)  # type: ignore[arg-type]
    out = list(raw.values())
    store.save_stats(out)
    return out
