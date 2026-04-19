"""
Pre-output self-check: prefer conservative confidence and probabilities when
projection, minutes, variance, directional bias, or market alignment look off.

Runs after market calibration; may shrink true_prob, downgrade confidence, and
append fg_* warning codes (surfaced on PropProbability.calibration_warnings).
"""

from __future__ import annotations

from typing import Optional

from domain.constants import (
    FINAL_GATE_HIGH_EDGE_EDGE,
    FINAL_GATE_IMPLIED_BLEND,
    FINAL_GATE_TRUE_SHRINK,
    LOW_LINE_THRESHOLD,
    PROJECTION_VS_SEASON_OUTLIER_RATIO,
)
from domain.entities import Game, Player, StatProjection
from domain.enums import ConfidenceTier, InjuryStatus, PropSide, PropType
from models.projection_baseline import season_stat_for_prop
from utils.math_helpers import clamp

_VOLATILE_TYPES = frozenset(
    {PropType.THREES, PropType.BLOCKS, PropType.STEALS},
)


def _downgrade_confidence(tier: ConfidenceTier) -> ConfidenceTier:
    order = (
        ConfidenceTier.HIGH,
        ConfidenceTier.MEDIUM,
        ConfidenceTier.LOW,
        ConfidenceTier.VERY_LOW,
    )
    try:
        i = order.index(tier)
    except ValueError:
        return ConfidenceTier.VERY_LOW
    return order[min(i + 1, len(order) - 1)]


def _collect_flags(
    player: Player,
    game: Optional[Game],
    is_home: bool,
    prop_type: PropType,
    side: PropSide,
    line: float,
    projection: StatProjection,
    true_prob: float,
    implied: float,
    edge: float,
    completeness: float,
    american_odds: int,
) -> list[str]:
    flags: list[str] = []
    proj = projection.projected_value
    baseline = projection.baseline_projection or proj
    season = season_stat_for_prop(player, prop_type)
    mpg = max(player.minutes_per_game, 1e-6)
    exp_m = projection.expected_minutes
    std = max(projection.dist_std, 1e-6)
    mean_for_cv = max(abs(proj), 0.25)
    cv = std / mean_for_cv

    blowout = float(getattr(game, "blowout_risk", 0.0) or 0.0) if game else 0.0
    b2b = False
    if game:
        b2b = (is_home and bool(getattr(game, "is_back_to_back_home", False))) or (
            (not is_home) and bool(getattr(game, "is_back_to_back_away", False))
        )

    # 1) Realistic vs season / role
    if season > 0.5:
        ratio = proj / season
        if player.is_starter and ratio > 1.42:
            flags.append("fg_projection_vs_season_high")
        if ratio < 0.68 and player.injury_status == InjuryStatus.ACTIVE:
            flags.append("fg_projection_vs_season_low")
        if abs(proj - season) / season > PROJECTION_VS_SEASON_OUTLIER_RATIO:
            flags.append("fg_projection_vs_season_outlier")
    if baseline > 0.25 and abs(proj - baseline) / baseline > 0.48:
        flags.append("fg_projection_vs_baseline_divergence")

    # 2) Expected minutes vs season (allow B2B / blowout context)
    if exp_m > 0 and mpg > 3:
        r = exp_m / mpg
        if r < 0.74 and player.injury_status == InjuryStatus.ACTIVE and not b2b and blowout < 0.14:
            flags.append("fg_expected_minutes_below_season")
        if r > 1.16 and (not game or not getattr(game, "is_playoff", False)):
            flags.append("fg_expected_minutes_above_season")

    # 3) Variance / low-count props — avoid spurious high certainty
    if prop_type in _VOLATILE_TYPES or line <= LOW_LINE_THRESHOLD:
        if cv < 0.32 and line <= 1.75 and abs(edge) > 0.045:
            flags.append("fg_low_count_or_low_line_variance")
        if line <= 1.25 and true_prob > 0.66 and completeness < 0.75:
            flags.append("fg_low_line_high_prob_sparse_data")

    # 4) Unintentional under bias — strong UNDER edge while season clears the line
    if side == PropSide.UNDER and season > line * 1.02 and edge > 0.045:
        flags.append("fg_under_edge_vs_season_above_line")
    if side == PropSide.UNDER and proj > line - 0.2 and true_prob > 0.60:
        flags.append("fg_tight_line_under_high_prob")

    # 5) Sharp / market alignment — huge disagreement at moderate prices
    gap = abs(true_prob - implied)
    if gap > 0.16 and -220 <= american_odds <= 240:
        flags.append("fg_model_market_gap_mid_odds")
    if 0.47 <= implied <= 0.53 and abs(edge) > 0.075:
        flags.append("fg_near_coinflip_large_edge")

    return flags


def apply_final_calibration_gate(
    player: Player,
    game: Optional[Game],
    is_home: bool,
    prop_type: PropType,
    side: PropSide,
    line: float,
    projection: StatProjection,
    true_prob: float,
    implied: float,
    edge: float,
    confidence: ConfidenceTier,
    completeness: float,
    american_odds: int,
) -> tuple[float, ConfidenceTier, list[str]]:
    """
    If any self-check fails, shrink probability toward 50% and implied,
    downgrade confidence, and return warning codes. High-edge props never
    keep HIGH confidence when flagged.
    """
    flags = _collect_flags(
        player,
        game,
        is_home,
        prop_type,
        side,
        line,
        projection,
        true_prob,
        implied,
        edge,
        completeness,
        american_odds,
    )
    if not flags:
        return true_prob, confidence, []

    t = true_prob
    b = FINAL_GATE_IMPLIED_BLEND
    t = 0.5 + (t - 0.5) * FINAL_GATE_TRUE_SHRINK
    t = t * (1.0 - b) + implied * b
    t = clamp(t, 0.001, 0.999)

    conf = _downgrade_confidence(confidence)
    if edge >= FINAL_GATE_HIGH_EDGE_EDGE and conf == ConfidenceTier.HIGH:
        conf = ConfidenceTier.MEDIUM

    return t, conf, flags
