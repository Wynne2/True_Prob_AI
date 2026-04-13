"""
SportsDataIO provider.

Connects to the SportsDataIO NBA API for:
- Schedules and game data
- Player stats and injuries
- Depth charts / lineups
- Betting odds and player props

API documentation: https://sportsdata.io/developers/api-documentation/nba

All responses are normalised into domain entities via data/normalizers.py.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import requests

from config import get_sdio_config
from data.normalizers import raw_dict_to_odds_line, raw_dict_to_player
from domain.constants import PROP_ALIAS_MAP
from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import (
    BookName,
    DataSource,
    InjuryStatus,
    PlayerRole,
    Position,
    PropType,
)
from providers.base_provider import BaseProvider
from utils.date_utils import format_date

logger = logging.getLogger(__name__)


class SportsDataIOProvider(BaseProvider):
    """
    SportsDataIO NBA data provider.

    Implements schedules, players, injuries, lineups, and odds endpoints.
    """

    source_name = DataSource.SPORTSDATAIO

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        self._cfg = get_sdio_config()
        self._session = requests.Session()
        self._session.headers.update({"Ocp-Apim-Subscription-Key": api_key})

    def is_available(self) -> bool:
        return bool(self._key)

    def _get(self, endpoint: str, base: Optional[str] = None, **params) -> Optional[dict | list]:
        url = f"{base or self._cfg.base_url}/{endpoint}"
        try:
            resp = self._session.get(url, params=params, timeout=self._cfg.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            logger.error("SportsDataIO HTTP error %s for %s: %s", resp.status_code, url, e)
        except requests.RequestException as e:
            logger.error("SportsDataIO request error for %s: %s", url, e)
        return None

    def get_games_for_date(self, game_date: date) -> list[Game]:
        date_str = format_date(game_date)
        data = self._get(f"scores/json/GamesByDate/{date_str}")
        if not data:
            return []
        games = []
        for raw in data:
            try:
                g = Game(
                    game_id=str(raw.get("GameID", "")),
                    home_team_id=str(raw.get("HomeTeamID", "")),
                    home_team_abbr=raw.get("HomeTeam", ""),
                    away_team_id=str(raw.get("AwayTeamID", "")),
                    away_team_abbr=raw.get("AwayTeam", ""),
                    game_date=game_date,
                    game_total=float(raw.get("OverUnder", 0) or 0),
                    home_spread=float(raw.get("PointSpread", 0) or 0),
                    data_source=DataSource.SPORTSDATAIO,
                )
                games.append(g)
            except Exception as exc:
                logger.warning("Failed to parse game: %s", exc)
        return games

    def get_players_for_game(self, game_id: str) -> list[Player]:
        # SportsDataIO returns box score / game participants
        data = self._get(f"stats/json/PlayerGameStatsByGame/{game_id}")
        if not data:
            return []
        return self._parse_players(data)

    def _parse_players(self, raw_list: list) -> list[Player]:
        players = []
        for raw in raw_list:
            try:
                p = Player(
                    player_id=str(raw.get("PlayerID", "")),
                    name=raw.get("Name", ""),
                    team_id=str(raw.get("TeamID", "")),
                    team_abbr=raw.get("Team", ""),
                    position=Position.G,  # will be refined
                    minutes_per_game=float(raw.get("Minutes", 0) or 0),
                    points_per_game=float(raw.get("Points", 0) or 0),
                    rebounds_per_game=float(raw.get("Rebounds", 0) or 0),
                    assists_per_game=float(raw.get("Assists", 0) or 0),
                    threes_per_game=float(raw.get("ThreePointersMade", 0) or 0),
                    blocks_per_game=float(raw.get("BlockedShots", 0) or 0),
                    steals_per_game=float(raw.get("Steals", 0) or 0),
                    turnovers_per_game=float(raw.get("Turnovers", 0) or 0),
                    data_source=DataSource.SPORTSDATAIO,
                )
                players.append(p)
            except Exception as exc:
                logger.warning("Failed to parse player: %s", exc)
        return players

    def get_injuries(self, game_date: Optional[date] = None) -> list[dict]:
        data = self._get("scores/json/InjuredPlayers")
        if not data:
            return []
        results = []
        for raw in data:
            results.append({
                "player_id": str(raw.get("PlayerID", "")),
                "player_name": raw.get("Name", ""),
                "team_id": str(raw.get("TeamID", "")),
                "status": raw.get("Status", "Active"),
                "description": raw.get("InjuryDescription", ""),
            })
        return results

    def get_lineups(self, game_date: Optional[date] = None) -> list[dict]:
        # SportsDataIO projected starters
        date_str = format_date(game_date) if game_date else format_date(date.today())
        data = self._get(f"projections/json/ProjectedPlayerGameStatsByDate/{date_str}")
        if not data:
            return []
        return [
            {
                "player_id": str(r.get("PlayerID", "")),
                "team_id": str(r.get("TeamID", "")),
                "starting": bool(r.get("Started", False)),
            }
            for r in data
        ]

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        """
        Fetch player prop odds via SportsDataIO betting endpoints.
        Endpoint: /odds/json/PlayerPropsByDate/{date}
        """
        date_str = format_date(game_date)
        data = self._get(
            f"odds/json/PlayerPropsByDate/{date_str}",
            base=self._cfg.odds_base_url,
        )
        if not data:
            return []

        lines = []
        for raw in data:
            try:
                prop_type_str = raw.get("BetType", "")
                prop_type = PROP_ALIAS_MAP.get(prop_type_str.lower())
                if not prop_type:
                    continue

                for book in raw.get("HomeTeamPlayerProps", []) + raw.get("AwayTeamPlayerProps", []):
                    for prop in book.get("PlayerProps", []):
                        line = OddsLine(
                            book=BookName.SAMPLE,  # map book name
                            player_id=str(prop.get("PlayerID", "")),
                            player_name=prop.get("PlayerName", ""),
                            prop_type=prop_type,
                            line=float(prop.get("OverUnder", 0)),
                            over_odds=int(prop.get("OverPayout", -110)),
                            under_odds=int(prop.get("UnderPayout", -110)),
                            game_id=str(raw.get("GameId", "")),
                            data_source=DataSource.SPORTSDATAIO,
                        )
                        lines.append(line)
            except Exception as exc:
                logger.warning("Failed to parse prop line: %s", exc)

        return lines
