"""Deterministic analyst sub-scores (0-100) and confidence from a fund's facts (plan 1.4).

`facts` maps fact name -> value for ONE fund (e.g. {"expense_ratio": 0.45, "sharpe": 1.1}).
"""

import math
from collections.abc import Mapping

from ..config import Settings, get_settings
from .categories import CATEGORIES, min_horizon

REQUIRED_FACTS = (
    "expense_ratio",
    "aum_cr",
    "roll_3y_median",
    "cagr_5y",
    "volatility",
    "max_drawdown",
    "sharpe",
)


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def fundamental_score(
    facts: Mapping[str, float | str], s: Settings | None = None
) -> float:
    """Weighted sum of available fundamental metrics according to s.fundamental_weights.

    Metrics include: 3y rolling percentile, 5y CAGR percentile, Sharpe ratio,
    max drawdown, alpha score, and low-volatility percentile.
    Missing metrics are skipped and remaining weights are re-normalized;
    returns neutral 50.0 if no metrics are available.
    """
    s = s or get_settings()
    component_scores: dict[str, float] = {}

    for k in ("pct_roll_3y", "pct_cagr_5y"):
        val = facts.get(k)
        if isinstance(val, (int, float)):
            component_scores[k] = _clamp(float(val))

    sharpe_val = facts.get("sharpe")
    if isinstance(sharpe_val, (int, float)):
        component_scores["sharpe"] = _clamp((float(sharpe_val) + 0.5) / 2.5 * 100)

    dd_val = facts.get("max_drawdown")
    if isinstance(dd_val, (int, float)):
        component_scores["max_drawdown"] = _clamp(100 + float(dd_val) * 2)

    alpha_val = facts.get("alpha")
    if isinstance(alpha_val, (int, float)):
        # Alpha around 0 -> 50, +5% -> 100, -5% -> 0
        component_scores["alpha"] = _clamp(50 + float(alpha_val) * 10)

    low_vol_val = facts.get("pct_low_vol")
    if isinstance(low_vol_val, (int, float)):
        component_scores["pct_low_vol"] = _clamp(float(low_vol_val))

    # M13 asset-class specific fundamental signals (blended when present)
    extra_scores: list[float] = []

    # 13a: Bank FD / Fixed Income (fd_rate_pct, credit_rating)
    fd_rate = facts.get("fd_rate_pct")
    if isinstance(fd_rate, (int, float)):
        # 5.0% -> 40, 7.25% -> 85, 8.5% -> 98
        rate_sc = _clamp(40.0 + (float(fd_rate) - 5.0) * 20.0)
        cr = str(facts.get("credit_rating") or "").upper()
        if cr == "AAA" or cr == "SOV":
            rate_sc = _clamp(rate_sc + 5.0)
        elif cr in ("AA+", "AA"):
            pass
        elif cr and cr not in ("AAA", "SOV", "AA+", "AA"):
            rate_sc = _clamp(rate_sc - 30.0)
        extra_scores.append(rate_sc)

    # 13b: Commodity / SGB (coupon_pct, price_trend_1y)
    coupon = facts.get("coupon_pct")
    if isinstance(coupon, (int, float)):
        # SGB 2.5% coupon + gold trend
        c_sc = _clamp(60.0 + float(coupon) * 12.0)
        trend_1y = facts.get("price_trend_1y")
        if isinstance(trend_1y, (int, float)):
            c_sc = _clamp(0.6 * c_sc + 0.4 * _clamp(50.0 + float(trend_1y) * 2.5))
        extra_scores.append(c_sc)

    # 13c: Direct Equity (pe_ratio vs sector_pe, earnings_growth_yoy, debt_to_equity, governance)
    pe = facts.get("pe_ratio")
    sec_pe = facts.get("sector_pe")
    if isinstance(pe, (int, float)) and isinstance(sec_pe, (int, float)) and float(sec_pe) > 0:
        rel_pe = float(pe) / float(sec_pe)
        # rel_pe = 0.8 -> 85, 1.0 -> 70, 1.5 -> 35
        val_sc = _clamp(70.0 + (1.0 - rel_pe) * 70.0)
        eg = facts.get("earnings_growth_yoy")
        if isinstance(eg, (int, float)):
            eg_sc = _clamp(50.0 + float(eg) * 2.0)
            val_sc = 0.5 * val_sc + 0.5 * eg_sc
        de = facts.get("debt_to_equity")
        if isinstance(de, (int, float)):
            de_sc = _clamp(90.0 - float(de) * 35.0)
            val_sc = 0.7 * val_sc + 0.3 * de_sc
        pledge = facts.get("promoter_pledge_pct")
        if isinstance(pledge, (int, float)) and float(pledge) > 0:
            val_sc = _clamp(val_sc - float(pledge) * 1.0)
        insider = str(facts.get("insider_activity") or "").lower()
        if insider == "net_buy":
            val_sc = _clamp(val_sc + 5.0)
        elif insider == "net_sell":
            val_sc = _clamp(val_sc - 10.0)
        extra_scores.append(_clamp(val_sc))

    # 13d: Alternatives REIT/InvIT (distribution_yield, occupancy_pct, nav_discount_pct, leverage_pct)
    dist_yield = facts.get("distribution_yield")
    if isinstance(dist_yield, (int, float)):
        # 6% yield -> 70, 8% yield -> 90
        alt_sc = _clamp(40.0 + float(dist_yield) * 6.0)
        occ = facts.get("occupancy_pct")
        if isinstance(occ, (int, float)):
            occ_sc = _clamp((float(occ) - 60.0) * 2.2)
            alt_sc = 0.5 * alt_sc + 0.5 * occ_sc
        disc = facts.get("nav_discount_pct")
        if isinstance(disc, (int, float)):
            alt_sc = _clamp(alt_sc + float(disc) * 1.0)
        lev = facts.get("leverage_pct")
        if isinstance(lev, (int, float)) and float(lev) > 35.0:
            alt_sc = _clamp(alt_sc - (float(lev) - 35.0) * 1.5)
        extra_scores.append(_clamp(alt_sc))

    if not component_scores and not extra_scores:
        return 50.0

    weights = s.fundamental_weights if s and s.fundamental_weights else {}
    weighted_sum = 0.0
    weight_total = 0.0
    for key, score in component_scores.items():
        w = float(weights.get(key, 0.0))
        if w > 0:
            weighted_sum += w * score
            weight_total += w

    base_score: float | None = None
    if weight_total > 0:
        base_score = _clamp(weighted_sum / weight_total)
    elif component_scores:
        base_score = _clamp(sum(component_scores.values()) / len(component_scores))

    if extra_scores:
        extra_mean = sum(extra_scores) / len(extra_scores)
        if base_score is None:
            return round(_clamp(extra_mean), 4)
        return round(_clamp(0.5 * base_score + 0.5 * extra_mean), 4)

    return round(base_score if base_score is not None else 50.0, 4)


def fit_score(
    facts: Mapping[str, float | str],
    category: str,
    risk_category: str,
    horizon_years: float,
    s: Settings | None = None,
) -> float:
    """100 if volatility is within the risk category's cap; otherwise 100 minus 200x the relative
    excess (floor 0); halved if the horizon is shorter than the category's minimum; penalised if
    beta exceeds max_beta ceiling.
    """
    s = s or get_settings()
    cap = s.vol_caps[risk_category]
    score = 100.0
    vol = facts.get("volatility")
    if isinstance(vol, (int, float)):
        excess = max(0.0, float(vol) / 100 - cap)
        score = _clamp(100 - excess / cap * 200)

    min_h = min_horizon(category, s)
    if horizon_years < min_h:
        score *= 0.5

    if s.max_beta is not None:
        beta = facts.get("beta")
        if isinstance(beta, (int, float)) and beta > s.max_beta:
            beta_excess = float(beta) - s.max_beta
            score = _clamp(score - beta_excess * 50)

    return round(score, 4)


def liquidity_cost_score(
    facts: Mapping[str, float | str], category: str, s: Settings | None = None
) -> float:
    """Mean of expense score (50 at the ceiling, 100 at zero) and AUM score (100 Cr -> 0,
    10,000 Cr -> 100), minus 20 for regular plans, plus trusted AMC bonus (capped at 100).
    """
    s = s or get_settings()
    parts: list[float] = []
    er = facts.get("expense_ratio")
    if isinstance(er, (int, float)):
        ceiling = s.expense_ceilings.get(category, s.default_expense_ceiling)
        parts.append(_clamp(100 * (1 - float(er) / (2 * ceiling))))
    aum = facts.get("aum_cr")
    if isinstance(aum, (int, float)) and aum > 0:
        parts.append(_clamp(math.log10(float(aum) / 100) / 2 * 100))
    te = facts.get("tracking_error")
    if isinstance(te, (int, float)):
        parts.append(_clamp(100.0 - float(te) * 80.0))
    score = _mean(parts)
    score = 50.0 if score is None else score
    if facts.get("plan") == "regular":
        score -= 20

    ew_pen = facts.get("early_withdrawal_penalty")
    if isinstance(ew_pen, (int, float)):
        score -= float(ew_pen) * 15.0

    amc = str(facts.get("amc") or "")
    if amc and s.trusted_amcs and s.trusted_amc_bonus > 0:
        if any(trusted.lower() in amc.lower() for trusted in s.trusted_amcs):
            score += s.trusted_amc_bonus

    return round(_clamp(score), 4)


def news_score(facts: Mapping[str, float | str]) -> float:
    """Deterministic news sub-score (0-100) from tagged news facts; defaults to neutral 50.0."""
    val = facts.get("news_score")
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return round(_clamp(float(val)), 4)
    return 50.0


def sentiment_score(facts: Mapping[str, float | str]) -> float:
    """Deterministic market sentiment sub-score (0-100) from news/sentiment facts; defaults to 50.0."""
    val = facts.get("sentiment_score")
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return round(_clamp(float(val)), 4)
    return 50.0


def detect_conflicts(
    facts: Mapping[str, float | str], category: str, s: Settings | None = None
) -> list[tuple[str, list[str]]]:
    """(description, fact names involved) for contradictory signals; never averaged away."""
    s = s or get_settings()
    out: list[tuple[str, list[str]]] = []
    strong = (
        isinstance(facts.get("pct_roll_3y"), (int, float))
        and float(facts["pct_roll_3y"]) >= 70  # type: ignore[arg-type]
    )
    ceiling = s.expense_ceilings.get(category, s.default_expense_ceiling)
    er = facts.get("expense_ratio")
    if strong and isinstance(er, (int, float)) and er > ceiling:
        out.append(
            (
                "Strong peer-relative returns but expense ratio is above the category ceiling",
                ["pct_roll_3y", "expense_ratio"],
            )
        )
    rating = facts.get("rating")
    if s.min_rating is not None and strong and isinstance(rating, (int, float)) and rating < s.min_rating:
        out.append(
            (
                "Strong peer-relative returns but rating is below the floor",
                ["pct_roll_3y", "rating"],
            )
        )
    dd = facts.get("max_drawdown")
    if strong and isinstance(dd, (int, float)) and dd < -25:
        out.append(
            ("Strong returns came with a deep drawdown", ["pct_roll_3y", "max_drawdown"])
        )
    if facts.get("plan") == "regular":
        out.append(
            ("Regular plan carries a higher expense ratio than the direct plan", ["plan"])
        )

    # Manager tenure check
    tenure = facts.get("manager_tenure_years")
    if (
        s.min_manager_tenure_years is not None
        and isinstance(tenure, (int, float))
        and tenure < s.min_manager_tenure_years
    ):
        out.append(
            (
                f"Manager tenure ({tenure:.1f}y) is below the minimum threshold ({s.min_manager_tenure_years:g}y)",
                ["manager_tenure_years"],
            )
        )

    # YTM outlier credit risk check
    if s.ytm_outlier_pp is not None:
        ytm = facts.get("ytm")
        cat_med = facts.get("category_median_ytm")
        if (
            isinstance(ytm, (int, float))
            and isinstance(cat_med, (int, float))
            and ytm > cat_med + s.ytm_outlier_pp
        ):
            out.append(
                (
                    f"YTM ({ytm:.2f}%) is {ytm - cat_med:.2f} pp above category median ({cat_med:.2f}%): check credit risk",
                    ["ytm"],
                )
            )

    # M13 asset-class conflict checks
    pledge = facts.get("promoter_pledge_pct")
    if isinstance(pledge, (int, float)) and float(pledge) > 10.0:
        out.append(
            (
                f"Promoter pledging ({float(pledge):.1f}%) exceeds 10% governance threshold",
                ["promoter_pledge_pct"],
            )
        )

    pe = facts.get("pe_ratio")
    sec_pe = facts.get("sector_pe")
    if (
        isinstance(pe, (int, float))
        and isinstance(sec_pe, (int, float))
        and float(sec_pe) > 0
        and float(pe) > float(sec_pe) * 1.3
    ):
        out.append(
            (
                f"P/E ratio ({float(pe):.1f}) exceeds sector P/E ({float(sec_pe):.1f}) by >30%",
                ["pe_ratio", "sector_pe"],
            )
        )

    de = facts.get("debt_to_equity")
    if isinstance(de, (int, float)) and float(de) > 1.5:
        out.append(
            (
                f"Debt-to-equity ratio ({float(de):.2f}) exceeds 1.5x leverage threshold",
                ["debt_to_equity"],
            )
        )

    if str(facts.get("insider_activity") or "").lower() == "net_sell":
        out.append(
            ("Recent net insider selling reported in governance filings", ["insider_activity"])
        )

    fd_rate = facts.get("fd_rate_pct")
    cr = str(facts.get("credit_rating") or "").upper()
    if isinstance(fd_rate, (int, float)) and cr and cr not in ("AAA", "SOV", "AA+", "AA"):
        out.append(
            (
                f"High FD rate ({float(fd_rate):.2f}%) paired with sub-AA credit rating ({cr})",
                ["fd_rate_pct", "credit_rating"],
            )
        )

    lev = facts.get("leverage_pct")
    if isinstance(lev, (int, float)) and float(lev) > 40.0:
        out.append(
            (
                f"REIT/InvIT leverage ({float(lev):.1f}%) exceeds 40% threshold",
                ["leverage_pct"],
            )
        )

    return out


def confidence(
    facts: Mapping[str, float | str], n_conflicts: int, n_stale_docs: int = 0
) -> float:
    """Completeness of required facts - 0.1 per conflict - 0.1 per stale doc, clamped 0-1.
    Example: 6 of 7 facts, 1 conflict -> 0.857 - 0.1 = 0.757."""
    have = sum(1 for k in REQUIRED_FACTS if facts.get(k) is not None)
    return round(
        _clamp(have / len(REQUIRED_FACTS) - 0.1 * n_conflicts - 0.1 * n_stale_docs, 0, 1),
        4,
    )

