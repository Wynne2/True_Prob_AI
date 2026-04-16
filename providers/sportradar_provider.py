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
from datetime import date, datetime, timezone
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

    @staticmethod
    def _parse_tip_off(scheduled_str: str) -> datetime | None:
        """Parse Sportradar's ISO-8601 scheduled time to a UTC-aware datetime."""
        if not scheduled_str:
            return None
        try:
            return datetime.fromisoformat(scheduled_str.replace("Z", "+00:00"))
        except Exception:
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
                venue = game_raw.get("venue", {})
                g = Game(
                    game_id=game_raw.get("id", ""),
                    home_team_id=home.get("id", ""),
                    home_team_abbr=home.get("alias", ""),
                    away_team_id=away.get("id", ""),
                    away_team_abbr=away.get("alias", ""),
                    game_date=game_date,
                    tip_off_time=self._parse_tip_off(game_raw.get("scheduled", "")),
                    arena=venue.get("name", ""),
                    city=venue.get("city", ""),
                    data_source=DataSource.SPORTRADAR,
                )
                games.append(g)
            except Exception as exc:
                logger.warning("Failed to parse Sportradar game: %s", exc)
        return games

    @staticmethod
    def _is_sportradar_id(game_id: str) -> bool:
        """
        Return True only for UUID-format IDs (e.g. 'd50df249-f9ad-4bde-...').

        Sportradar game IDs are UUIDs.  Sample data uses short readable IDs
        like 'g_bos_mia'; passing those to the API always produces a 404.
        """
        import re
        return bool(re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            game_id.lower(),
        ))

    def get_players_for_game(self, game_id: str) -> list[Player]:
        if not self._is_sportradar_id(game_id):
            logger.debug(
                "Sportradar: skipping get_players_for_game — '%s' is not a Sportradar UUID",
                game_id,
            )
            raise NotImplementedError  # let registry fall through to next provider

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
