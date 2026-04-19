"""
Injury Redistribution Model.

When a teammate is OUT, their usage and minute load do not disappear — they
redistribute to remaining players based on positional similarity and role.

This model replaces the flat "every active player gets +6% per absence" logic
in injury_context_service.py with a position-weighted, usage-weighted
calculation that correctly boosts guards when a guard is out and bigs when
a big is out.

SOURCE: Player usage/minutes from PlayerContextService + position weights
        from domain/constants.py.
"""

from __future__ import annotations

import logging
from typing import Optional

from domain.constants import POSITION_SIMILARITY
from domain.enums import InjuryStatus, Position

logger = logging.getLogger(__name__)

# Caps on redistribution to prevent unrealistic single-game spikes.
_MAX_USAGE_BOOST: float = 0.12     # max additive usage boost (12 pp)
_MAX_MINUTES_BOOST: float = 5.0   # max additive minutes boost (5 min)

# Fraction of a missing player's usage/minutes that "leaks" to teammates.
# Not 100% because some of it goes to slower offenses, more possessions out,
# or simply a lower team pace.
_USAGE_REDISTRIBUTION_FRACTION: float = 0.60
_MINUTES_REDISTRIBUTION_FRACTION: float = 0.65


def _pos_key(position: Position) -> str:
    """Return the string key used in POSITION_SIMILARITY lookup."""
    return position.value if hasattr(position, "value") else str(position)


def compute_vacancy_factor(
    player_position: Position,
    player_usage_rate: float,
    teammates_out: list[dict],
) -> tuple[float, float]:
    """
    Compute how much extra usage and minutes a player should receive
    when teammates are OUT.

    Args:
        player_position: The active player's position enum.
        player_usage_rate: The active player's current usage rate (0-1 fraction).
        teammates_out: list of dicts with keys:
            - "position": Position enum (or string)
            - "usage_rate": float (0-1 fraction)
            - "minutes_per_game": float

    Returns:
        (usage_boost, minutes_boost):
            usage_boost   — additive usage-rate boost to apply (e.g. 0.05 → +5%)
            minutes_boost — additive minutes boost (e.g. 3.0 → +3 min projected)
    """
    player_pos_key = _pos_key(player_position)
    sim_row = POSITION_SIMILARITY.get(player_pos_key, {})

    total_usage_vacuum = 0.0
    total_minutes_vacuum = 0.0

    for out in teammates_out:
        raw_pos = out.get("position", "G")
        out_pos_key = _pos_key(raw_pos) if isinstance(raw_pos, Position) else str(raw_pos).upper()

        similarity = sim_row.get(out_pos_key, 0.0)
        if similarity <= 0:
            continue

        out_usage = float(out.get("usage_rate", 0) or 0)
        out_minutes = float(out.get("minutes_per_game", 0) or 0)

        if out_usage <= 0 and out_minutes <= 0:
            # Fallback for when player stats are not loaded: use conservative defaults
            out_usage = 0.20
            out_minutes = 28.0
            logger.debug(
                "InjuryRedistribution: no stats for OUT player; using defaults "
                "(usage=0.20, min=28)"
            )

        # Share of their opportunity that this player can absorb
        usage_share = out_usage * similarity * _USAGE_REDISTRIBUTION_FRACTION
        minutes_share = out_minutes * similarity * _MINUTES_REDISTRIBUTION_FRACTION

        total_usage_vacuum += usage_share
        total_minutes_vacuum += minutes_share

        logger.debug(
            "InjuryRedistribution: OUT player (pos=%s, usg=%.3f, min=%.1f) → "
            "active player (pos=%s, sim=%.2f) gets +%.3f usg, +%.1f min",
            out_pos_key, out_usage, out_minutes,
            player_pos_key, similarity, usage_share, minutes_share,
        )

    # Apply caps
    usage_boost = min(total_usage_vacuum, _MAX_USAGE_BOOST)
    minutes_boost = min(total_minutes_vacuum, _MAX_MINUTES_BOOST)

    return usage_boost, minutes_boost


def build_teammates_out_dicts(
    injury_index: dict,
    team_id: str,
    player_id: str,
    season_stats_index: Optional[dict] = None,
) -> list[dict]:
    """
    Build the list of OUT teammate dicts from the live injury index.

    Args:
        injury_index: player_id → raw injury record (from InjuryContextService).
        team_id: Team ID of the active player.
        player_id: Active player's own ID (excluded from the list).
        season_stats_index: Optional player_id → season stats for usage/minutes.

    Returns:
        List of dicts suitable for compute_vacancy_factor().
    """
    from domain.enums import InjuryStatus

    result: list[dict] = []
    for pid, inj in injury_index.items():
        if pid == player_id:
            continue
        if str(inj.get("team_id", "")) != str(team_id):
            continue

        raw_status = (inj.get("status") or "active").strip().lower()
        is_out = raw_status in ("out", "suspended", "not with team", "not_with_team")
        if not is_out:
            continue

        stats = (season_stats_index or {}).get(pid, {})
        raw_pos = inj.get("position") or stats.get("position") or "G"

        result.append({
            "player_id": pid,
            "player_name": inj.get("player_name", pid),
            "position": str(raw_pos).upper(),
            "usage_rate": float(stats.get("usg_pct", 0) or 0) / 100.0
                          if stats.get("usg_pct", 0) > 1 else float(stats.get("usage_rate", 0) or 0),
            "minutes_per_game": float(stats.get("min", 0) or 0),
        })

    return result
