"""
Role-aware **per-minute** skill baseline — no matchup, no pace.

Blends **season rate** and **recent per-minute rate** from paired game logs.
Raw recent game totals are never averaged without minutes; that avoids treating
“more minutes” as “hot form.”

Stat models should use:

  ``raw_projection = blended_stat_rate(...) * expected_minutes``
  then apply multiplicative environment / usage / injury (no second form stack).

``blended_baseline`` returns the same skill estimate expressed as per-game
production at **season MPG** (rate × season minutes) for UI / compatibility.
"""

from __future__ import annotations

from typing import Any

from domain.constants import (
    BASELINE_BLEND_ELITE_ASSISTS,
    BASELINE_BLEND_ELITE_POINTS,
    BASELINE_BLEND_ELITE_REBOUNDS,
    BASELINE_BLEND_WEIGHTS,
    BASELINE_HARD_ANCHOR_RATIO,
    BASELINE_HARD_ANCHOR_RATIO_BENCH,
    BASELINE_SEASON_SOFT_FLOOR_RATIO,
    BASELINE_SEASON_SOFT_FLOOR_RATIO_BENCH,
    ELITE_ASSISTS_SEASON_THRESHOLD,
    ELITE_POINTS_SEASON_THRESHOLD,
    ELITE_REBOUNDS_SEASON_THRESHOLD,
    RECENT_VS_SEASON_FLOOR_RATIO,
)
from domain.entities import Player
from domain.enums import InjuryStatus, PropType


def _season_stat(player: Player, prop_type: PropType) -> float:
    m: dict[PropType, float] = {
        PropType.POINTS: player.points_per_game,
        PropType.REBOUNDS: player.rebounds_per_game,
        PropType.ASSISTS: player.assists_per_game,
        PropType.THREES: player.threes_per_game,
        PropType.STEALS: player.steals_per_game,
        PropType.BLOCKS: player.blocks_per_game,
        PropType.TURNOVERS: player.turnovers_per_game,
        PropType.PRA: player.points_per_game + player.rebounds_per_game + player.assists_per_game,
    }
    return max(m.get(prop_type, 0.0), 0.0)


def _trusts_full_season_anchor(player: Player) -> bool:
    return player.injury_status == InjuryStatus.ACTIVE


def _season_anchor_ratios(player: Player) -> tuple[float, float]:
    """(soft_floor, hard_floor) multipliers vs season **rate** for ACTIVE players."""
    if player.is_starter:
        return BASELINE_SEASON_SOFT_FLOOR_RATIO, BASELINE_HARD_ANCHOR_RATIO
    return BASELINE_SEASON_SOFT_FLOOR_RATIO_BENCH, BASELINE_HARD_ANCHOR_RATIO_BENCH


def _blend_weights(player: Player, prop_type: PropType) -> dict[str, float]:
    if not _trusts_full_season_anchor(player):
        return dict(BASELINE_BLEND_WEIGHTS)
    if (
        prop_type == PropType.ASSISTS
        and player.assists_per_game >= ELITE_ASSISTS_SEASON_THRESHOLD
        and player.is_starter
    ):
        return dict(BASELINE_BLEND_ELITE_ASSISTS)
    if (
        prop_type == PropType.REBOUNDS
        and player.rebounds_per_game >= ELITE_REBOUNDS_SEASON_THRESHOLD
        and player.is_starter
    ):
        return dict(BASELINE_BLEND_ELITE_REBOUNDS)
    if (
        prop_type == PropType.POINTS
        and player.points_per_game >= ELITE_POINTS_SEASON_THRESHOLD
        and player.is_starter
    ):
        return dict(BASELINE_BLEND_ELITE_POINTS)
    if prop_type == PropType.PRA and player.is_starter:
        if player.points_per_game >= ELITE_POINTS_SEASON_THRESHOLD:
            return dict(BASELINE_BLEND_ELITE_POINTS)
        if player.assists_per_game >= ELITE_ASSISTS_SEASON_THRESHOLD:
            return dict(BASELINE_BLEND_ELITE_POINTS)
        if player.rebounds_per_game >= ELITE_REBOUNDS_SEASON_THRESHOLD:
            return dict(BASELINE_BLEND_ELITE_REBOUNDS)
    return dict(BASELINE_BLEND_WEIGHTS)


def _aligned_stat_minute_lists(player: Player, prop_type: PropType) -> tuple[list[float], list[float]]:
    """
    Return parallel (stat per game, minutes) lists for the best available window.
    Prefer last10 when present; else last5.
    """
    if prop_type == PropType.PRA:
        if player.last10_points and player.last10_rebounds and player.last10_assists:
            lp, lr, la = player.last10_points, player.last10_rebounds, player.last10_assists
            n = min(len(lp), len(lr), len(la))
            stats = [lp[i] + lr[i] + la[i] for i in range(n)]
            mins = player.last10_minutes or []
        elif player.last5_points and player.last5_rebounds and player.last5_assists:
            lp, lr, la = player.last5_points, player.last5_rebounds, player.last5_assists
            n = min(len(lp), len(lr), len(la))
            stats = [lp[i] + lr[i] + la[i] for i in range(n)]
            mins = player.last5_minutes or []
        else:
            return [], []
        if len(mins) < n:
            return [], []
        return stats, mins[:n]

    stat_map: dict[PropType, tuple[str, str]] = {
        PropType.POINTS: ("last10_points", "last5_points"),
        PropType.REBOUNDS: ("last10_rebounds", "last5_rebounds"),
        PropType.ASSISTS: ("last10_assists", "last5_assists"),
        PropType.THREES: ("last10_threes", "last5_threes"),
        PropType.STEALS: ("last10_steals", "last5_steals"),
        PropType.BLOCKS: ("last10_blocks", "last5_blocks"),
        PropType.TURNOVERS: ("last10_turnovers", "last5_turnovers"),
    }
    k10, k5 = stat_map[prop_type]
    stats_10 = getattr(player, k10, None) or []
    stats_5 = getattr(player, k5, None) or []
    if stats_10:
        mins = player.last10_minutes or []
        return list(stats_10), list(mins)
    if stats_5:
        mins = player.last5_minutes or []
        return list(stats_5), list(mins)
    return [], []


def _recent_rate_from_pairs(
    stats: list[float],
    mins: list[float],
    season: float,
    mpg: float,
) -> float:
    """Mean stat/minute over paired games (minutes >= 3). Fallback: season per-minute rate."""
    season_rate = season / mpg if mpg > 0 else 0.0
    if not stats or not mins:
        return season_rate
    n = min(len(stats), len(mins))
    pairs = [(stats[i], mins[i]) for i in range(n) if mins[i] >= 3.0]
    if not pairs:
        return season_rate
    return sum(s / m for s, m in pairs) / len(pairs)


def blended_stat_rate(
    player: Player,
    prop_type: PropType,
    expected_minutes: float,
) -> tuple[float, dict[str, Any]]:
    """
    Blended **per-minute** skill rate for *prop_type*.

    Combines season rate with recent per-minute production from paired logs.
    The legacy ``per_minute_expected`` weight is merged into the season pillar so we
    do not double-count the same season curve under two names.
    """
    season = _season_stat(player, prop_type)
    mpg = max(player.minutes_per_game, 1.0)
    season_rate = season / mpg

    stats, mins = _aligned_stat_minute_lists(player, prop_type)
    recent_rate = _recent_rate_from_pairs(stats, mins, season, mpg)

    if _trusts_full_season_anchor(player) and season_rate > 0:
        recent_rate = max(recent_rate, season_rate * RECENT_VS_SEASON_FLOOR_RATIO)

    w = _blend_weights(player, prop_type)
    w_season_combined = w["season"] + w["per_minute_expected"]
    blended_rate = w_season_combined * season_rate + w["recent"] * recent_rate

    if _trusts_full_season_anchor(player) and season > 0:
        soft_r, hard_r = _season_anchor_ratios(player)
        blended_rate = max(blended_rate, season_rate * soft_r, season_rate * hard_r)

    detail: dict[str, Any] = {
        "season_anchor": season,
        "season_rate_per_minute": season_rate,
        "recent_rate_per_minute": recent_rate,
        "recent_avg_equiv_at_season_mpg": recent_rate * mpg,
        "expected_minutes_pass_through": expected_minutes,
        "weights": dict(w),
        "full_season_anchor": _trusts_full_season_anchor(player),
    }
    return max(blended_rate, 0.0), detail


def blended_baseline(
    player: Player,
    prop_type: PropType,
    expected_minutes: float,
) -> tuple[float, dict[str, Any]]:
    """
    Per-game skill baseline at **season** minute load: ``rate × season_mpg``.

    ``expected_minutes`` is recorded for debugging only; it must not enter the blend.
    """
    rate, detail = blended_stat_rate(player, prop_type, expected_minutes)
    mpg = max(player.minutes_per_game, 1.0)
    baseline_ppg = rate * mpg
    # Back-compat keys for callers / tests
    detail["per_minute_at_season_mpg"] = (detail["season_anchor"] / mpg) * mpg
    detail["recent_avg"] = detail.get("recent_avg_equiv_at_season_mpg", rate * mpg)
    return max(baseline_ppg, 0.0), detail


def season_stat_for_prop(player: Player, prop_type: PropType) -> float:
    """Season per-game average for *prop_type* (shared anchor for guards / checks)."""
    return _season_stat(player, prop_type)
