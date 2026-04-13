"""
RotoWire provider stub.

RotoWire does not have a documented public API.
This stub exists as a placeholder for future licensed feed integration.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import DataSource, PropType
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class RotoWireProvider(BaseProvider):
    """
    RotoWire provider stub.

    Not currently available via public API.
    Set ROTOWIRE_API_KEY to enable once licensed access is established.
    """

    source_name = DataSource.ROTOWIRE

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        logger.warning(
            "RotoWire provider: no public API available. "
            "Contact RotoWire for licensed data feed access."
        )

    def is_available(self) -> bool:
        return False

    def get_games_for_date(self, game_date: date) -> list[Game]:
        raise NotImplementedError("RotoWire: no public API – licensed access required")

    def get_players_for_game(self, game_id: str) -> list[Player]:
        raise NotImplementedError("RotoWire: no public API – licensed access required")

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        raise NotImplementedError("RotoWire: no public API – licensed access required")
