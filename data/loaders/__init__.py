"""Small, mode-aware loading boundary for the Streamlit app and scanner."""

from __future__ import annotations

from datetime import date
from typing import Optional

from domain.entities import Game, OddsLine, Player
from providers.provider_registry import ProviderRegistry

_registry: ProviderRegistry | None = None


def _get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry.build()
    return _registry


def load_games(game_date: date) -> list[Game]:
    return _get_registry().get_games_for_date(game_date)


def load_players_for_game(
    game_id: str, candidate_player_ids: Optional[set[str]] = None,
) -> list[Player]:
    return _get_registry().get_players_for_game(game_id, candidate_player_ids)


def load_odds(game_date: date) -> list[OddsLine]:
    return _get_registry().get_player_props(game_date)


def load_final_player_totals(game_id: str) -> dict[str, dict[str, float]]:
    return _get_registry().get_final_player_totals(game_id)


def provider_status() -> dict[str, str]:
    return _get_registry().status()


def reset_registry() -> None:
    """Rebuild the provider when switching process modes in tests."""
    global _registry
    _registry = None
