"""
NBA Official provider stub.

The NBA provides official game-state data through its stats API.
Access to the official developer program is required.

Apply at: https://developer.nba.com/
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import DataSource, PropType
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class NBAOfficialProvider(BaseProvider):
    """
    NBA Official data provider.

    Requires NBA_OFFICIAL_API_KEY and approved developer program access.
    """

    source_name = DataSource.NBA_OFFICIAL

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        logger.info("NBA Official provider initialised (developer program access required)")

    def is_available(self) -> bool:
        return bool(self._key)

    def get_games_for_date(self, game_date: date) -> list[Game]:
        """
        Fetch today's games from NBA stats API.
        Endpoint: https://stats.nba.com/stats/scoreboardV2?GameDate={date}
        Requires NBA.com headers and may require additional authentication.
        """
        raise NotImplementedError("NBA Official: implement with approved API access")

    def get_players_for_game(self, game_id: str) -> list[Player]:
        raise NotImplementedError("NBA Official: implement with approved API access")

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        raise NotImplementedError("NBA Official does not provide sportsbook odds")

    def get_tracking_metrics(self, player_id: str) -> Optional[dict]:
        """
        NBA tracking data including touches, speed, distance.
        Endpoint: https://stats.nba.com/stats/playerdashptpass
        """
        raise NotImplementedError("NBA Official: implement with approved API access")
