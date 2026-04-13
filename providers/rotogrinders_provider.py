"""
RotoGrinders provider stub.

RotoGrinders does not have a documented public API.
This stub exists as a placeholder for future licensed or private feed integration.

Options for integration:
1. Contact RotoGrinders for a data partnership / licensed feed.
2. Use their official export/import CSV workflow if available.
3. Use a custom connector if an API is provided under agreement.

DO NOT build brittle scraping-first architecture for this source.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import DataSource, PropType
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class RotoGrindersProvider(BaseProvider):
    """
    RotoGrinders provider stub.

    Not currently available via public API.
    Set ROTOGRINDERS_API_KEY to enable once licensed access is established.
    """

    source_name = DataSource.ROTOGRINDERS

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        logger.warning(
            "RotoGrinders provider: no public API available. "
            "Contact RotoGrinders for licensed data access."
        )

    def is_available(self) -> bool:
        # Always returns False until a licensed connector is implemented
        return False

    def get_games_for_date(self, game_date: date) -> list[Game]:
        raise NotImplementedError("RotoGrinders: no public API – licensed access required")

    def get_players_for_game(self, game_id: str) -> list[Player]:
        raise NotImplementedError("RotoGrinders: no public API – licensed access required")

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        raise NotImplementedError("RotoGrinders: no public API – licensed access required")
