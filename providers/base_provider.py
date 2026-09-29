"""Shared contract for the demo and BALLDONTLIE pregame data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

from domain.entities import Game, OddsLine, Player
from domain.enums import DataSource


class BaseProvider(ABC):
    source_name: DataSource

    @abstractmethod
    def get_games_for_date(self, game_date: date) -> list[Game]:
        raise NotImplementedError

    @abstractmethod
    def get_players_for_game(
        self, game_id: str, candidate_player_ids: Optional[set[str]] = None,
    ) -> list[Player]:
        raise NotImplementedError

    @abstractmethod
    def get_player_props(self, game_date: date) -> list[OddsLine]:
        raise NotImplementedError

    def get_final_player_totals(self, game_id: str) -> dict[str, dict[str, float]]:
        raise NotImplementedError("Final results are available only from the live provider.")
