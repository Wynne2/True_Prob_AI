"""
Sportradar provider.

Connects to the Sportradar NBA API (v8) for official game, player,
schedule, and team data.

API documentation: https://developer.sportradar.com/docs/read/basketball/NBA_v8

Note: Sportradar access is typically licensed. The trial tier is
available at https://developer.sportradar.com/
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Optional

import requests

from config import get_sportradar_config
from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import DataSource, Position, PropType
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class SportradarProvider(BaseProvider):
    """
    Sportradar NBA data provider.

    Implements schedules, player profiles, and game context data.
    Odds data is not available via standard Sportradar NBA feeds.
    """

    source_name = DataSource.SPORTRADAR

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        self._cfg = get_sportradar_config()
        # Sportradar rate-limits trial keys; add a small delay between calls
        self._call_delay: float = 1.0

    def is_available(self) -> bool:
        return bool(self._key)

    def _get(self, path: str) -> Optional[dict | list]:
        url = f"{self._cfg.base_url}/{path}?api_key={self._key}"
        try:
            time.sleep(self._call_delay)
            resp = requests.get(url, timeout=self._cfg.timeout)
            if resp.status_code == 403:
                logger.error("Sportradar: access denied – check API key / subscription")
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.error("Sportradar request error for %s: %s", url, exc)
            return None

    def get_games_for_date(self, game_date: date) -> list[Game]:
        year = game_date.year
        month = game_date.month
        day = game_date.day
        data = self._get(f"games/{year}/{month:02d}/{day:02d}/schedule.json")
        if not data:
            return []
        games = []
        for game_raw in data.get("games", []):
            try:
                home = game_raw.get("home", {})
                away = game_raw.get("away", {})
                g = Game(
                    game_id=game_raw.get("id", ""),
                    home_team_id=home.get("id", ""),
                    home_team_abbr=home.get("alias", ""),
                    away_team_id=away.get("id", ""),
                    away_team_abbr=away.get("alias", ""),
                    game_date=game_date,
                    data_source=DataSource.SPORTRADAR,
                )
                games.append(g)
            except Exception as exc:
                logger.warning("Failed to parse Sportradar game: %s", exc)
        return games

    def get_players_for_game(self, game_id: str) -> list[Player]:
        # Sportradar: use game summary to get rosters
        data = self._get(f"games/{game_id}/summary.json")
        if not data:
            return []
        players = []
        for team_key in ("home", "away"):
            team = data.get(team_key, {})
            team_abbr = team.get("alias", "")
            for player_raw in team.get("players", []):
                try:
                    p = Player(
                        player_id=player_raw.get("id", ""),
                        name=f"{player_raw.get('name', {}).get('first', '')} {player_raw.get('name', {}).get('last', '')}".strip(),
                        team_id=team.get("id", ""),
                        team_abbr=team_abbr,
                        position=Position.G,
                        data_source=DataSource.SPORTRADAR,
                    )
                    players.append(p)
                except Exception as exc:
                    logger.warning("Failed to parse Sportradar player: %s", exc)
        return players

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        # Sportradar NBA does not provide player props in standard feeds
        raise NotImplementedError
