"""
FantasyPros provider stub.

FantasyPros provides fantasy rankings, projections, and consensus data.
This stub requires a licensed API key.

API info: https://www.fantasypros.com/nba/
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import DataSource, PropType
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class FantasyProsProvider(BaseProvider):
    """
    FantasyPros provider.

    Requires a licensed API key (FANTASYPROS_API_KEY).
    Implements fantasy projections and rankings that enrich the model.
    """

    source_name = DataSource.FANTASYPROS

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        logger.info("FantasyPros provider initialised (licensed API required)")

    def is_available(self) -> bool:
        return bool(self._key)

    def get_games_for_date(self, game_date: date) -> list[Game]:
        raise NotImplementedError("FantasyPros does not provide game schedules")

    def get_players_for_game(self, game_id: str) -> list[Player]:
        raise NotImplementedError("FantasyPros does not provide per-game player lists")

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        raise NotImplementedError("FantasyPros does not provide sportsbook odds")

    def get_player_context(self, player_id: str) -> Optional[Player]:
        """
        Fetch FantasyPros projection context for a player.
        Implementation requires licensed API access.
        Endpoint: GET /api/nba/v1/players/{player_id}/projections
        """
        logger.debug("FantasyPros.get_player_context: not yet implemented")
        raise NotImplementedError
