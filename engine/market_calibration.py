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
    haircut = 1.0
    if gap > MARKET_DISAGREE_HARD:
        haircut = CALIBRATION_HAIRCUT_HARD
        warnings.append("model_vs_market_gap_hard")
    elif gap > MARKET_DISAGREE_SOFT:
        haircut = CALIBRATION_HAIRCUT_SOFT
        warnings.append("model_vs_market_gap_soft")

    # Long-shot prices: be extra conservative when claiming edge.
    if american_odds >= 220 and gap > 0.10:
        haircut *= 0.96
        warnings.append("long_odds_conservative")

    blended = true_prob * haircut + implied * (1.0 - haircut) * 0.35
    out = clamp(blended, 0.001, 0.999)
    return out, warnings
