"""
Shared opportunity math.

Season averages estimate rates and conversion. The projected mean is
opportunities tonight times that conversion, after pace, usage, and a
partial matchup adjustment. A rate prior keeps a noisy tracking number
from replacing the player's history entirely.
"""

from __future__ import annotations

from typing import Optional

from domain.constants import LEAGUE_AVG_DEF_EFF, LEAGUE_AVG_PACE
from domain.entities import TeamDefense
from utils.math_helpers import clamp

# Share of an opportunity product that uses the measured conversion.
# The rest uses a stable prior, so raising chances does not fully cancel
# through conversion = makes / chances.
OBSERVED_CONVERSION_WEIGHT: float = 0.75
# Share of the final mean taken from opportunities. The rest is the
# minute-scaled rate prior, which regresses noisy tracking.
OPPORTUNITY_MEAN_WEIGHT: float = 0.80
LEAGUE_POINTS_PER_FGA: float = 1.15
ASSIST_CONVERSION_PRIOR: float = 0.50
REBOUND_SECURE_PRIOR: float = 0.48
PACE_FALLBACK_STD_MULT: float = 1.08


def resolve_pace(defense: Optional[TeamDefense]) -> tuple[float, bool]:
    """
    Return (pace, is_fallback).

    A missing defense object or an explicit fallback flag is not treated
    as a measured opponent pace.
    """
    if defense is None or defense.pace <= 0 or getattr(defense, "pace_is_fallback", False):
        pace = defense.pace if defense is not None and defense.pace > 0 else LEAGUE_AVG_PACE
        return pace, True
    return float(defense.pace), False


def regressed_rate(observed: float, prior: float, observed_weight: float = OBSERVED_CONVERSION_WEIGHT) -> float:
    """Blend a measured rate toward a prior. Weight is on the observation."""
    weight = clamp(observed_weight, 0.0, 1.0)
    return weight * observed + (1.0 - weight) * prior


def partial_factor(raw_factor: float, weight: float) -> float:
    """Move only `weight` of the way from 1.0 to `raw_factor`."""
    if raw_factor <= 0:
        return 1.0
    return 1.0 + (raw_factor - 1.0) * clamp(weight, 0.0, 1.0)


def blend_opportunity(opportunity_mean: float, rate_prior: float) -> float:
    """80% opportunity product, 20% minute-scaled rate prior."""
    return (
        OPPORTUNITY_MEAN_WEIGHT * opportunity_mean
        + (1.0 - OPPORTUNITY_MEAN_WEIGHT) * rate_prior
    )


def widen_std_for_pace_fallback(projection, is_fallback: bool) -> None:
    """League-average pace is a real number, but it is not this opponent."""
    if not is_fallback:
        return
    if projection.dist_std > 0:
        projection.dist_std *= PACE_FALLBACK_STD_MULT
    flags = projection.projection_audit_flags
    if "pace_fallback_uncertainty" not in flags:
        flags.append("pace_fallback_uncertainty")


# pace_source set when matchup adjustments are off and pace is deliberately held
# at the league average (Model A). It is not a measured opponent pace.
NEUTRAL_PACE_SOURCE = "neutral_for_model_a"


def pace_label(pace: float, is_fallback: bool, source: str = "") -> str:
    if source == NEUTRAL_PACE_SOURCE:
        return (
            f"Opponent pace: held at the league average {pace:.1f}. "
            "Matchup adjustments are off (Model A), so this opponent's measured pace is not used."
        )
    if is_fallback:
        return (
            f"Opponent pace: {pace:.1f} (league-average fallback; "
            "BALLDONTLIE team pace missing). Variance widened."
        )
    band = "high" if pace > 102 else "low" if pace < 98 else "near the league average"
    return f"Opponent pace: {pace:.1f} (BALLDONTLIE team pace, {band})."


def defense_rating_factor(defensive_efficiency: float) -> float:
    if defensive_efficiency <= 0:
        return 1.0
    return clamp(defensive_efficiency / LEAGUE_AVG_DEF_EFF, 0.80, 1.20)
