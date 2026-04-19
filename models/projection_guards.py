"""
Unified floors/ceilings on projected means vs season anchor.
"""

from __future__ import annotations

from domain.constants import (
    ELITE_ASSISTS_FLOOR_THRESHOLD,
    LOW_USAGE_MINUTES_VACUUM_RELAX,
    LOW_USAGE_POINTS_MAX_ABOVE_MINUTE_ANCHOR,
    LOW_USAGE_RATE_THRESHOLD,
    MINUTES_FLOOR_RELAX_THRESHOLD,
    PROJECTION_CEILING_RATIO_BENCH,
    PROJECTION_CEILING_RATIO_STARTER,
    PROJECTION_FLOOR_ELITE_ASSISTS,
    PROJECTION_FLOOR_RATIO_BENCH,
    PROJECTION_FLOOR_RATIO_STARTER,
    THREES_MAX_ABOVE_MINUTE_SCALED_ANCHOR,
)
from domain.entities import Player
from domain.enums import InjuryStatus, PropType
from models.projection_audit import minutes_down_ceiling_bump
from models.projection_baseline import _season_stat
from utils.math_helpers import clamp


def apply_projection_guards(
    projected: float,
    player: Player,
    prop_type: PropType,
    expected_minutes: float,
) -> float:
    """
    Clamp projection relative to season stat anchor; relax floor if minutes are down.
    """
    season_ref = max(_season_stat(player, prop_type), 1e-6)
    is_starter = bool(player.is_starter)
    floor_r = PROJECTION_FLOOR_RATIO_STARTER if is_starter else PROJECTION_FLOOR_RATIO_BENCH
    if (
        prop_type == PropType.ASSISTS
        and is_starter
        and season_ref >= ELITE_ASSISTS_FLOOR_THRESHOLD
        and player.injury_status == InjuryStatus.ACTIVE
    ):
        floor_r = max(floor_r, PROJECTION_FLOOR_ELITE_ASSISTS)
    ceil_r = PROJECTION_CEILING_RATIO_STARTER if is_starter else PROJECTION_CEILING_RATIO_BENCH

    mpg = max(player.minutes_per_game, 1.0)
    minute_frac = expected_minutes / mpg
    minutes_ok = expected_minutes >= MINUTES_FLOOR_RELAX_THRESHOLD * mpg

    if player.injury_status == InjuryStatus.OUT:
        return max(projected, 0.0)

    lo = season_ref * floor_r
    hi = season_ref * ceil_r

    scaled_anchor = season_ref * minute_frac

    # All prop types: fewer minutes than season → ceiling vs minute-scaled season anchor.
    if expected_minutes < mpg - 1e-6 and season_ref > 1e-9:
        hi = min(hi, scaled_anchor * minutes_down_ceiling_bump(player))

    # Low-usage scorers: extra-tight cap vs workload (cannot exceed general ceiling already applied).
    if prop_type == PropType.POINTS:
        usg = float(getattr(player, "usage_rate", 0.0) or 0.0)
        if usg > 1.0:
            usg /= 100.0
        if usg > 0 and usg < LOW_USAGE_RATE_THRESHOLD:
            vacuum = float(getattr(player, "minutes_vacuum", 0.0) or 0.0)
            bump = LOW_USAGE_POINTS_MAX_ABOVE_MINUTE_ANCHOR
            if vacuum >= LOW_USAGE_MINUTES_VACUUM_RELAX:
                bump = min(bump + 0.06, 1.28)
            hi = min(hi, scaled_anchor * bump)

    if not minutes_ok:
        lo *= 0.92
        # Threes makes: cap vs minute-scaled season rate.
        if prop_type == PropType.THREES:
            scaled = season_ref * minute_frac
            hi = min(hi, scaled * THREES_MAX_ABOVE_MINUTE_SCALED_ANCHOR)
        elif prop_type == PropType.POINTS:
            usg = float(getattr(player, "usage_rate", 0.0) or 0.0)
            if usg > 1.0:
                usg /= 100.0
            if usg <= 0 or usg >= LOW_USAGE_RATE_THRESHOLD:
                hi *= 1.05
        else:
            hi *= 1.05

    if player.injury_status in (InjuryStatus.QUESTIONABLE, InjuryStatus.DOUBTFUL):
        hi = min(hi, season_ref * 1.22)

    out = clamp(projected, lo, hi)
    return max(out, 0.0)
