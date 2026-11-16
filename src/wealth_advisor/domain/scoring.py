from ..config import AnalystWeights, get_settings


def score_instrument(sub_scores: dict[str, float], weights: AnalystWeights | None = None) -> float:
    """Weighted 0-100 score; all sub-scores required and within 0-100.

    Example: all sub-scores 80 -> 80; fundamental 100, rest 0 -> 35.
    """
    w = (weights or get_settings().analyst_weights).model_dump()
    missing = set(w) - set(sub_scores)
    if missing:
        raise ValueError(f"missing sub-scores: {sorted(missing)}")
    if any(not 0 <= sub_scores[k] <= 100 for k in w):
        raise ValueError("sub-scores must be within 0-100")
    return round(sum(sub_scores[k] * w[k] for k in w), 4)
