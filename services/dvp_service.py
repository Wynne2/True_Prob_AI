"""
DvP Service  –  thin accessor wrapper around dvp_builder.

Provides a clean interface for the engine to retrieve DvP factors
without knowing the raw build process.

SOURCE: computed internally from nba_api + SportsDataIO data.
        See data/builders/dvp_builder.py for construction logic.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from data.builders.dvp_builder import (
    DvPEntry,
    _normalise_position,
    build_and_cache_dvp,
    get_league_avg_for_position,
)

logger = logging.getLogger(__name__)

# Module-level DvP table (loaded once per day / per process)
_dvp_tables: dict[str, dict[str, DvPEntry]] = {}
_dvp_date: Optional[date] = None


def refresh_dvp_tables(
    player_game_logs: list[dict],
    position_map: dict[str, str],
    cache_date: Optional[date] = None,
) -> None:
    """
    Build or refresh the in-process DvP tables.

    Call this once per daily run (from SlateScanner or engine init) before
    evaluating any props.  Subsequent calls on the same date will be served
    from disk cache.

    Parameters
    ----------
    player_game_logs : list[dict]
        Raw game logs from nba_api or SportsDataIO containing:
        player_id, opponent_team_id, pts, reb, ast, stl, blk, tov.
    position_map : dict[str, str]
        player_id -> normalised position (PG/SG/SF/PF/C).
    cache_date : date | None
        Date to tag the cache entry (defaults to today).
    """
    global _dvp_tables, _dvp_date

    ds = cache_date or date.today()
    if _dvp_date == ds and _dvp_tables:
        logger.debug("DvP tables already loaded for %s", ds)
        return

    logger.info("DvP service: refreshing tables for %s", ds)
    _dvp_tables = build_and_cache_dvp(player_game_logs, position_map, ds)
    _dvp_date = ds
    logger.info(
        "DvP service: tables ready (%d teams)",
        len(_dvp_tables),
    )


def get_dvp(
    defense_team_id: str,
    position: str,
) -> Optional[DvPEntry]:
    """
    Return the DvPEntry for *defense_team_id* vs players at *position*.

    Returns None if no DvP data exists for this combination.
    """
    norm_pos = _normalise_position(position)
    entry = _dvp_tables.get(defense_team_id, {}).get(norm_pos)
    if entry is None:
        logger.debug(
            "DvP: no entry for team=%s pos=%s", defense_team_id, norm_pos
        )
    return entry


def get_dvp_factor(
    defense_team_id: str,
    position: str,
    stat: str = "pts",
) -> float:
    """
    Return the normalised DvP factor for a specific stat.

    stat: 'pts', 'reb', 'ast', 'fantasy'

    Returns 1.0 (neutral) if no data is available.
    """
    entry = get_dvp(defense_team_id, position)
    if entry is None:
        return 1.0

    mapping = {
        "pts": entry.norm_pts,
        "reb": entry.norm_reb,
        "ast": entry.norm_ast,
        "fantasy": entry.norm_fantasy,
    }
    return mapping.get(stat, 1.0)


def is_loaded() -> bool:
    """Return True if DvP tables have been loaded for the current date."""
    return bool(_dvp_tables) and _dvp_date == date.today()
