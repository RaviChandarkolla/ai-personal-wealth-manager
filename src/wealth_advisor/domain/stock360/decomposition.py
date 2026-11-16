"""Section 5: price vs fundamentals, return decomposition, mean-reversion price."""

from __future__ import annotations

from math import log


def pct_change(old: float | None, new: float | None) -> float | None:
    return None if old is None or new is None or old <= 0 else (new / old - 1) * 100


def decompose_return(
    price_old: float, price_new: float, eps_old: float | None, eps_new: float | None
) -> dict[str, float] | None:
    """Price return = (1+EPS growth)(1+P/E change) - 1, split on a log basis so the parts add up.

    Requires positive EPS at both ends."""
    if min(price_old, price_new) <= 0 or eps_old is None or eps_new is None or min(eps_old, eps_new) <= 0:
        return None
    total_log = log(price_new / price_old)
    eps_log = log(eps_new / eps_old)
    rerate_log = total_log - eps_log
    total = (price_new / price_old - 1) * 100
    if total_log == 0:
        return {"price_return_pct": 0.0, "eps_growth_pct": (eps_new / eps_old - 1) * 100,
                "pe_change_pct": ((price_new / eps_new) / (price_old / eps_old) - 1) * 100,
                "from_eps_pct": 0.0, "from_rerating_pct": 0.0}
    return {
        "price_return_pct": total,
        "eps_growth_pct": (eps_new / eps_old - 1) * 100,
        "pe_change_pct": ((price_new / eps_new) / (price_old / eps_old) - 1) * 100,
        "from_eps_pct": total * eps_log / total_log,
        "from_rerating_pct": total * rerate_log / total_log,
    }


def mean_reversion_price(eps_ttm: float | None, median_pe: float | None) -> float | None:
    if eps_ttm is None or median_pe is None or eps_ttm <= 0 or median_pe <= 0:
        return None
    return eps_ttm * median_pe


def mean_reversion_price_ev(
    ebitda_cr: float | None, median_ev_ebitda: float | None, net_debt_cr: float | None,
    shares_cr: float | None,
) -> float | None:
    """EV/EBITDA variant for low-EPS companies."""
    if None in (ebitda_cr, median_ev_ebitda, net_debt_cr, shares_cr) or not shares_cr or ebitda_cr <= 0:  # type: ignore[operator]
        return None
    eq = ebitda_cr * median_ev_ebitda - net_debt_cr  # type: ignore[operator]
    return eq / shares_cr if eq > 0 else None
