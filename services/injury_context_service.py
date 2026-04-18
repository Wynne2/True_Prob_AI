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

from domain.enums import InjuryStatus
from domain.provider_models import InjuryContext
from services.cache_service import get_cache

logger = logging.getLogger(__name__)
_CACHE = get_cache("sportsdataio", default_ttl=900)   # 15-min TTL for injuries

# Usage boost per absent star teammate (approximate league average)
_USAGE_BOOST_PER_ABSENCE = 0.06   # 6% per key absence, capped at 20%

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
    is_starter = bool(lineup_raw.get("started", True))
    proj_minutes = float(lineup_raw.get("projected_min", 0) or 0)

    # Find teammates who are OUT
    teammates_out_names: list[str] = []
    for pid, inj in _injury_index.items():
        if pid == player_id:
            continue
        if str(inj.get("team_id", "")) != str(team_id):
            continue
        if _normalise_status(inj.get("status", "")) == InjuryStatus.OUT.value:
            teammates_out_names.append(inj.get("player_name", pid))

    out_count = len(teammates_out_names)
    # Vacancy factor: boost per absent teammate, capped at 20%
    vacancy = min(1.0 + out_count * _USAGE_BOOST_PER_ABSENCE, 1.20)

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
        teammate_usage_vacuum=vacancy,
    )


def get_all_injury_statuses() -> dict[str, str]:
    """Return player_id -> status string for all injured players."""
    return {pid: _normalise_status(r.get("status", "active")) for pid, r in _injury_index.items()}


def get_lineup_by_team(team_id: str) -> list[dict]:
    """Return projected lineup records for *team_id*."""
    return [r for r in _lineup_index.values() if str(r.get("team_id", "")) == str(team_id)]
