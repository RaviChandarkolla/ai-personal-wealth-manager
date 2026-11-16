"""Relative valuation model for equities (FA Course Notes Section 10)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from wealth_advisor.data.stocks.provider import (
    StockFinancialYear,
    StockSectorMedian,
    StockSnapshot,
)

NON_BANK_DEFAULT_WEIGHTS: dict[str, float] = {
    "ev_ebitda": 0.30,
    "ps": 0.30,
    "pe": 0.30,
    "pb": 0.10,
}

BANK_DEFAULT_WEIGHTS: dict[str, float] = {
    "pb": 0.60,
    "pe": 0.40,
}


@dataclass
class ValuationComponent:
    """Single valuation multiple component in the weighted relative valuation model."""

    method: str
    label: str
    weight: float
    effective_weight: float
    stock_multiple: float | None
    sector_median_multiple: float | None
    implied_price: float | None
    weighted_contribution: float | None
    applicable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StockRelativeValuation:
    """Output of the multi-multiple relative valuation model."""

    symbol: str
    is_bank: bool
    cmp: float | None
    components: list[ValuationComponent]
    weighted_fair_value: float | None
    fair_value_low: float | None
    fair_value_high: float | None
    expected_return_low_pct: float | None
    expected_return_high_pct: float | None
    verdict: str  # "undervalued" | "fairly_valued" | "overvalued" | "insufficient_data"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "is_bank": self.is_bank,
            "cmp": self.cmp,
            "components": [c.to_dict() for c in self.components],
            "weighted_fair_value": self.weighted_fair_value,
            "fair_value_low": self.fair_value_low,
            "fair_value_high": self.fair_value_high,
            "expected_return_low_pct": self.expected_return_low_pct,
            "expected_return_high_pct": self.expected_return_high_pct,
            "verdict": self.verdict,
        }


def compute_weighted_valuation_from_implied_prices(
    *,
    symbol: str,
    cmp: float | None,
    implied_prices: dict[str, float | None],
    stock_multiples: dict[str, float | None] | None = None,
    sector_multiples: dict[str, float | None] | None = None,
    weights: dict[str, float] | None = None,
    is_bank: bool = False,
    range_Place_multiplier: float = 1.20,
) -> StockRelativeValuation:
    """Compute weighted fair value, [1.0x, 1.2x] range, and expected return from implied prices."""
    default_weights = BANK_DEFAULT_WEIGHTS if is_bank else NON_BANK_DEFAULT_WEIGHTS
    raw_weights = dict(weights) if weights is not None else dict(default_weights)
    s_mult = stock_multiples or {}
    sec_mult = sector_multiples or {}

    method_labels = {
        "ev_ebitda": "EV / EBITDA",
        "ps": "Price to Sales (P/S)",
        "pe": "Price to Earnings (P/E)",
        "pb": "Price to Book (P/B)",
    }
    ordered_methods = ["ev_ebitda", "ps", "pe", "pb"]

    available_weight_sum = sum(
        raw_weights.get(m, 0.0)
        for m in ordered_methods
        if implied_prices.get(m) is not None
        and (implied_prices.get(m) or 0.0) > 0
        and raw_weights.get(m, 0.0) > 0
    )

    components: list[ValuationComponent] = []
    weighted_sum = 0.0
    has_valid = available_weight_sum > 0

    for m in ordered_methods:
        applicable = not (is_bank and m in ("ev_ebitda", "ps"))
        w = raw_weights.get(m, 0.0) if applicable else 0.0
        imp = implied_prices.get(m) if applicable else None
        if imp is not None and imp <= 0:
            imp = None

        eff_w = (w / available_weight_sum) if (has_valid and imp is not None and w > 0) else 0.0
        contrib = (imp * eff_w) if (imp is not None and eff_w > 0) else None
        if contrib is not None:
            weighted_sum += contrib

        components.append(
            ValuationComponent(
                method=m,
                label=method_labels[m],
                weight=round(w, 4),
                effective_weight=round(eff_w, 4),
                stock_multiple=s_mult.get(m),
                sector_median_multiple=sec_mult.get(m),
                implied_price=round(imp, 2) if imp is not None else None,
                weighted_contribution=round(contrib, 2) if contrib is not None else None,
                applicable=applicable,
            )
        )

    if not has_valid or weighted_sum <= 0:
        return StockRelativeValuation(
            symbol=symbol,
            is_bank=is_bank,
            cmp=cmp,
            components=components,
            weighted_fair_value=None,
            fair_value_low=None,
            fair_value_high=None,
            expected_return_low_pct=None,
            expected_return_high_pct=None,
            verdict="insufficient_data",
        )

    fv_low = round(weighted_sum, 2)
    fv_high = round(weighted_sum * range_Place_multiplier, 2)

    exp_low: float | None = None
    exp_high: float | None = None
    verdict = "insufficient_data"

    if cmp is not None and cmp > 0:
        exp_low = round(((fv_low - cmp) / cmp) * 100.0, 2)
        exp_high = round(((fv_high - cmp) / cmp) * 100.0, 2)
        if cmp < fv_low:
            verdict = "undervalued"
        elif cmp <= fv_high:
            verdict = "fairly_valued"
        else:
            verdict = "overvalued"

    return StockRelativeValuation(
        symbol=symbol,
        is_bank=is_bank,
        cmp=cmp,
        components=components,
        weighted_fair_value=fv_low,
        fair_value_low=fv_low,
        fair_value_high=fv_high,
        expected_return_low_pct=exp_low,
        expected_return_high_pct=exp_high,
        verdict=verdict,
    )


def compute_relative_valuation(
    snapshot: StockSnapshot,
    sector_median: StockSectorMedian | None,
    *,
    latest_fy: StockFinancialYear | None = None,
    is_bank: bool = False,
    weights: dict[str, float] | None = None,
) -> StockRelativeValuation:
    """Compute relative valuation for a stock against its sector median multiples."""
    cmp = snapshot.price
    stock_multiples: dict[str, float | None] = {
        "ev_ebitda": snapshot.ev_ebitda,
        "ps": snapshot.ps,
        "pe": snapshot.pe,
        "pb": snapshot.pb,
    }
    sector_multiples: dict[str, float | None] = {
        "ev_ebitda": sector_median.median_ev_ebitda if sector_median else None,
        "ps": sector_median.median_ps if sector_median else None,
        "pe": sector_median.median_pe if sector_median else None,
        "pb": sector_median.median_pb if sector_median else None,
    }

    implied_prices: dict[str, float | None] = {
        "ev_ebitda": None,
        "ps": None,
        "pe": None,
        "pb": None,
    }

    if cmp is not None and cmp > 0 and sector_median is not None:
        # P/E implied price: EPS * Median P/E == CMP * (Median P/E / Stock P/E)
        if (
            snapshot.pe is not None
            and snapshot.pe > 0
            and sector_median.median_pe is not None
            and sector_median.median_pe > 0
        ):
            implied_prices["pe"] = cmp * (sector_median.median_pe / snapshot.pe)
        elif (
            latest_fy is not None
            and latest_fy.eps is not None
            and latest_fy.eps > 0
            and sector_median.median_pe is not None
            and sector_median.median_pe > 0
        ):
            implied_prices["pe"] = latest_fy.eps * sector_median.median_pe

        # P/B implied price: BVPS * Median P/B == CMP * (Median P/B / Stock P/B)
        if (
            snapshot.pb is not None
            and snapshot.pb > 0
            and sector_median.median_pb is not None
            and sector_median.median_pb > 0
        ):
            implied_prices["pb"] = cmp * (sector_median.median_pb / snapshot.pb)
        elif (
            latest_fy is not None
            and latest_fy.bvps is not None
            and latest_fy.bvps > 0
            and sector_median.median_pb is not None
            and sector_median.median_pb > 0
        ):
            implied_prices["pb"] = latest_fy.bvps * sector_median.median_pb

        if not is_bank:
            # P/S implied price
            if (
                snapshot.ps is not None
                and snapshot.ps > 0
                and sector_median.median_ps is not None
                and sector_median.median_ps > 0
            ):
                implied_prices["ps"] = cmp * (sector_median.median_ps / snapshot.ps)

            # EV/EBITDA implied price
            if (
                snapshot.ev_ebitda is not None
                and snapshot.ev_ebitda > 0
                and sector_median.median_ev_ebitda is not None
                and sector_median.median_ev_ebitda > 0
            ):
                if (
                    latest_fy is not None
                    and latest_fy.ebitda_cr is not None
                    and latest_fy.ebitda_cr > 0
                    and snapshot.market_cap_cr is not None
                    and snapshot.market_cap_cr > 0
                ):
                    debt = latest_fy.total_debt_cr or 0.0
                    cash = latest_fy.cash_and_equivalents_cr or 0.0
                    implied_ev = latest_fy.ebitda_cr * sector_median.median_ev_ebitda
                    implied_mcap = implied_ev - debt + cash
                    if implied_mcap > 0:
                        implied_prices["ev_ebitda"] = cmp * (implied_mcap / snapshot.market_cap_cr)
                    else:
                        implied_prices["ev_ebitda"] = cmp * (
                            sector_median.median_ev_ebitda / snapshot.ev_ebitda
                        )
                else:
                    implied_prices["ev_ebitda"] = cmp * (
                        sector_median.median_ev_ebitda / snapshot.ev_ebitda
                    )

    return compute_weighted_valuation_from_implied_prices(
        symbol=snapshot.symbol,
        cmp=cmp,
        implied_prices=implied_prices,
        stock_multiples=stock_multiples,
        sector_multiples=sector_multiples,
        weights=weights,
        is_bank=is_bank,
    )
