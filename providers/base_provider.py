"""
Abstract base provider interface.

Every data source (SportsDataIO, Sportradar, The Odds API, CSV, sample
data, etc.) must subclass BaseProvider and implement the methods that its
API supports.  Methods that the source cannot support should raise
NotImplementedError — the ProviderRegistry will route around them.

All methods return normalised domain entities, never raw dicts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import DataSource, PropType


class BaseProvider(ABC):
    """
    Abstract provider interface.

    Subclasses must set `source_name` and implement whichever methods
    their upstream API supports.  The registry will call `is_available()`
    before delegating any method.
    """

    source_name: DataSource = DataSource.SAMPLE

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """
        Return True if this provider can serve live data right now.

        The default implementation returns False; concrete providers
        override this to check that their API key is present and (optionally)
        that a health-check endpoint responds.
        """
        return False

    # ------------------------------------------------------------------
    # Schedule / Games
    # ------------------------------------------------------------------

    @abstractmethod
    def get_games_for_date(self, game_date: date) -> list[Game]:
        """Return all NBA games scheduled for *game_date*."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Player data
    # ------------------------------------------------------------------

    @abstractmethod
    def get_players_for_game(self, game_id: str) -> list[Player]:
        """Return all players rostered for both teams in *game_id*."""
        raise NotImplementedError

    def get_player_context(self, player_id: str) -> Optional[Player]:
        """
        Return full player context (stats, role, injury, splits) for one
        player.  Providers that cannot do per-player lookups may leave this
        unimplemented.
        """
        raise NotImplementedError

    def get_player_recent_form(self, player_id: str, n: int = 10) -> list[dict]:
        """
        Return a list of game-log dicts for the last *n* games.

        Each dict should contain at minimum:
            date, points, rebounds, assists, minutes, threes, blocks, steals, turnovers
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Team / matchup context
    # ------------------------------------------------------------------

    def get_team_context(self, team_id: str) -> Optional[dict]:
        """Return team-level context (pace, off/def efficiency, record)."""
        raise NotImplementedError

    def get_team_defense(self, team_id: str) -> Optional[TeamDefense]:
        """Return the team's defensive profile broken down by position."""
        raise NotImplementedError

    def get_defense_vs_position(
        self, team_id: str, position: str, prop_type: PropType
    ) -> float:
        """
        Return the average stat allowed by *team_id* to players at
        *position* for *prop_type*.  Convenience wrapper over get_team_defense.
        """
        raise NotImplementedError

    def get_fantasy_points_allowed(
        self, team_id: str, position: str
    ) -> float:
        """
        Return fantasy points per game allowed by *team_id* to players at
        *position* (DraftKings scoring).
        """
        raise NotImplementedError

    def get_matchup_history(
        self, player_id: str, opponent_team_id: str
    ) -> list[dict]:
        """
        Return historical game logs for *player_id* against *opponent_team_id*.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Injury / lineup / depth chart
    # ------------------------------------------------------------------

    def get_injuries(self, game_date: Optional[date] = None) -> list[dict]:
        """
        Return the current injury report.

        Each dict should contain at minimum:
            player_id, player_name, team_id, status, description
        """
        raise NotImplementedError

    def get_lineups(self, game_date: Optional[date] = None) -> list[dict]:
        """
        Return confirmed lineup data for *game_date*.

        Each dict should contain at minimum:
            game_id, team_id, player_id, starting
        """
        raise NotImplementedError

    def get_depth_charts(self, team_id: Optional[str] = None) -> list[dict]:
        """Return depth chart data for one or all teams."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Odds and props
    # ------------------------------------------------------------------

    @abstractmethod
    def get_player_props(self, game_date: date) -> list[OddsLine]:
        """
        Return all available player prop lines for *game_date*.
        This is the primary odds-ingestion method called by the engine.
        """
        raise NotImplementedError

    def get_live_odds(self, game_date: date) -> list[OddsLine]:
        """
        Return live (in-game or near-real-time) odds for *game_date*.
        Falls back to get_player_props for providers without live feeds.
        """
        return self.get_player_props(game_date)

    def get_historical_odds(
        self,
        prop_type: PropType,
        days_back: int = 7,
    ) -> list[OddsLine]:
        """Return historical odds lines for backtesting purposes."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Advanced / tracking metrics
    # ------------------------------------------------------------------

    def get_tracking_metrics(self, player_id: str) -> Optional[dict]:
        """
        Return advanced tracking metrics for *player_id*.

        Expected keys (subset):
            touches, time_of_possession, rebound_chances,
            potential_assists, speed, distance
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        available = "available" if self.is_available() else "unavailable"
        return f"{self.__class__.__name__}(source={self.source_name}, status={available})"
