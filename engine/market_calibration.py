"""
Compare model true probability to market implied (raw / vig-included); apply haircuts and warnings.
"""

from __future__ import annotations

from domain.constants import (
    CALIBRATION_HAIRCUT_HARD,
    CALIBRATION_HAIRCUT_SOFT,
    MARKET_DISAGREE_HARD,
    MARKET_DISAGREE_SOFT,
)
from utils.math_helpers import clamp


def calibrate_true_probability(
    true_prob: float,
    implied: float,
    american_odds: int,
) -> tuple[float, list[str]]:
    """
    If the model disagrees sharply with the market, shrink toward implied and record warnings.
    """
    warnings: list[str] = []
    gap = true_prob - implied
    if gap > MARKET_DISAGREE_HARD:
        warnings.append("model_vs_market_gap_hard")
    elif gap > MARKET_DISAGREE_SOFT:
        warnings.append("model_vs_market_gap_soft")

    # This smooth monotonic transform avoids hard-threshold probability
    # reversals: d(gap / (1 + alpha * abs(gap))) / d(gap) remains positive.
    alpha = 3.0
    if american_odds >= 220 and gap > 0.10:
        alpha = 4.0
        warnings.append("long_odds_conservative")

    out = clamp(implied + gap / (1.0 + alpha * abs(gap)), 0.001, 0.999)
    return out, warnings
