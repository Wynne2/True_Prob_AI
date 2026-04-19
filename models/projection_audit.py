"""
Volume and role sanity flags for projections (pre–probability).

Flags surface in ``StatProjection.projection_audit_flags`` and PropProbability warnings.
"""

from __future__ import annotations

from domain.constants import (
    HIGH_USAGE_RATE_FLOOR,
    LOW_USAGE_RATE_THRESHOLD,
    MINUTES_DOWN_SCALED_CEILING_BASE,
    MINUTES_DOWN_SCALED_CEILING_HIGH_USG,
    MINUTES_DOWN_SCALED_CEILING_VACUUM,
    POINTS_PER_FGA_EFFICIENCY_CEILING,
)
from domain.entities import Player
from domain.enums import PropType
from models.projection_baseline import season_stat_for_prop


def _norm_usg(player: Player) -> float:
    u = float(getattr(player, "usage_rate", 0.0) or 0.0)
    if u > 1.0:
        u /= 100.0
    return u


def minutes_down_ceiling_bump(player: Player) -> float:
    """Multiplicative ceiling vs minute-scaled season anchor when exp < season MPG."""
    u = _norm_usg(player)
    vacuum = float(getattr(player, "minutes_vacuum", 0.0) or 0.0)
    b = MINUTES_DOWN_SCALED_CEILING_BASE
    if vacuum >= 2.0:
        b = max(b, MINUTES_DOWN_SCALED_CEILING_VACUUM)
    if u >= HIGH_USAGE_RATE_FLOOR:
        b = max(b, MINUTES_DOWN_SCALED_CEILING_HIGH_USG)
    return min(b, 1.28)


def audit_points_projection(
    player: Player,
    projected: float,
    expected_minutes: float,
    season_ppm: float,
    recent_ppm: float,
    environment_multiplier: float,
    expected_fga_proxy: float,
) -> list[str]:
    flags: list[str] = []
    season = season_stat_for_prop(player, PropType.POINTS)
    mpg = max(player.minutes_per_game, 1.0)
    u = _norm_usg(player)
    minute_frac = expected_minutes / mpg
    scaled = season * minute_frac

    if expected_minutes < mpg - 1e-6 and scaled > 0.5:
        bump = minutes_down_ceiling_bump(player)
        if projected > scaled * bump + 1e-3:
            flags.append("projection_vs_minutes_outlier")

    if (
        expected_minutes < 24.0
        and u < LOW_USAGE_RATE_THRESHOLD
        and season > 0.5
        and projected > season * 1.12
    ):
        flags.append("low_minute_high_output_anomaly")

    if season_ppm > 1e-6 and recent_ppm > season_ppm * 1.14 and environment_multiplier > 1.04:
        flags.append("recent_form_double_count_risk")

    if expected_fga_proxy > 2.0 and projected > expected_fga_proxy * POINTS_PER_FGA_EFFICIENCY_CEILING:
        flags.append("projection_vs_usage_outlier")

    if season > 1.0 and projected > season * 1.35:
        flags.append("projection_vs_season_outlier")

    return flags


def audit_threes_projection(
    player: Player,
    projected: float,
    expected_minutes: float,
    expected_3pa_proxy: float,
    season_ppm: float,
) -> list[str]:
    flags: list[str] = []
    season = season_stat_for_prop(player, PropType.THREES)
    mpg = max(player.minutes_per_game, 1.0)
    scaled = season * (expected_minutes / mpg) if mpg > 0 else 0.0
    u = _norm_usg(player)

    if expected_minutes < mpg - 1e-6 and scaled > 0.05:
        bump = minutes_down_ceiling_bump(player)
        if projected > scaled * bump + 0.05:
            flags.append("projection_vs_minutes_outlier")

    if expected_3pa_proxy > 0.4 and projected > expected_3pa_proxy * 0.52:
        flags.append("projection_vs_volume_outlier")

    if expected_minutes < 24.0 and u < LOW_USAGE_RATE_THRESHOLD and projected > season * 1.2:
        flags.append("low_minute_high_output_anomaly")

    if season_ppm > 1e-6 and abs(projected - scaled) > 0.35 and season > 0.3:
        flags.append("threes_mean_vs_attempts_mismatch")

    return flags
