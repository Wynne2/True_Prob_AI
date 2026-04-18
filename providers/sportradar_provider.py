"""
Sportradar Synergy Basketball API provider.

API: https://api.sportradar.com/synergy/basketball/nba/
Auth: x-api-key request header  (NOT a query parameter)

Available endpoints at this subscription tier:
  GET /competitiondefinitions  - competition groups (Playoffs, Regular Season, Pre-Season)
  GET /seasons                 - seasons with data (currently 2021-22)
  GET /games                   - all games in the subscribed season
  GET /teams                   - teams in the subscribed league
  GET /playercareers           - player career histories (multi-season team mapping)

Endpoints requiring higher subscription tiers (return 404 at free/trial):
  /players, /playtypestats, /playbypays, /possessionevents, /possessionreports

Architecture role of this provider:
  - Game slate / schedule: NOT used (SportsDataIO GamesByDate is the primary slate source)
  - Player careers / team history: available from /playercareers
  - Historical game context: available from /games (currently 2021-22 season)
  - Advanced play-type analytics: requires higher subscription tier
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Optional

import requests

from config import get_sportradar_config
from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import DataSource, Position, PropType
from providers.base_provider import BaseProvider
from services.cache_service import get_cache

logger = logging.getLogger(__name__)

_CACHE = get_cache("sportradar_synergy", default_ttl=86_400)  # 24-hour default TTL

# Rate-limit: Synergy API enforces per-second limits; sleep between calls
_CALL_DELAY: float = 1.2


class SportradarProvider(BaseProvider):
    """
    Sportradar Synergy Basketball API provider.

    Authentication uses the x-api-key HTTP header.  Schedules / game slates for
    today's games are NOT sourced here — SportsDataIO handles that role.  This
    provider exposes player career history and historical Synergy game data.
    """

    source_name = DataSource.SPORTRADAR

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        self._cfg = get_sportradar_config()
        self._headers = {
            "accept": "application/json",
            "x-api-key": api_key,
        }

    def is_available(self) -> bool:
        return bool(self._key)

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    def _get(self, path: str, **params) -> Optional[dict | list]:
        """
        GET {base_url}/{path} with x-api-key header authentication.

        Returns the parsed JSON body, or None on any error.
        Respects a per-call sleep to avoid rate-limit (429) responses.
        """
        url = f"{self._cfg.base_url}/{path}"
        try:
            time.sleep(_CALL_DELAY)
            resp = requests.get(
                url,
                headers=self._headers,
                params=params or None,
                timeout=self._cfg.timeout,
            )
            if resp.status_code == 401:
                logger.error("Sportradar Synergy: invalid API key")
                return None
            if resp.status_code == 403:
                logger.error(
                    "Sportradar Synergy: access denied for %s — check subscription",
                    path,
                )
                return None
            if resp.status_code == 404:
                logger.debug("Sportradar Synergy: endpoint not found: %s", path)
                return None
            if resp.status_code == 429:
                logger.warning("Sportradar Synergy: rate limited on %s, backing off", path)
                time.sleep(5)
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.error("Sportradar Synergy request error for %s: %s", url, exc)
            return None

    # ------------------------------------------------------------------
    # Competition / Season helpers
    # ------------------------------------------------------------------

    def get_competition_definitions(self) -> list[dict]:
        """
        Return competition group definitions for the NBA.

        Source: GET /competitiondefinitions
        Includes Playoffs, Pre-Season, and Regular Season competition IDs.
        """
        cache_key = "competition_definitions"
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached

        data = self._get("competitiondefinitions")
        if not data:
            return []

        result = data.get("data", []) if isinstance(data, dict) else []
        _CACHE.set(cache_key, result)
        logger.info("Sportradar Synergy: loaded %d competition definitions", len(result))
        return result

    def get_seasons(self) -> list[dict]:
        """
        Return seasons available under this subscription.

        Source: GET /seasons
        """
        cache_key = "synergy_seasons"
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached

        data = self._get("seasons")
        if not data:
            return []

        seasons = [
            entry.get("data", entry)
            for entry in data.get("data", [])
            if isinstance(data, dict)
        ]
        _CACHE.set(cache_key, seasons)
        return seasons

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------

    def get_teams(self) -> list[dict]:
        """
        Return NBA teams from the Synergy API.

        Source: GET /teams
        Each team has: id, abbr, fullName, conference, division.
        """
        cache_key = "synergy_teams"
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached

        data = self._get("teams")
        if not data:
            return []

        teams = [
            entry.get("data", entry)
            for entry in data.get("data", [])
            if isinstance(data, dict)
        ]
        _CACHE.set(cache_key, teams)
        logger.info("Sportradar Synergy: loaded %d teams", len(teams))
        return teams

    # ------------------------------------------------------------------
    # Games (historical — subscription-limited season)
    # ------------------------------------------------------------------

    def get_all_games(self, competition_id: Optional[str] = None) -> list[dict]:
        """
        Return all games in the subscribed season from the Synergy API.

        Source: GET /games
        NOTE: The subscribed data tier may only include a specific historical
        season (e.g. 2021-22).  This is NOT a source for today's live slate —
        use SportsDataIO GamesByDate for that.

        Each game has: id, name, homeTeam, awayTeam, date, status, competition.
        """
        cache_key = f"synergy_games_{competition_id or 'all'}"
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached

        params = {}
        if competition_id:
            params["competitionId"] = competition_id

        data = self._get("games", **params)
        if not data:
            return []

        games = [
            entry.get("data", entry)
            for entry in data.get("data", [])
            if isinstance(data, dict)
        ]
        _CACHE.set(cache_key, games)
        logger.debug(
            "Sportradar Synergy: loaded %d historical games (subscription: %s)",
            len(games),
            "all" if not competition_id else competition_id,
        )
        return games

    # ------------------------------------------------------------------
    # Player Careers
    # ------------------------------------------------------------------

    def get_player_careers(self) -> list[dict]:
        """
        Return player career season histories from the Synergy API.

        Source: GET /playercareers
        Each record contains a player's history: [{player, season, team, league}].
        Useful for mapping player IDs across seasons and tracking team history.
        """
        cache_key = "synergy_player_careers"
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached

        data = self._get("playercareers")
        if not data:
            return []

        careers = []
        for entry in data.get("data", []):
            career_data = entry.get("data", entry)
            player_items = career_data.get("items", [])
            if not player_items:
                continue
            # Flatten: one record per (player, season, team)
            for item in player_items:
                player = item.get("player", {})
                season = item.get("season", {})
                team = item.get("team", {})
                careers.append({
                    "player_id": career_data.get("id", ""),
                    "sportradar_player_id": player.get("id", ""),
                    "player_name": player.get("name", ""),
                    "first_name": player.get("firstName", ""),
                    "last_name": player.get("lastName", ""),
                    "season": season.get("name", ""),
                    "team_id": team.get("id", ""),
                    "team_abbr": team.get("abbr", ""),
                    "team_name": team.get("fullName", ""),
                })

        _CACHE.set(cache_key, careers)
        logger.info("Sportradar Synergy: loaded %d player career records", len(careers))
        return careers

    # ------------------------------------------------------------------
    # BaseProvider interface implementation
    # ------------------------------------------------------------------

    def get_games_for_date(self, game_date: date) -> list[Game]:
        """
        Return games scheduled for *game_date*.

        NOTE: The Synergy Basketball API is a historical analytics database.
        The current subscription contains only historical season data and does
        not provide a live game slate.  SportsDataIO GamesByDate is the
        authoritative source for today's slate — this method returns empty
        to allow the registry to route to that source.
        """
        return []

    def get_players_for_game(self, game_id: str) -> list[Player]:
        """
        Not implemented: Synergy API does not provide game rosters directly.
        Use SportsDataIO or nba_api for player rosters.
        """
        raise NotImplementedError

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        """
        Not implemented: Synergy API does not provide betting prop lines.
        Use The Odds API for prop lines.
        """
        raise NotImplementedError
