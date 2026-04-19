"""
Injury Context Service.

Fetches and processes injury and availability data from SportsDataIO.

Responsibilities:
  - Maintain a player_id → InjuryContext lookup for the current date.
  - Compute the teammate-vacancy factor: when key teammates are OUT,
    the remaining players receive redistributed usage.
  - Expose starter / role context from projected lineups.

SOURCE: SportsDataIO primary (injuries, lineups, depth charts).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from domain.enums import InjuryStatus, Position
from domain.provider_models import InjuryContext
from models.injury_redistribution_model import build_teammates_out_dicts, compute_vacancy_factor
from services.cache_service import get_cache

logger = logging.getLogger(__name__)
_CACHE = get_cache("sportsdataio", default_ttl=900)   # 15-min TTL for injuries

_STATUS_MAP: dict[str, InjuryStatus] = {
    "active": InjuryStatus.ACTIVE,
    "probable": InjuryStatus.ACTIVE,
    "questionable": InjuryStatus.QUESTIONABLE,
    "doubtful": InjuryStatus.DOUBTFUL,
    "out": InjuryStatus.OUT,
    "gtd": InjuryStatus.QUESTIONABLE,
    "game time decision": InjuryStatus.QUESTIONABLE,
    "day-to-day": InjuryStatus.DAY_TO_DAY,
    "suspended": InjuryStatus.SUSPENDED,
    "not with team": InjuryStatus.NOT_WITH_TEAM,
}


def _normalise_status(raw: str) -> str:
    """Map provider status string to our InjuryStatus values."""
    clean = (raw or "active").strip().lower()
    return _STATUS_MAP.get(clean, InjuryStatus.ACTIVE).value


# ---------------------------------------------------------------------------
# Module-level injury index (loaded once per refresh)
# ---------------------------------------------------------------------------

_injury_index: dict[str, dict] = {}   # player_id -> raw injury record
_lineup_index: dict[str, dict] = {}   # player_id -> projected lineup record
_loaded_date: Optional[date] = None


def refresh(game_date: Optional[date] = None) -> None:
    """
    Load or refresh injury and lineup data from SportsDataIO.

    Safe to call multiple times — re-fetches from provider (with 15-min
    disk cache backing) each time.
    """
    global _injury_index, _lineup_index, _loaded_date

    ds = game_date or date.today()

    from data.loaders.sportsdataio_loader import (
        fetch_injuries,
        fetch_projected_lineups,
    )

    injuries = fetch_injuries()
    _injury_index = {r["player_id"]: r for r in injuries if r.get("player_id")}

    lineups = fetch_projected_lineups(ds)
    _lineup_index = {r["player_id"]: r for r in lineups if r.get("player_id")}

    _loaded_date = ds
    logger.info(
        "InjuryContextService: loaded %d injury records, %d lineup projections for %s",
        len(_injury_index), len(_lineup_index), ds,
    )


def get_injury_context(
    player_id: str,
    team_id: str,
    game_date: Optional[date] = None,
) -> InjuryContext:
    """
    Return the full InjuryContext for *player_id*.

    Computes teammate vacancy factor from the set of OUT teammates on the
    same team for *game_date*.
    """
    if _loaded_date != (game_date or date.today()):
        refresh(game_date)

    injury_raw = _injury_index.get(player_id, {})
    lineup_raw = _lineup_index.get(player_id, {})

    status = _normalise_status(
        injury_raw.get("status") or lineup_raw.get("injury_status") or "active"
    )

    # Default is_starter to False (unknown) when no lineup data, not True.
    # Using depth-order == 1 from lineup as a reliable starter signal.
    if lineup_raw:
        is_starter = bool(lineup_raw.get("started") or lineup_raw.get("depth_order") == 1)
    else:
        is_starter = False

    proj_minutes = float(lineup_raw.get("projected_min", 0) or 0)

    # --- Role-aware injury redistribution (replaces flat uniform boost) ---
    # Build the list of OUT teammates with their stats for redistribution.
    from services.player_context_service import _season_stats_index as _stats_idx
    teammates_out_dicts = build_teammates_out_dicts(
        _injury_index, team_id, player_id, _stats_idx
    )
    teammates_out_names = [t["player_name"] for t in teammates_out_dicts]
    out_count = len(teammates_out_dicts)

    # Determine this player's position for similarity weighting.
    from services.player_context_service import _depth_index as _depth_idx
    depth_rec = _depth_idx.get(player_id, {})
    raw_pos = injury_raw.get("position") or depth_rec.get("position") or "G"
    try:
        player_pos = Position(raw_pos.upper())
    except (ValueError, AttributeError):
        player_pos = Position.G

    player_usage = float((_stats_idx.get(player_id) or {}).get("usage_rate", 0) or 0)

    usage_boost, minutes_boost = compute_vacancy_factor(
        player_pos, player_usage, teammates_out_dicts
    )

    # teammate_usage_vacuum expressed as a multiplier (>1.0 = more usage available)
    # Additive usage boost expressed relative to a baseline usage of 0.20
    base_usage_for_mult = max(player_usage, 0.20)
    vacancy_multiplier = 1.0 + (usage_boost / base_usage_for_mult) if usage_boost > 0 else 1.0
    vacancy_multiplier = min(vacancy_multiplier, 1.40)

    if out_count > 0:
        logger.info(
            "InjuryRedistribution: player=%s team=%s | %d teammate(s) OUT → "
            "usage_boost=+%.3f, minutes_boost=+%.1f min",
            player_id, team_id, out_count, usage_boost, minutes_boost,
        )

    return InjuryContext(
        player_id=player_id,
        player_name=injury_raw.get("player_name", ""),
        team_id=team_id,
        status=status,
        injury_description=injury_raw.get("injury", ""),
        is_starter=is_starter,
        projected_minutes=proj_minutes,
        teammates_out=teammates_out_names,
        teammates_out_count=out_count,
        teammate_usage_vacuum=vacancy_multiplier,
        minutes_vacuum=minutes_boost,
    )


def get_all_injury_statuses() -> dict[str, str]:
    """Return player_id -> status string for all injured players."""
    return {pid: _normalise_status(r.get("status", "active")) for pid, r in _injury_index.items()}


def get_lineup_by_team(team_id: str) -> list[dict]:
    """Return projected lineup records for *team_id*."""
    return [r for r in _lineup_index.values() if str(r.get("team_id", "")) == str(team_id)]
