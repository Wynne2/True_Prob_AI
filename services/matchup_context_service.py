"""
Matchup Context Service.

Builds MatchupContext from a blend of:
  - SportsDataIO: team defensive stats, season pts allowed, recent trends
  - nba_api: pace, possessions-per-game environment

SOURCE:
  - SportsDataIO → team defensive stats, opponent season context
  - nba_api → pace / possessions environment (PRIMARY for these factors)
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from data.loaders.nba_api_loader import fetch_team_pace_batch, index_by_player_id
from data.loaders.sportsdataio_loader import (
    fetch_team_season_stats,
    index_by_team_id,
)
from domain.constants import NBA_SEASON, SDIO_SEASON
from domain.provider_models import MatchupContext
from services.cache_service import get_cache

logger = logging.getLogger(__name__)
_CACHE = get_cache("derived", default_ttl=21_600)

_CURRENT_SEASON_SDIO = SDIO_SEASON
_CURRENT_SEASON_NBA = NBA_SEASON

# League-average references (2024-25 estimates)
_LEAGUE_AVG_DEF_RATING = 113.1
_LEAGUE_AVG_PACE = 100.3
_LEAGUE_AVG_PTS_ALLOWED = 113.0

# Module-level indexes
_team_stats_index: dict[str, dict] = {}    # team_id -> SportsDataIO team stats
_team_pace_index: dict[str, dict] = {}     # team_id -> nba_api team pace
_loaded_date: Optional[date] = None


def refresh(force: bool = False) -> None:
    """Load or refresh team-level context data."""
    global _team_stats_index, _team_pace_index, _loaded_date

    today = date.today()
    if not force and _loaded_date == today and _team_stats_index:
        return

    date_str = today.isoformat()

    # SOURCE: SportsDataIO (team defensive stats)
    sdio_teams = fetch_team_season_stats(season=_CURRENT_SEASON_SDIO)
    _team_stats_index = {r["team_id"]: r for r in sdio_teams if r.get("team_id")}

    # SOURCE: nba_api (pace/possessions environment)
    nba_teams = fetch_team_pace_batch(season=_CURRENT_SEASON_NBA, date_str=date_str)
    _team_pace_index = {r["team_id"]: r for r in nba_teams if r.get("team_id")}

    _loaded_date = today
    logger.info(
        "MatchupContextService: loaded %d SDIO team records, %d nba_api pace records",
        len(_team_stats_index), len(_team_pace_index),
    )


def get_matchup_context(
    home_team_id: str,
    away_team_id: str,
    defense_team_id: str,
    game_total: float = 0.0,
    point_spread: float = 0.0,
) -> MatchupContext:
    """
    Build a MatchupContext for the team facing *defense_team_id*.

    Parameters
    ----------
    home_team_id / away_team_id : str
        Team IDs from the slate.
    defense_team_id : str
        The defending team (the opponent of the player being evaluated).
    game_total / point_spread : float
        From the odds feed.
    """
    if not _team_stats_index:
        refresh()

    sdio = _team_stats_index.get(defense_team_id, {})
    nba = _team_pace_index.get(defense_team_id, {})

    # Defensive efficiency (SOURCE: nba_api primary for def_rating)
    def_rating = float(nba.get("def_rating", 0) or 0)
    opp_pace = float(nba.get("pace", 0) or _LEAGUE_AVG_PACE)

    # Points allowed (SOURCE: SportsDataIO for raw pts allowed)
    # SDIO reports raw pts/game; normalize to per-100-poss so it's comparable
    # to nba_api def_rating (which is also per-100-poss).
    pts_allowed_raw = float(sdio.get("opp_pts", 0) or 0)
    if pts_allowed_raw > 0 and opp_pace > 0:
        # pts_per_game → pts_per_100_poss = pts_per_game * (100 / pace * 48)
        pts_allowed_per100 = pts_allowed_raw * (100.0 / (opp_pace / 48.0)) / 48.0
    else:
        pts_allowed_per100 = 0.0

    # Prefer nba_api def_rating (already per-100-poss); fall back to normalised SDIO
    if def_rating > 0:
        effective_def_rating = def_rating
    elif pts_allowed_per100 > 0:
        effective_def_rating = pts_allowed_per100
    else:
        effective_def_rating = _LEAGUE_AVG_DEF_RATING

    # Normalised defense factor (>1.0 = weaker defense = boost for offence)
    defense_factor = effective_def_rating / _LEAGUE_AVG_DEF_RATING

    # Use raw pts/game as the readable "pts_allowed_per_game" for display only
    pts_allowed = pts_allowed_raw if pts_allowed_raw > 0 else (
        effective_def_rating * opp_pace / (100.0 * 48.0 / 48.0)
    )

    # Clamp to ±20%
    defense_factor = max(0.80, min(1.20, defense_factor))
    def_rating = effective_def_rating  # expose the normalized rating

    # Recent defense trend — use last-10 from nba_api if available, else season
    recent_defense_factor = defense_factor   # no last-10 team split in simple pull; use season

    return MatchupContext(
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        defense_team_id=defense_team_id,
        defense_team_abbr=sdio.get("team", "") or nba.get("team_abbr", ""),
        def_rating=def_rating,
        opp_pace=opp_pace,
        pts_allowed_per_game=pts_allowed,
        last_10_pts_allowed=pts_allowed,  # season proxy
        last_10_def_rating=def_rating,    # season proxy
        game_total=game_total,
        point_spread=point_spread,
        defense_factor=defense_factor,
        recent_defense_factor=recent_defense_factor,
    )


def get_team_pace(team_id: str) -> float:
    """Return pace for *team_id* (possessions per 48 min)."""
    if not _team_pace_index:
        refresh()
    return float(_team_pace_index.get(team_id, {}).get("pace", _LEAGUE_AVG_PACE))
