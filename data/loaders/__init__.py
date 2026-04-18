"""
data.loaders package.

Re-exports all public loader functions so that existing imports like:
    from data.loaders import load_games, load_odds, load_players_for_game
continue to work after data/loaders/ became a package directory.

All functions delegate to the ProviderRegistry, which routes to the
correct source (Sportradar/SportsDataIO for slate, The Odds API for odds,
nba_api + SportsDataIO for player data).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import PropType

logger = logging.getLogger(__name__)

_registry = None


def _get_registry():
    global _registry
    if _registry is None:
        from providers.provider_registry import ProviderRegistry
        _registry = ProviderRegistry.build()
        logger.info("Provider registry initialised:\n%s", _registry.summary())
    return _registry


# ---------------------------------------------------------------------------
# Game slate  (SOURCE: Sportradar primary -> SportsDataIO fallback)
# ---------------------------------------------------------------------------

def load_games(game_date: date) -> list[Game]:
    """Load all NBA games for *game_date*."""
    return _get_registry().get_games_for_date(game_date)


# ---------------------------------------------------------------------------
# Players  (SOURCE: SportsDataIO primary + nba_api supplement)
# ---------------------------------------------------------------------------

def load_players_for_game(game_id: str) -> list[Player]:
    """Load all players for both teams in *game_id*."""
    return _get_registry().get_players_for_game(game_id)


def load_all_players_for_date(game_date: date) -> list[Player]:
    """Load every player appearing in any game on *game_date*."""
    games = load_games(game_date)
    seen: set[str] = set()
    players: list[Player] = []
    for game in games:
        for player in load_players_for_game(game.game_id):
            if player.player_id not in seen:
                seen.add(player.player_id)
                players.append(player)
    return players


def load_player(player_id: str) -> Optional[Player]:
    """Load full context for a single player."""
    return _get_registry().get_player_context(player_id)


# ---------------------------------------------------------------------------
# Team defence  (SOURCE: SportsDataIO + dvp_service derived)
# ---------------------------------------------------------------------------

def load_team_defense(team_id: str) -> Optional[TeamDefense]:
    """Load defensive profile for *team_id*."""
    return _get_registry().get_team_defense(team_id)


def load_defense_by_abbr(team_abbr: str) -> Optional[TeamDefense]:
    """Load defensive profile using team abbreviation."""
    # Try registry first (SportsDataIO), fall back to sample data
    registry = _get_registry()
    defense = registry.get_team_defense(team_abbr)
    if defense:
        return defense
    # Last resort: sample data lookup by abbr
    try:
        from data.sample_teams import DEFENSE_BY_TEAM_ABBR
        defense = DEFENSE_BY_TEAM_ABBR.get(team_abbr)
        if defense:
            return defense
    except ImportError:
        pass
    logger.warning("No defensive profile found for team abbr: %s", team_abbr)
    return None


# ---------------------------------------------------------------------------
# Odds  (SOURCE: The Odds API only)
# ---------------------------------------------------------------------------

def load_odds(game_date: date) -> list[OddsLine]:
    """Load all player prop lines for *game_date* across all books."""
    return _get_registry().get_player_props(game_date)


def load_odds_for_player(
    game_date: date, player_id: str, prop_type: Optional[PropType] = None
) -> list[OddsLine]:
    """Load odds filtered to one player (and optionally one prop type)."""
    all_lines = load_odds(game_date)
    result = [ol for ol in all_lines if ol.player_id == player_id]
    if prop_type:
        result = [ol for ol in result if ol.prop_type == prop_type]
    return result


# ---------------------------------------------------------------------------
# Injuries / lineup  (SOURCE: SportsDataIO primary)
# ---------------------------------------------------------------------------

def load_injuries(game_date: Optional[date] = None) -> list[dict]:
    """Load current injury report."""
    return _get_registry().get_injuries(game_date)


def load_lineups(game_date: Optional[date] = None) -> list[dict]:
    """Load confirmed starting lineups."""
    return _get_registry().get_lineups(game_date)


# ---------------------------------------------------------------------------
# Fantasy points allowed  (SOURCE: dvp_service derived)
# ---------------------------------------------------------------------------

def load_fpa(team_id: str, position: str) -> float:
    """Return fantasy points allowed per game by *team_id* to *position*."""
    return _get_registry().get_fantasy_points_allowed(team_id, position)


# ---------------------------------------------------------------------------
# Registry refresh
# ---------------------------------------------------------------------------

def reset_registry() -> None:
    """Force the registry to be rebuilt (useful in tests)."""
    global _registry
    _registry = None
