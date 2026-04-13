"""
StatMuse provider stub.

StatMuse provides historical stat lookups via a natural language API.
Licensed API access is required.

Contact: https://www.statmuse.com/
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import DataSource, PropType
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class StatMuseProvider(BaseProvider):
    """
    StatMuse provider.

    Requires STATMUSE_API_KEY and licensed API access.
    Best used for matchup history queries and historical stat lookups.
    """

    source_name = DataSource.STATMUSE

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        logger.info("StatMuse provider initialised (licensed access required)")

    def is_available(self) -> bool:
        return bool(self._key)

    def get_games_for_date(self, game_date: date) -> list[Game]:
        raise NotImplementedError("StatMuse: does not provide future game schedules")

    def get_players_for_game(self, game_id: str) -> list[Player]:
        raise NotImplementedError("StatMuse: does not provide per-game player lists")

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        raise NotImplementedError("StatMuse: does not provide sportsbook odds")

    def get_matchup_history(
        self, player_id: str, opponent_team_id: str
    ) -> list[dict]:
        """
        Query historical player performance vs opponent.
        Requires licensed StatMuse API access.
        """
        raise NotImplementedError("StatMuse: implement with licensed API credentials")
