"""
nba_api Provider.

Implements the BaseProvider interface using the nba_api Python package.
This provider does NOT require an API key — nba_api uses public NBA.com
endpoints.

Role in the engine:
  - PRIMARY source for usage rate, touches, possessions, pace, splits.
  - Provides player tracking and advanced context.
  - NEVER called per-prop; all data is fetched in batches, cached, and
    served through the service layer.

Data is fetched via data/loaders/nba_api_loader.py which handles caching
and rate-limit safety.  This provider wraps those loaders behind the
BaseProvider interface so the ProviderRegistry can route to it.

SOURCE: nba_api Python library (no API key required).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from data.loaders.nba_api_loader import (
    fetch_recent_player_logs_batch,
    fetch_today_player_tracking_batch,
    fetch_usage_dashboard_batch,
    index_by_player_id,
)
from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import DataSource, InjuryStatus, Position
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)

_CURRENT_SEASON = "2024-25"


class NBAApiProvider(BaseProvider):
    """
    nba_api data provider.

    Handles batch-fetched advanced stats, player tracking, game logs,
    and split context.  No API key required.

    SOURCE: nba_api Python library (PRIMARY: usage, touches, possessions, splits).
    """

    source_name = DataSource.NBA_API

    def __init__(self, season: str = _CURRENT_SEASON) -> None:
        self._season = season
        # Lazy-load indexes; populated on first access
        self._usage_index: dict[str, dict] = {}
        self._tracking_index: dict[str, dict] = {}

    def is_available(self) -> bool:
        """nba_api is always available (no API key required)."""
        try:
            import nba_api  # noqa: F401
            return True
        except ImportError:
            logger.warning("nba_api package not installed; run: pip install nba_api")
            return False

    # ------------------------------------------------------------------
    # Required abstract methods (not primary use case for this provider;
    # these return empty so the registry falls through to SportsDataIO)
    # ------------------------------------------------------------------

    def get_games_for_date(self, game_date: date) -> list[Game]:
        """nba_api is not the primary slate source; return empty."""
        return []

    def get_players_for_game(self, game_id: str) -> list[Player]:
        """Slate player lists come from SportsDataIO; return empty."""
        return []

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        """Odds come from The Odds API; return empty."""
        return []

    # ------------------------------------------------------------------
    # Tracking / advanced context
    # ------------------------------------------------------------------

    def get_tracking_metrics(self, player_id: str) -> Optional[dict]:
        """
        Return cached tracking metrics for *player_id*.

        SOURCE: nba_api LeagueDashPtStats (PRIMARY: touches, time of possession).
        """
        if not self._tracking_index:
            self._warm_tracking()
        return self._tracking_index.get(player_id)

    def get_player_recent_form(self, player_id: str, n: int = 10) -> list[dict]:
        """
        Return last *n* game logs for *player_id*.

        SOURCE: nba_api PlayerGameLog.
        """
        logs = fetch_recent_player_logs_batch([player_id], last_n=n, season=self._season)
        return logs.get(player_id, [])

    def get_player_context(self, player_id: str) -> Optional[Player]:
        """
        Return a minimal Player with usage/tracking fields populated.

        Full player context is assembled by PlayerContextService; this
        method is a lightweight fallback for direct registry calls.
        """
        if not self._usage_index:
            self._warm_usage()

        rec = self._usage_index.get(player_id)
        if not rec:
            return None

        return Player(
            player_id=player_id,
            name=rec.get("player_name", ""),
            team_id=rec.get("team_id", ""),
            team_abbr=rec.get("team_abbr", ""),
            position=Position.G,
            usage_rate=float(rec.get("usg_pct", 0) or 0),
            data_source=DataSource.NBA_API,
        )

    # ------------------------------------------------------------------
    # Bulk warm-up helpers (called lazily on first access)
    # ------------------------------------------------------------------

    def _warm_usage(self) -> None:
        """Fetch and index the league-wide advanced dashboard."""
        logger.info("NBAApiProvider: warming usage dashboard")
        records = fetch_usage_dashboard_batch(season=self._season)
        self._usage_index = index_by_player_id(records)

    def _warm_tracking(self) -> None:
        """Fetch and index the possessions tracking dashboard."""
        logger.info("NBAApiProvider: warming tracking dashboard")
        records = fetch_today_player_tracking_batch(
            season=self._season, pt_measure_type="Possessions"
        )
        self._tracking_index = index_by_player_id(records)

    def warm_all(self) -> None:
        """Pre-warm all batch indexes (call once at start of daily run)."""
        self._warm_usage()
        self._warm_tracking()
