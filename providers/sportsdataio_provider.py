"""
SportsDataIO provider.

PRIMARY source for:
  - Game schedules / slates
  - Player season averages (box-score stats)
  - Player game logs / recent form
  - Injuries / availability
  - Depth charts / lineups / projected starters
  - Team defensive stats and season context

All heavy lifting is delegated to data/loaders/sportsdataio_loader.py
which handles caching.  This provider adapts those raw records into
domain entities for the ProviderRegistry interface.

SOURCE: SportsDataIO NBA API (api.sportsdata.io)
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from config import get_credentials, get_sdio_config
from data.loaders.sportsdataio_loader import (
    fetch_depth_charts,
    fetch_games_for_date,
    fetch_injuries,
    fetch_player_game_logs,
    fetch_player_season_stats,
    fetch_projected_lineups,
    fetch_team_season_stats,
    index_by_player_id,
)
from domain.constants import PROP_ALIAS_MAP
from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import (
    BookName,
    DataSource,
    InjuryStatus,
    PlayerRole,
    Position,
    PropType,
)
from providers.base_provider import BaseProvider
from utils.date_utils import format_date, today_eastern

logger = logging.getLogger(__name__)

_POSITION_MAP: dict[str, Position] = {
    "PG": Position.PG,
    "SG": Position.SG,
    "SF": Position.SF,
    "PF": Position.PF,
    "C": Position.C,
    "G": Position.G,
    "F": Position.F,
    "FC": Position.FC,
    "GF": Position.GF,
}

_STATUS_MAP: dict[str, InjuryStatus] = {
    "active": InjuryStatus.ACTIVE,
    "questionable": InjuryStatus.QUESTIONABLE,
    "doubtful": InjuryStatus.DOUBTFUL,
    "out": InjuryStatus.OUT,
    "gtd": InjuryStatus.QUESTIONABLE,
    "day-to-day": InjuryStatus.DAY_TO_DAY,
    "probable": InjuryStatus.ACTIVE,
    "suspended": InjuryStatus.SUSPENDED,
    "not with team": InjuryStatus.NOT_WITH_TEAM,
}

_CURRENT_SEASON = "2026"


class SportsDataIOProvider(BaseProvider):
    """
    SportsDataIO NBA data provider.

    SOURCE: SportsDataIO API (PRIMARY: injuries, rosters, season stats,
            game logs, depth charts, schedules, team defense).
    """

    source_name = DataSource.SPORTSDATAIO

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def is_available(self) -> bool:
        return bool(self._key)

    # ------------------------------------------------------------------
    # Schedule / Games  (SOURCE: SportsDataIO primary for slate)
    # ------------------------------------------------------------------

    def get_games_for_date(self, game_date: date) -> list[Game]:
        """
        Return all NBA games on *game_date*.

        SOURCE: SportsDataIO (slate/schedule support).
        """
        raw = fetch_games_for_date(game_date)
        games = []
        for r in raw:
            try:
                games.append(Game(
                    game_id=r["game_id"],
                    home_team_id=r["home_team_id"],
                    home_team_abbr=r["home_team"],
                    away_team_id=r["away_team_id"],
                    away_team_abbr=r["away_team"],
                    game_date=game_date,
                    game_total=r.get("over_under", 0.0),
                    home_spread=r.get("point_spread", 0.0),
                    data_source=DataSource.SPORTSDATAIO,
                ))
            except Exception as exc:
                logger.warning("SportsDataIO: failed to parse game: %s", exc)
        return games

    # ------------------------------------------------------------------
    # Players  (SOURCE: SportsDataIO primary for season averages / roster)
    # ------------------------------------------------------------------

    def get_players_for_game(self, game_id: str) -> list[Player]:
        """
        Return players for *game_id* using projected lineup data.

        SOURCE: SportsDataIO projected lineups.
        """
        return []  # Handled more richly by PlayerContextService

    def get_player_context(self, player_id: str) -> Optional[Player]:
        """
        Return a Player entity populated from SportsDataIO season stats.

        SOURCE: SportsDataIO (PRIMARY: season averages, injury status).
        """
        stats = index_by_player_id(fetch_player_season_stats(_CURRENT_SEASON))
        depth = index_by_player_id(fetch_depth_charts())

        sdio = stats.get(player_id)
        d = depth.get(player_id, {})

        if not sdio and not d:
            return None

        raw_pos = (sdio or d).get("position", "G") if sdio or d else "G"
        position = _POSITION_MAP.get(str(raw_pos).upper().strip(), Position.G)

        inj_raw = d.get("injury_status", "")
        injury = _STATUS_MAP.get(str(inj_raw).lower(), InjuryStatus.ACTIVE)

        s = sdio or {}
        return Player(
            player_id=player_id,
            name=s.get("player_name") or d.get("player_name", ""),
            team_id=s.get("team_id") or d.get("team_id", ""),
            team_abbr=s.get("team") or d.get("team", ""),
            position=position,
            injury_status=injury,
            is_starter=d.get("depth_order", 99) == 1,
            minutes_per_game=float(s.get("min", 0) or 0),
            points_per_game=float(s.get("pts", 0) or 0),
            rebounds_per_game=float(s.get("reb", 0) or 0),
            assists_per_game=float(s.get("ast", 0) or 0),
            threes_per_game=float(s.get("fg3m", 0) or 0),
            blocks_per_game=float(s.get("blk", 0) or 0),
            steals_per_game=float(s.get("stl", 0) or 0),
            turnovers_per_game=float(s.get("tov", 0) or 0),
            field_goal_attempts=float(s.get("fga", 0) or 0),
            free_throw_attempts=float(s.get("fta", 0) or 0),
            three_point_attempts=float(s.get("fg3a", 0) or 0),
            data_source=DataSource.SPORTSDATAIO,
        )

    def get_player_recent_form(self, player_id: str, n: int = 10) -> list[dict]:
        """
        Return last *n* game logs for *player_id*.

        SOURCE: SportsDataIO (primary: recent form / game logs).
        """
        return fetch_player_game_logs(player_id, season=_CURRENT_SEASON, num_games=n)

    # ------------------------------------------------------------------
    # Team defense  (SOURCE: SportsDataIO primary)
    # ------------------------------------------------------------------

    def get_team_defense(self, team_id: str) -> Optional[TeamDefense]:
        """
        Return a TeamDefense entity from SportsDataIO team season stats.

        Note: Per-position DvP breakdown is computed by dvp_builder, not
        here.  This method provides the team-level defensive context.

        SOURCE: SportsDataIO (team defensive stats).
        """
        team_stats = {r["team_id"]: r for r in fetch_team_season_stats(_CURRENT_SEASON)}
        raw = team_stats.get(team_id)
        if not raw:
            return None

        # Use opponent points allowed as a proxy for defensive efficiency
        opp_pts = float(raw.get("opp_pts", 0) or 0)

        return TeamDefense(
            team_id=team_id,
            team_abbr=raw.get("team", ""),
            # Overall defensive context
            defensive_efficiency=opp_pts,   # pts allowed per game (not per 100 poss)
            # Position-level DvP fields are populated by dvp_builder; set neutral defaults
            pts_allowed_pg=opp_pts,
            pts_allowed_sg=opp_pts,
            pts_allowed_sf=opp_pts,
            pts_allowed_pf=opp_pts,
            pts_allowed_c=opp_pts,
            data_source=DataSource.SPORTSDATAIO,
        )

    def get_team_context(self, team_id: str) -> Optional[dict]:
        """
        Return raw team context dict from SportsDataIO.

        SOURCE: SportsDataIO (team stats, pace context).
        """
        team_stats = {r["team_id"]: r for r in fetch_team_season_stats(_CURRENT_SEASON)}
        return team_stats.get(team_id)

    def get_fantasy_points_allowed(self, team_id: str, position: str) -> float:
        """
        Return FPA from DvP tables if loaded, else 0.0.

        SOURCE: derived internally via dvp_service.
        """
        try:
            from services.dvp_service import get_dvp_factor
            return get_dvp_factor(team_id, position, "fantasy")
        except Exception:
            return 0.0

    def get_defense_vs_position(
        self, team_id: str, position: str, prop_type: PropType
    ) -> float:
        """
        Return normalised DvP factor for *team_id* / *position* / *prop_type*.

        SOURCE: derived internally via dvp_service.
        """
        try:
            from services.dvp_service import get_dvp_factor
            stat_map = {
                PropType.POINTS: "pts",
                PropType.REBOUNDS: "reb",
                PropType.ASSISTS: "ast",
                PropType.PRA: "fantasy",
            }
            stat = stat_map.get(prop_type, "pts")
            return get_dvp_factor(team_id, position, stat)
        except Exception:
            return 1.0

    # ------------------------------------------------------------------
    # Injuries / lineups  (SOURCE: SportsDataIO primary)
    # ------------------------------------------------------------------

    def get_injuries(self, game_date: Optional[date] = None) -> list[dict]:
        """
        Return current injury report.

        SOURCE: SportsDataIO (PRIMARY: injury status / availability).
        """
        raw = fetch_injuries()
        return [
            {
                "player_id": r["player_id"],
                "player_name": r["player_name"],
                "team_id": r["team_id"],
                "status": r["status"],
                "description": r.get("injury", ""),
            }
            for r in raw
        ]

    def get_lineups(self, game_date: Optional[date] = None) -> list[dict]:
        """
        Return projected starters.

        SOURCE: SportsDataIO (PRIMARY: lineup / depth chart context).
        """
        ds = game_date if game_date is not None else today_eastern()
        return fetch_projected_lineups(ds)

    def get_depth_charts(self, team_id: Optional[str] = None) -> list[dict]:
        """
        Return depth chart for *team_id* (or all teams).

        SOURCE: SportsDataIO (PRIMARY: role / depth chart context).
        """
        charts = fetch_depth_charts()
        if team_id:
            return [r for r in charts if str(r.get("team_id", "")) == str(team_id)]
        return charts

    # ------------------------------------------------------------------
    # Player props / odds
    # ------------------------------------------------------------------

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        """
        SportsDataIO props endpoint (supplemental; primary is The Odds API).

        SOURCE: SportsDataIO odds endpoint (supplemental only).
        """
        # This provider's prop lines are not the primary odds source.
        # The Odds API is the primary source for sportsbook pricing.
        # Return empty so the registry falls through to the OddsAPI provider.
        return []

    # ------------------------------------------------------------------
    # Extended accessors (called directly by service layer, not registry)
    # ------------------------------------------------------------------

    def get_player_season_stats(self, season: str = _CURRENT_SEASON) -> list[dict]:
        """
        Return all player season stats.

        SOURCE: SportsDataIO (PRIMARY: season averages).
        """
        return fetch_player_season_stats(season)

    def get_player_game_logs(
        self, player_id: str, season: str = _CURRENT_SEASON, num_games: int = 10
    ) -> list[dict]:
        """
        Return recent game logs for *player_id*.

        SOURCE: SportsDataIO (PRIMARY: recent form / game logs).
        """
        return fetch_player_game_logs(player_id, season=season, num_games=num_games)

    def get_team_stats(self, season: str = _CURRENT_SEASON) -> list[dict]:
        """
        Return all team season stats.

        SOURCE: SportsDataIO (PRIMARY: team defensive stats).
        """
        return fetch_team_season_stats(season)
