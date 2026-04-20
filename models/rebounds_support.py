"""
Rebounds-specific projection helpers.

Design goals:
- Minutes × per-minute rebound rate is the core (not raw recent game totals).
- 70/30 season/recent **rate** blend with tight clamps on recent pull.
- Positional rebound defense + mild pace only (no FPA stack for glass).
- Role / rotation / competition multipliers stay conservative.
"""

from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any

from domain.constants import (
    LEAGUE_AVG_PACE,
    PACE_SENSITIVITY,
    REBOUNDS_ENV_BAND_MAX,
    REBOUNDS_ENV_BAND_MIN,
    REBOUNDS_PACE_SENSITIVITY_MULT,
    REBOUNDS_POS_MATCHUP_CLAMP,
    REBOUNDS_RATE_BLEND_RECENT,
    REBOUNDS_RATE_BLEND_SEASON,
    REBOUNDS_RECENT_RPM_MAX_RATIO,
    REBOUNDS_RECENT_RPM_MIN_RATIO,
)
from domain.entities import Player, TeamDefense
from domain.enums import InjuryStatus, PlayerRole, Position, PropType
from models.matchup_model import MatchupModel
from models.projection_baseline import blended_stat_rate
from utils.math_helpers import clamp


def blended_rebound_rates(
    player: Player,
    expected_minutes: float,
) -> tuple[float, float, float, dict[str, Any]]:
    """
    Return (blended_rpm, season_rpm, recent_rpm_clamped, blended_stat_rate detail).

    Blended = 70% season RPM + 30% recent RPM, with recent RPM clamped vs season
    so one spike week cannot dominate.
    """
    _, detail = blended_stat_rate(player, PropType.REBOUNDS, expected_minutes)
    season_rpm = float(detail.get("season_rate_per_minute", 0.0))
    recent_rpm = float(detail.get("recent_rate_per_minute", season_rpm))
    if season_rpm > 1e-9:
        recent_rpm = clamp(
            recent_rpm,
            season_rpm * REBOUNDS_RECENT_RPM_MIN_RATIO,
            season_rpm * REBOUNDS_RECENT_RPM_MAX_RATIO,
        )
    blended = (
        REBOUNDS_RATE_BLEND_SEASON * season_rpm
        + REBOUNDS_RATE_BLEND_RECENT * recent_rpm
    )
    return max(blended, 0.0), season_rpm, recent_rpm, detail


def rebound_pace_multiplier(game_pace: float) -> float:
    """Mild pace effect on rebound opportunities (half of full prop sensitivity)."""
    if game_pace <= 0:
        game_pace = LEAGUE_AVG_PACE
    sens = PACE_SENSITIVITY.get(PropType.REBOUNDS, 0.70) * REBOUNDS_PACE_SENSITIVITY_MULT
    ratio = game_pace / LEAGUE_AVG_PACE
    return ratio ** sens


def rebound_positional_matchup_only(
    matchup: MatchupModel,
    defense: TeamDefense,
    player: Player,
) -> float:
    """
    Single rebound-defense signal: opponent rebounds allowed to position vs league shape.
    Tighter clamp than generic matchup to avoid DvP acting like a star boost.
    """
    raw = matchup.positional_defense_factor(defense, player, PropType.REBOUNDS)
    return clamp(raw, REBOUNDS_POS_MATCHUP_CLAMP[0], REBOUNDS_POS_MATCHUP_CLAMP[1])


def rebound_environment_multiplier(
    matchup: MatchupModel,
    defense: TeamDefense,
    player: Player,
    game_pace: float,
) -> float:
    """
    Geometric blend of **pace** and **positional rebound defense only** (no FPA).

    Clamped to a narrow band so matchup + pace cannot double-stack like before.
    """
    p_pace = rebound_pace_multiplier(game_pace)
    p_pos = rebound_positional_matchup_only(matchup, defense, player)
    g = math.sqrt(max(p_pace, 1e-6) * max(p_pos, 1e-6))
    return clamp(g, REBOUNDS_ENV_BAND_MIN, REBOUNDS_ENV_BAND_MAX)


def minutes_volatility_ratio(player: Player) -> float:
    """Coefficient of variation of recent minutes; 0 if unknown."""
    mins = player.last10_minutes or player.last5_minutes or []
    if len(mins) < 3:
        return 0.0
    m = mean(mins)
    if m < 1e-6:
        return 0.0
    sd = pstdev(mins) if len(mins) > 1 else 0.0
    return sd / m


def role_stability_factor(player: Player, exp_minutes: float, mpg: float) -> float:
    """
    Penalize bench / unstable roles. Does not invent starters data — uses role + minutes.
    """
    f = 1.0
    if player.role in (PlayerRole.BENCH, PlayerRole.RESERVE):
        f *= 0.93
    if not player.is_starter:
        f *= 0.96
    # Projected minutes far below season average without injury flag → fragile
    if mpg > 5 and exp_minutes < mpg * 0.82 and player.injury_status == InjuryStatus.ACTIVE:
        f *= 0.94
    cv = minutes_volatility_ratio(player)
    if cv > 0.22:
        f *= max(0.88, 1.0 - (cv - 0.22) * 0.8)
    return clamp(f, 0.78, 1.0)


def teammate_competition_factor(player: Player) -> float:
    """
    Proxy for crowded frontcourt / rebound competition.

    Uses rebound_chances vs rebounds_per_game when available; otherwise PF/C bench heuristics.
    """
    f = 1.0
    rpg = max(player.rebounds_per_game, 0.1)
    if player.rebound_chances > 1e-6:
        # Lower conversion of chances → more competition / contested board profile
        ch_per_game = player.rebound_chances  # treated as per-game from tracking
        conv = rpg / ch_per_game
        if conv < 0.42:
            f *= 0.94
        elif conv < 0.48:
            f *= 0.97
    if player.position in (Position.PF, Position.C, Position.FC):
        if player.role == PlayerRole.BENCH:
            f *= 0.95
    return clamp(f, 0.85, 1.0)


def rebound_negbinom_inflation(minute_cv: float, exp_minutes: float, mpg: float) -> float:
    """Higher dispersion when minutes are volatile or projection is below season load."""
    from domain.constants import (
        REBOUNDS_NEGBIN_INFLATION_BASE,
        REBOUNDS_NEGBIN_INFLATION_HIGH_VOLATILITY,
        REBOUNDS_NEGBIN_INFLATION_MINUTES_STRESS,
    )

    v = REBOUNDS_NEGBIN_INFLATION_BASE
    if minute_cv > 0.18:
        v += min(0.45, (minute_cv - 0.18) * 2.0)
    if mpg > 5 and exp_minutes < mpg * 0.88:
        v += REBOUNDS_NEGBIN_INFLATION_MINUTES_STRESS
    return min(v, REBOUNDS_NEGBIN_INFLATION_HIGH_VOLATILITY)
