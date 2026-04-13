"""
Sample data provider  –  always-available fallback.

Returns realistic sample data so the platform works immediately without
any API keys.  This is the last provider in the registry chain.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import DataSource, PropType
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class SampleProvider(BaseProvider):
    """Always-available provider backed by static sample data."""

    source_name = DataSource.SAMPLE

    def is_available(self) -> bool:
        return True

    def get_games_for_date(self, game_date: date) -> list[Game]:
        from data.sample_games import get_sample_games
        games = get_sample_games()
        # Update game dates to today so they always appear current
        for g in games:
            g.game_date = game_date
        return games

    def get_players_for_game(self, game_id: str) -> list[Player]:
        from data.sample_games import get_sample_games
        from data.sample_players import PLAYERS_BY_TEAM

        # Find the game
        games = get_sample_games()
        game = next((g for g in games if g.game_id == game_id), None)
        if game is None:
            return []

        home_players = PLAYERS_BY_TEAM.get(game.home_team_abbr, [])
        away_players = PLAYERS_BY_TEAM.get(game.away_team_abbr, [])
        return home_players + away_players

    def get_player_context(self, player_id: str) -> Optional[Player]:
        from data.sample_players import PLAYER_BY_ID
        return PLAYER_BY_ID.get(player_id)

    def get_team_defense(self, team_id: str) -> Optional[TeamDefense]:
        from data.sample_teams import DEFENSE_BY_TEAM_ID, DEFENSE_BY_TEAM_ABBR
        defense = DEFENSE_BY_TEAM_ID.get(team_id)
        if defense is None:
            defense = DEFENSE_BY_TEAM_ABBR.get(team_id)
        return defense

    def get_defense_vs_position(
        self, team_id: str, position: str, prop_type: PropType
    ) -> float:
        from data.sample_teams import DEFENSE_BY_TEAM_ID, DEFENSE_BY_TEAM_ABBR
        defense = DEFENSE_BY_TEAM_ID.get(team_id) or DEFENSE_BY_TEAM_ABBR.get(team_id)
        if defense is None:
            return 0.0
        # Return appropriate defensive stat based on prop type and position
        pos = position.upper()
        if prop_type == PropType.POINTS:
            return getattr(defense, f"pts_allowed_{pos.lower()}", 0.0)
        elif prop_type == PropType.REBOUNDS:
            return getattr(defense, f"reb_allowed_{pos.lower()}", 0.0)
        elif prop_type == PropType.ASSISTS:
            return getattr(defense, f"ast_allowed_{pos.lower()}", 0.0)
        elif prop_type == PropType.THREES:
            return getattr(defense, f"threes_allowed_{pos.lower()}", 0.0)
        return 0.0

    def get_fantasy_points_allowed(self, team_id: str, position: str) -> float:
        from data.sample_teams import DEFENSE_BY_TEAM_ID, DEFENSE_BY_TEAM_ABBR
        defense = DEFENSE_BY_TEAM_ID.get(team_id) or DEFENSE_BY_TEAM_ABBR.get(team_id)
        if defense is None:
            return 0.0
        pos = position.lower()
        return getattr(defense, f"fpa_{pos}", 0.0)

    def get_injuries(self, game_date: Optional[date] = None) -> list[dict]:
        # Return a minimal injury stub based on sample player statuses
        from data.sample_players import SAMPLE_PLAYERS
        from domain.enums import InjuryStatus
        results = []
        for p in SAMPLE_PLAYERS:
            if p.injury_status != InjuryStatus.ACTIVE:
                results.append({
                    "player_id": p.player_id,
                    "player_name": p.name,
                    "team_id": p.team_id,
                    "status": p.injury_status.value,
                    "description": f"{p.name} listed as {p.injury_status.value}",
                })
        return results

    def get_lineups(self, game_date: Optional[date] = None) -> list[dict]:
        from data.sample_players import SAMPLE_PLAYERS
        return [
            {
                "player_id": p.player_id,
                "team_id": p.team_id,
                "starting": p.is_starter,
            }
            for p in SAMPLE_PLAYERS
        ]

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        from data.sample_games import get_sample_odds
        return get_sample_odds()

    def get_matchup_history(
        self, player_id: str, opponent_team_id: str
    ) -> list[dict]:
        # No per-opponent history in sample data; return empty
        return []
