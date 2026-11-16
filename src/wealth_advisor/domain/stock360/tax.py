"""Section 17: capital-gains tax on a sell decision (rates come from config)."""

from __future__ import annotations

from datetime import date


def tax_on_sale(
    buy_price: float, sell_price: float, qty: float, buy_date: date, sell_date: date,
    stcg_pct: float = 20.0, ltcg_pct: float = 12.5, exemption: float = 125000.0,
) -> dict[str, float | str]:
    gain = (sell_price - buy_price) * qty
    held = (sell_date - buy_date).days
    long_term = held > 365
    if gain <= 0:
        tax = 0.0
    elif long_term:
        tax = max(gain - exemption, 0.0) * ltcg_pct / 100
    else:
        tax = gain * stcg_pct / 100
    return {"gain": round(gain, 2), "days_held": held, "type": "LTCG" if long_term else "STCG",
            "tax": round(tax, 2), "days_to_ltcg": max(366 - held, 0)}


def wait_for_ltcg(
    cost_total: float, value_total: float, buy_date: date, today: date,
    stcg_pct: float = 20.0, ltcg_pct: float = 12.5, exemption: float = 125000.0,
) -> dict[str, float | str | None]:
    """Tax saving from waiting until LTCG applies (price unchanged) and the % fall in price
    that would wipe that saving out (break-even fall)."""
    gain = value_total - cost_total
    days = max(366 - (today - buy_date).days, 0)
    if gain <= 0 or days == 0 or value_total <= 0:
        return {"tax_now": 0.0 if gain <= 0 else round(gain * stcg_pct / 100, 2) if days else
                round(max(gain - exemption, 0) * ltcg_pct / 100, 2),
                "saving_by_waiting": 0.0, "break_even_fall_pct": None, "days_to_ltcg": days}
    now = gain * stcg_pct / 100
    later = max(gain - exemption, 0.0) * ltcg_pct / 100
    saving = now - later
    return {"tax_now": round(now, 2), "tax_after_ltcg": round(later, 2),
            "saving_by_waiting": round(saving, 2),
            "break_even_fall_pct": round(saving / value_total * 100, 2), "days_to_ltcg": days}


def hypothetical_position(
    cmp_: float, today: date, position_inr: float = 100000.0, assumed_gain_pct: float = 20.0,
    held_days: int = 180, **rates: float,
) -> dict[str, float | str | None]:
    """When the user gave no cost/date: a labelled assumption (₹1 lakh, +20% gain, 180 days held)."""
    from datetime import timedelta

    value = position_inr
    cost = value / (1 + assumed_gain_pct / 100)
    out = wait_for_ltcg(cost, value, today - timedelta(days=held_days), today, **rates)
    out["assumption"] = f"₹{position_inr:,.0f} position, +{assumed_gain_pct:.0f}% gain, held {held_days} days"
    return out
