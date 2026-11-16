"""Position sizing within a bucket."""

import math
from collections.abc import Mapping

from pydantic import BaseModel

from ..config import Settings, get_settings


class Candidate(BaseModel):
    id: str
    score: float  # 0-100


class Position(BaseModel):
    id: str
    weight: float  # percent of whole portfolio
    amount: float  # rupees, rounded down to min_ticket


def size_positions(
    shortlist: list[Candidate],
    bucket_weight: float,
    total_amount: float,
    s: Settings | None = None,
) -> list[Position]:
    """Split `bucket_weight` across top-N candidates proportional to score, capped per instrument.

    Excess above the cap is redistributed; weight that cannot be placed stays unallocated.
    Example: scores 80/20, bucket 40, cap 30 -> A capped at 30, B gets 10.
    """
    s = s or get_settings()
    top = sorted(shortlist, key=lambda c: (-c.score, c.id))[: s.max_instruments_per_bucket]
    top = [c for c in top if c.score > 0]
    cap = s.max_weight_per_instrument
    weights: dict[str, float] = {}
    remaining = bucket_weight
    active = list(top)
    while active and remaining > 1e-9:
        total_score = sum(c.score for c in active)
        capped = [c for c in active if remaining * c.score / total_score >= cap - weights.get(c.id, 0) - 1e-12]
        if not capped:
            for c in active:
                weights[c.id] = weights.get(c.id, 0) + remaining * c.score / total_score
            remaining = 0
            break
        for c in capped:
            room = cap - weights.get(c.id, 0)
            weights[c.id] = weights.get(c.id, 0) + room
            remaining -= room
            active.remove(c)

    out = []
    for c in top:
        w = weights.get(c.id, 0.0)
        raw = total_amount * w / 100
        amount = math.floor(raw / s.min_ticket + 1e-9) * s.min_ticket
        out.append(Position(id=c.id, weight=round(w, 6), amount=amount))
    return out


def size_stock_sleeve(
    shortlist: list[Candidate],
    sectors: Mapping[str, str],
    sleeve_weight: float,
    total_amount: float,
    s: Settings | None = None,
    max_stocks: int | None = None,
) -> list[Position]:
    """Size direct equity candidates within `sleeve_weight`.

    Enforces:
    - Per-stock cap: `s.max_weight_per_stock` (% of total portfolio, default 10%).
    - Per-sector cap: `s.max_sector_pct_of_stock_sleeve` (% of `sleeve_weight`, default 40%).
    - Up to `max_stocks` positions, diversified across sectors when available.
    """
    s = s or get_settings()
    max_stocks = s.max_stocks if max_stocks is None else max_stocks
    if sleeve_weight <= 0 or not shortlist:
        return []

    ranked = [c for c in sorted(shortlist, key=lambda c: (-c.score, c.id)) if c.score > 0]
    if not ranked:
        return []

    # Select up to max_stocks while preferring sector diversity (first pass max 2 per sector, second pass fill)
    top: list[Candidate] = []
    sec_counts: dict[str, int] = {}
    overflow: list[Candidate] = []
    for c in ranked:
        sec = sectors.get(c.id) or "Unknown"
        if sec_counts.get(sec, 0) < 2:
            top.append(c)
            sec_counts[sec] = sec_counts.get(sec, 0) + 1
            if len(top) >= max_stocks:
                break
        else:
            overflow.append(c)
    if len(top) < max_stocks:
        for c in overflow:
            top.append(c)
            if len(top) >= max_stocks:
                break

    stock_cap = float(s.max_weight_per_stock)
    sector_cap = float(sleeve_weight) * (float(s.max_sector_pct_of_stock_sleeve) / 100.0)

    weights: dict[str, float] = {c.id: 0.0 for c in top}
    sector_weights: dict[str, float] = {}
    remaining = float(sleeve_weight)
    active = list(top)

    while active and remaining > 1e-9:
        total_score = sum(c.score for c in active)
        if total_score <= 0:
            break

        # Check individual stock cap breaches
        indiv_capped = [
            c for c in active
            if remaining * c.score / total_score >= (stock_cap - weights[c.id]) - 1e-12
        ]

        # Check sector cap breaches across active candidates
        active_by_sec: dict[str, list[Candidate]] = {}
        for c in active:
            sec = sectors.get(c.id) or "Unknown"
            active_by_sec.setdefault(sec, []).append(c)

        sec_capped: list[str] = []
        for sec, sec_cands in active_by_sec.items():
            sec_score = sum(c.score for c in sec_cands)
            sec_room = sector_cap - sector_weights.get(sec, 0.0)
            if remaining * sec_score / total_score >= sec_room - 1e-12:
                sec_capped.append(sec)

        if not indiv_capped and not sec_capped:
            for c in active:
                add_w = remaining * c.score / total_score
                weights[c.id] += add_w
                sec = sectors.get(c.id) or "Unknown"
                sector_weights[sec] = sector_weights.get(sec, 0.0) + add_w
            remaining = 0.0
            break

        if sec_capped:
            for sec in sec_capped:
                sec_cands = active_by_sec[sec]
                sec_room = max(0.0, sector_cap - sector_weights.get(sec, 0.0))
                # Distribute sec_room across sec_cands respecting individual stock_cap
                sub_active = list(sec_cands)
                sub_rem = sec_room
                while sub_active and sub_rem > 1e-9:
                    sub_tot = sum(c.score for c in sub_active)
                    sub_ic = [
                        c for c in sub_active
                        if sub_rem * c.score / sub_tot >= (stock_cap - weights[c.id]) - 1e-12
                    ]
                    if not sub_ic:
                        for c in sub_active:
                            add_w = sub_rem * c.score / sub_tot
                            weights[c.id] += add_w
                            sector_weights[sec] = sector_weights.get(sec, 0.0) + add_w
                            remaining -= add_w
                        sub_rem = 0.0
                        break
                    for c in sub_ic:
                        room = max(0.0, stock_cap - weights[c.id])
                        weights[c.id] += room
                        sector_weights[sec] = sector_weights.get(sec, 0.0) + room
                        remaining -= room
                        sub_rem -= room
                        sub_active.remove(c)
                for c in sec_cands:
                    if c in active:
                        active.remove(c)
        else:
            for c in indiv_capped:
                sec = sectors.get(c.id) or "Unknown"
                room = max(0.0, min(stock_cap - weights[c.id], sector_cap - sector_weights.get(sec, 0.0)))
                weights[c.id] += room
                sector_weights[sec] = sector_weights.get(sec, 0.0) + room
                remaining -= room
                active.remove(c)

    out: list[Position] = []
    for c in top:
        w = weights.get(c.id, 0.0)
        if w <= 1e-9:
            continue
        raw = total_amount * w / 100.0
        amount = math.floor(raw / s.min_ticket + 1e-9) * s.min_ticket
        out.append(Position(id=c.id, weight=round(w, 6), amount=amount))
    return out

