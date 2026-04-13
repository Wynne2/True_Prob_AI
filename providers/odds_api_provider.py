"""
The Odds API provider.

Connects to https://api.the-odds-api.com for:
- Live sportsbook player props
- Multi-book odds comparison
- Upcoming game events

API documentation: https://the-odds-api.com/liveapi/guides/v4/

Endpoints used:
  GET /v4/sports/{sport}/odds/ → game-level moneyline, spread, totals
  GET /v4/sports/{sport}/events/{event_id}/odds → player props for one game
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import requests

from config import get_odds_api_config
from domain.constants import PROP_ALIAS_MAP
from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import BookName, DataSource, PropType
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)

# Map Odds API bookmaker keys to internal BookName enum
_BOOK_MAP: dict[str, BookName] = {
    "fanduel": BookName.FANDUEL,
    "draftkings": BookName.DRAFTKINGS,
    "betmgm": BookName.BETMGM,
    "caesars": BookName.CAESARS,
    "pointsbet_us": BookName.POINTSBET,
    "betrivers": BookName.BETRIVERS,
    "bovada": BookName.BOVADA,
    "bet365": BookName.BET365,
    "pinnacle": BookName.PINNACLE,
}


class OddsAPIProvider(BaseProvider):
    """
    The Odds API provider for live multi-book odds and player props.
    """

    source_name = DataSource.ODDS_API

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        self._cfg = get_odds_api_config()

    def is_available(self) -> bool:
        return bool(self._key)

    def _get(self, path: str, **params) -> Optional[list | dict]:
        url = f"{self._cfg.base_url}/{path}"
        params["apiKey"] = self._key
        try:
            resp = requests.get(url, params=params, timeout=self._cfg.timeout)
            if resp.status_code == 401:
                logger.error("The Odds API: invalid API key")
                return None
            if resp.status_code == 429:
                logger.warning("The Odds API: rate limit exceeded")
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.error("The Odds API request error: %s", exc)
            return None

    def _get_events(self, game_date: date) -> list[dict]:
        """Fetch upcoming events for the NBA on *game_date*."""
        data = self._get(
            f"sports/{self._cfg.sport_key}/odds",
            regions=self._cfg.regions,
            markets="h2h",
            oddsFormat=self._cfg.odds_format,
            dateFormat="iso",
        )
        if not data:
            return []
        date_str = game_date.isoformat()
        return [
            e for e in data
            if e.get("commence_time", "").startswith(date_str)
        ]

    def get_games_for_date(self, game_date: date) -> list[Game]:
        events = self._get_events(game_date)
        games = []
        for evt in events:
            try:
                home = evt.get("home_team", "")
                away = evt.get("away_team", "")
                g = Game(
                    game_id=evt.get("id", ""),
                    home_team_id=home.lower().replace(" ", "_"),
                    home_team_abbr=self._abbr(home),
                    away_team_id=away.lower().replace(" ", "_"),
                    away_team_abbr=self._abbr(away),
                    game_date=game_date,
                    data_source=DataSource.ODDS_API,
                )
                games.append(g)
            except Exception as exc:
                logger.warning("Failed to parse event: %s", exc)
        return games

    def get_players_for_game(self, game_id: str) -> list[Player]:
        # The Odds API does not provide player roster data
        raise NotImplementedError

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        """
        Fetch player props for all NBA events on *game_date*.

        For each event, fetches player_points, player_rebounds, etc.
        """
        events = self._get_events(game_date)
        all_lines: list[OddsLine] = []

        # The prop markets we request, split into two batches to avoid API limits
        markets_batch1 = "player_points,player_rebounds,player_assists"
        markets_batch2 = "player_threes,player_blocks,player_steals"

        for evt in events:
            event_id = evt.get("id", "")
            for markets in (markets_batch1, markets_batch2):
                data = self._get(
                    f"sports/{self._cfg.sport_key}/events/{event_id}/odds",
                    regions=self._cfg.regions,
                    markets=markets,
                    oddsFormat=self._cfg.odds_format,
                )
                if not data:
                    continue

                bookmakers = data.get("bookmakers", [])
                for book_data in bookmakers:
                    book_key = book_data.get("key", "")
                    book = _BOOK_MAP.get(book_key, BookName.SAMPLE)

                    for market in book_data.get("markets", []):
                        market_key = market.get("key", "")
                        prop_type = PROP_ALIAS_MAP.get(market_key)
                        if not prop_type:
                            continue

                        # Group outcomes by player (description)
                        player_outcomes: dict[str, dict] = {}
                        for outcome in market.get("outcomes", []):
                            desc = outcome.get("description", "")
                            name = outcome.get("name", "")  # 'Over' or 'Under'
                            price = outcome.get("price", -110)
                            point = outcome.get("point", 0.5)
                            if desc not in player_outcomes:
                                player_outcomes[desc] = {"point": point}
                            if name.lower() == "over":
                                player_outcomes[desc]["over_odds"] = int(price)
                            elif name.lower() == "under":
                                player_outcomes[desc]["under_odds"] = int(price)

                        for player_name, odds in player_outcomes.items():
                            if "over_odds" not in odds or "under_odds" not in odds:
                                continue
                            line = OddsLine(
                                book=book,
                                player_id=player_name.lower().replace(" ", "_"),
                                player_name=player_name,
                                prop_type=prop_type,
                                line=float(odds.get("point", 0.5)),
                                over_odds=odds["over_odds"],
                                under_odds=odds["under_odds"],
                                game_id=event_id,
                                data_source=DataSource.ODDS_API,
                            )
                            all_lines.append(line)

        logger.info("The Odds API: loaded %d prop lines for %s", len(all_lines), game_date)
        return all_lines

    @staticmethod
    def _abbr(full_name: str) -> str:
        """Extract a 3-letter abbreviation from a team full name."""
        # Very rough; real code would use a lookup table
        parts = full_name.split()
        if len(parts) >= 2:
            return parts[-1][:3].upper()
        return full_name[:3].upper()
