"""Merge engine for fund-selection rule sets.

Combines Set 1 and Set 2 into Combined according to deterministic strategies:
- Stricter minimum: higher floor wins
- Stricter maximum: lower ceiling wins
- Longer horizon: max horizon per category
- Overlapping range: range intersection; non-overlapping falls back to lower risk
- Union: combine unique items
- Either set: True if either set has it enabled
- Blend: average weights, normalized to sum to 1.0
- One-sided: adopt the set defining the rule
"""

from typing import Any


def _is_num(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def merge_rule_sets(
    a: dict[str, Any], b: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Merge rule set A (Set 1) and B (Set 2).

    Returns (combined_dict, provenance_list).
    """
    combined: dict[str, Any] = {}
    provenance: list[dict[str, Any]] = []

    def record(
        setting: str,
        set1_val: Any,
        set2_val: Any,
        result: Any,
        strategy: str,
        winner: str,
        reason: str,
        conflict: bool = False,
    ) -> None:
        combined[setting] = result
        entry = {
            "setting": setting,
            "set1_value": set1_val,
            "set2_value": set2_val,
            "result": result,
            "strategy": strategy,
            "winner": winner,
            "reason": reason,
        }
        if conflict:
            entry["conflict"] = True
        provenance.append(entry)

    # 1. Stricter minimums
    stricter_mins = [
        ("min_aum_cr", "Higher AUM floor wins"),
        ("min_aum_cr_small", "Higher small-category AUM floor wins"),
        ("min_track_years_equity", "Longer equity track record requirement wins"),
        ("min_track_years_debt", "Longer debt track record requirement wins"),
        ("min_rating", "Higher minimum star rating wins"),
        ("min_manager_tenure_years", "Minimum manager tenure floor"),
        ("buy_min_score", "Higher minimum buy score wins"),
        ("min_confidence", "Higher confidence threshold wins"),
    ]
    for key, desc in stricter_mins:
        v1, v2 = a.get(key), b.get(key)
        if v1 is None and v2 is None:
            continue
        elif v1 is None:
            record(key, v1, v2, v2, "Stricter minimum", "Set 2", f"{desc} (from Set 2)")
        elif v2 is None:
            record(key, v1, v2, v1, "Stricter minimum", "Set 1", f"{desc} (from Set 1)")
        else:
            if v1 > v2:
                record(key, v1, v2, v1, "Stricter minimum", "Set 1", f"{desc} ({v1} >= {v2})")
            elif v2 > v1:
                record(key, v1, v2, v2, "Stricter minimum", "Set 2", f"{desc} ({v2} >= {v1})")
            else:
                record(key, v1, v2, v1, "Stricter minimum", "Both", f"{desc} (equal: {v1})")

    # 2. Stricter maximums
    stricter_maxs = [
        ("equity_cap_horizon_lt_1y", "Tighter equity cap under 1 year wins"),
        ("equity_cap_horizon_lt_3y", "Tighter equity cap under 3 years wins"),
        ("equity_cap_horizon_lt_5y", "Tighter equity cap under 5 years wins"),
        ("equity_cap_low_emergency", "Tighter equity cap for low emergency fund wins"),
        ("max_beta", "Lower beta ceiling wins"),
        ("max_weight_per_instrument", "Lower single-instrument cap wins"),
    ]
    for key, desc in stricter_maxs:
        v1, v2 = a.get(key), b.get(key)
        if v1 is None and v2 is None:
            continue
        elif v1 is None:
            record(key, v1, v2, v2, "Stricter maximum", "Set 2", f"{desc} (from Set 2)")
        elif v2 is None:
            record(key, v1, v2, v1, "Stricter maximum", "Set 1", f"{desc} (from Set 1)")
        else:
            if v1 < v2:
                record(key, v1, v2, v1, "Stricter maximum", "Set 1", f"{desc} ({v1} <= {v2})")
            elif v2 < v1:
                record(key, v1, v2, v2, "Stricter maximum", "Set 2", f"{desc} ({v2} <= {v1})")
            else:
                record(key, v1, v2, v1, "Stricter maximum", "Both", f"{desc} (equal: {v1})")

    # Expense ceilings (dict per category)
    exp1 = a.get("expense_ceilings", {})
    exp2 = b.get("expense_ceilings", {})
    all_exp_cats = sorted(set(exp1) | set(exp2))
    merged_exp: dict[str, float] = {}
    for cat in all_exp_cats:
        e1, e2 = exp1.get(cat), exp2.get(cat)
        if e1 is None:
            merged_exp[cat] = e2
        elif e2 is None:
            merged_exp[cat] = e1
        else:
            merged_exp[cat] = min(e1, e2)
    if merged_exp:
        record(
            "expense_ceilings",
            exp1,
            exp2,
            merged_exp,
            "Stricter maximum",
            "Stricter per category",
            "Lower expense ceiling applied per category",
        )

    # Volatility caps (dict per risk category)
    vol1 = a.get("vol_caps", {})
    vol2 = b.get("vol_caps", {})
    all_vol_cats = sorted(set(vol1) | set(vol2))
    merged_vol: dict[str, float] = {}
    for rcat in all_vol_cats:
        vc1, vc2 = vol1.get(rcat), vol2.get(rcat)
        if vc1 is None:
            merged_vol[rcat] = vc2
        elif vc2 is None:
            merged_vol[rcat] = vc1
        else:
            merged_vol[rcat] = min(vc1, vc2)
    if merged_vol:
        record(
            "vol_caps",
            vol1,
            vol2,
            merged_vol,
            "Stricter maximum",
            "Stricter per profile",
            "Lower volatility cap applied per profile",
        )

    # 3. Longer horizon (category_min_horizons)
    horiz1 = a.get("category_min_horizons", {})
    horiz2 = b.get("category_min_horizons", {})
    all_horiz_cats = sorted(set(horiz1) | set(horiz2))
    merged_horizons: dict[str, float] = {}
    for cat in all_horiz_cats:
        h1 = horiz1.get(cat)
        h2 = horiz2.get(cat)
        if h1 is None:
            merged_horizons[cat] = h2
        elif h2 is None:
            merged_horizons[cat] = h1
        else:
            merged_horizons[cat] = max(h1, h2)
    record(
        "category_min_horizons",
        horiz1,
        horiz2,
        merged_horizons,
        "Longer horizon",
        "Higher per category",
        "Longer minimum holding horizon per fund category",
    )

    # 4. Overlapping range (allocation_bands)
    bands1 = a.get("allocation_bands", {})
    bands2 = b.get("allocation_bands", {})
    merged_bands: dict[str, dict[str, tuple[float, float]]] = {}
    has_band_conflict = False
    conflict_details: list[str] = []

    for prof in ("Conservative", "Moderate", "Aggressive"):
        p1 = bands1.get(prof, {})
        p2 = bands2.get(prof, {})
        merged_bands[prof] = {}
        for bkt in ("equity", "debt", "gold", "alternatives"):
            r1 = p1.get(bkt, (0.0, 0.0))
            r2 = p2.get(bkt, (0.0, 0.0))
            lo1, hi1 = r1
            lo2, hi2 = r2
            ov_lo = max(lo1, lo2)
            ov_hi = min(hi1, hi2)
            if ov_lo <= ov_hi:
                merged_bands[prof][bkt] = (float(ov_lo), float(ov_hi))
            else:
                # Ranges do not overlap: fallback to lower-risk side
                has_band_conflict = True
                if bkt == "debt":
                    # For debt, higher allocation is lower risk
                    fallback = r1 if r1[0] >= r2[0] else r2
                    source_set = "Set 1" if r1[0] >= r2[0] else "Set 2"
                else:
                    # For equity/gold/alternatives, lower allocation is lower risk
                    fallback = r1 if r1[1] <= r2[1] else r2
                    source_set = "Set 1" if r1[1] <= r2[1] else "Set 2"
                merged_bands[prof][bkt] = (float(fallback[0]), float(fallback[1]))
                conflict_details.append(f"{prof} {bkt}: non-overlapping {r1} vs {r2}, used lower-risk {source_set}")

    record(
        "allocation_bands",
        bands1,
        bands2,
        merged_bands,
        "Overlapping range",
        "Conflict (lower risk)" if has_band_conflict else "Overlap",
        "; ".join(conflict_details) if has_band_conflict else "Intersection of allocation ranges for all profiles",
        conflict=has_band_conflict,
    )

    # 5. Union: allowed_categories, trusted_amcs
    cats1 = a.get("allowed_categories", [])
    cats2 = b.get("allowed_categories", [])
    merged_cats = list(dict.fromkeys(list(cats1) + list(cats2)))
    record(
        "allowed_categories",
        cats1,
        cats2,
        merged_cats,
        "Union",
        "Union of both",
        f"All {len(merged_cats)} categories from both sets are available",
    )

    amcs1 = a.get("trusted_amcs", [])
    amcs2 = b.get("trusted_amcs", [])
    merged_amcs = list(dict.fromkeys(list(amcs1) + list(amcs2)))
    record(
        "trusted_amcs",
        amcs1,
        amcs2,
        merged_amcs,
        "Union",
        "Set 2" if amcs2 and not amcs1 else "Union of both",
        f"Trusted AMCs eligible for bonus: {', '.join(merged_amcs) if merged_amcs else 'None'}",
    )

    # 6. Either set (booleans)
    either_rules = [
        ("require_direct_plan", "Direct plan required"),
        ("require_positive_alpha", "Positive alpha required for actively managed equity"),
        ("match_maturity_to_horizon", "Debt fund maturity matched to goal horizon"),
    ]
    for key, desc in either_rules:
        b1, b2 = bool(a.get(key)), bool(b.get(key))
        res = b1 or b2
        winner = "Both" if (b1 and b2) else ("Set 1" if b1 else ("Set 2" if b2 else "Neither"))
        record(key, b1, b2, res, "Either set", winner, f"{desc} (rule active if enabled in either set)")

    # 7. Blend: analyst_weights, fundamental_weights
    aw1 = a.get("analyst_weights", {})
    aw2 = b.get("analyst_weights", {})
    if aw1 or aw2:
        keys = sorted(set(aw1) | set(aw2))
        avg_weights = {k: (float(aw1.get(k, 0.0)) + float(aw2.get(k, 0.0))) / 2.0 for k in keys}
        tot = sum(avg_weights.values()) or 1.0
        blended_aw = {k: round(v / tot, 4) for k, v in avg_weights.items()}
        record(
            "analyst_weights",
            aw1,
            aw2,
            blended_aw,
            "Blend",
            "Average of both",
            "Average of Set 1 and Set 2 analyst weights, normalized to sum to 1.0",
        )

    fw1 = a.get("fundamental_weights", {})
    fw2 = b.get("fundamental_weights", {})
    if fw1 or fw2:
        keys = sorted(set(fw1) | set(fw2))
        avg_f_weights = {k: (float(fw1.get(k, 0.0)) + float(fw2.get(k, 0.0))) / 2.0 for k in keys}
        tot_f = sum(avg_f_weights.values()) or 1.0
        blended_fw = {k: round(v / tot_f, 4) for k, v in avg_f_weights.items()}
        record(
            "fundamental_weights",
            fw1,
            fw2,
            blended_fw,
            "Blend",
            "Average of both",
            "Average of Set 1 and Set 2 fundamental score weights, normalized to sum to 1.0",
        )

    # 8. One-sided rules
    one_sided = [
        ("ytm_outlier_pp", "YTM outlier credit risk check threshold"),
        ("trusted_amc_bonus", "Score bonus for trusted AMC funds"),
    ]
    for key, desc in one_sided:
        v1, v2 = a.get(key), b.get(key)
        if v1 is not None and v2 is None:
            record(key, v1, v2, v1, "One-sided", "Set 1", f"{desc} (from Set 1)")
        elif v2 is not None and v1 is None:
            record(key, v1, v2, v2, "One-sided", "Set 2", f"{desc} (from Set 2)")
        elif v1 is not None and v2 is not None:
            # take non-zero or v2
            res = v2 if v2 else v1
            record(key, v1, v2, res, "One-sided", "Set 2" if res == v2 else "Set 1", f"{desc}")

    return combined, provenance
