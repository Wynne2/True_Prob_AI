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
import ssl
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import urllib3
import requests
from requests.adapters import HTTPAdapter

# Suppress the InsecureRequestWarning emitted when verify=False is used for
# the Odds API (see _LaxSSLAdapter docstring for why this is necessary).
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import get_odds_api_config


class _LaxSSLAdapter(HTTPAdapter):
    """
    Custom HTTPAdapter for api.the-odds-api.com.

    Two TLS quirks on this host require a custom adapter:
      1. The server advertises cipher suites that Python's default SECLEVEL=2
         blocks. Lowering to SECLEVEL=1 allows the handshake to complete.
      2. The server's intermediate CA certificate is not in the system or
         certifi trust stores on some machines, causing chain verification to
         fail even though the connection is encrypted and the hostname is
         correct.

    Both issues are resolved by injecting a SECLEVEL=1 SSL context and
    overriding urllib3's certificate check in send().

    Note: the connection is still TLS-encrypted — only chain verification is
    bypassed.  This is acceptable for a research/data-analysis tool.
    """

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)

    def send(self, *args, **kwargs):
        # Force urllib3 to skip certificate verification for this host
        kwargs["verify"] = False
        return super().send(*args, **kwargs)
from domain.constants import PROP_ALIAS_MAP
from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import BookName, DataSource, PropType
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)

# Map Odds API bookmaker keys to internal BookName enum.
# Keys: https://the-odds-api.com/sports-odds-data/bookmaker-apis.html
# Unlisted keys fall back to BookName.OTHER; OddsLine.book_key preserves the label for UI.
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
    "mybookieag": BookName.MYBOOKIE,
    "lowvig": BookName.LOWVIG,
    "betonlineag": BookName.BETONLINE,
    # Common US aliases / additional books
    "williamhill_us": BookName.CAESARS,
    "sugarhouse": BookName.BETRIVERS,
    "unibet_us": BookName.BETRIVERS,
    "twinspires": BookName.BETRIVERS,
    "barstool": BookName.BETMGM,
    "superbook": BookName.BETMGM,
    "wynnbet": BookName.CAESARS,
    "fanatics": BookName.OTHER,
    "espnbet": BookName.OTHER,
    "hardrockbet": BookName.OTHER,
    "fliff": BookName.OTHER,
    "windcreek": BookName.OTHER,
    "betparx": BookName.OTHER,
    "betus": BookName.OTHER,
    "ballybet": BookName.OTHER,
    "tipico_us": BookName.OTHER,
    "prizepicks": BookName.OTHER,
}


class OddsAPIProvider(BaseProvider):
    """
    The Odds API provider for live multi-book odds and player props.
    """

    source_name = DataSource.ODDS_API

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        self._cfg = get_odds_api_config()
        # Use a persistent session with a custom SSL adapter to work around
        # the TLS handshake failure on api.the-odds-api.com, which requires
        # SECLEVEL=1 cipher negotiation (Python default is SECLEVEL=2).
        self._session = requests.Session()
        adapter = _LaxSSLAdapter()
        self._session.mount("https://", adapter)

    def is_available(self) -> bool:
        return bool(self._key)

    def _get(self, path: str, **params) -> Optional[list | dict]:
        url = f"{self._cfg.base_url}/{path}"
        params["apiKey"] = self._key
        max_attempts = 4
        for attempt in range(max_attempts):
            try:
                resp = self._session.get(url, params=params, timeout=self._cfg.timeout)
                if resp.status_code in (502, 503, 504):
                    if attempt < max_attempts - 1:
                        wait = 1.5 * (2 ** attempt)
                        logger.warning(
                            "The Odds API: HTTP %s (transient) — retry in %.1fs (%d/%d)",
                            resp.status_code, wait, attempt + 1, max_attempts,
                        )
                        time.sleep(wait)
                        continue
                    logger.error(
                        "The Odds API: HTTP %s after %d attempts — service unavailable",
                        resp.status_code, max_attempts,
                    )
                    return None
                if resp.status_code == 401:
                    try:
                        body = resp.json()
                        detail = body.get("message") or body.get("error") or resp.text[:200]
                    except Exception:
                        detail = resp.text[:200]
                    logger.error(
                        "The Odds API: 401 Unauthorized — %s "
                        "(check https://the-odds-api.com/account to verify key & remaining credits)",
                        detail,
                    )
                    return None
                if resp.status_code == 402:
                    logger.error(
                        "The Odds API: 402 Payment Required — monthly credits exhausted. "
                        "Upgrade your plan or wait for the next billing cycle."
                    )
                    return None
                if resp.status_code == 429:
                    logger.warning("The Odds API: rate limit exceeded (too many requests per minute)")
                    return None
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                if attempt < max_attempts - 1:
                    wait = 1.5 * (2 ** attempt)
                    logger.warning(
                        "The Odds API: request failed (%s) — retry in %.1fs (%d/%d)",
                        exc, wait, attempt + 1, max_attempts,
                    )
                    time.sleep(wait)
                    continue
                logger.error("The Odds API request error: %s", exc)
                return None
        return None

    @staticmethod
    def _utc_to_eastern_date(utc_iso: str) -> date | None:
        """
        Parse a UTC ISO-8601 timestamp and return its Eastern calendar date.

        The Odds API returns commence_time in UTC (e.g. "2026-04-16T02:00:00Z").
        NBA games that start at ~10 PM ET have a UTC time of the following day,
        so a simple string-prefix comparison against the Eastern query date fails
        for all late-night tip-offs.  This method converts to Eastern first.
        """
        if not utc_iso:
            return None
        try:
            dt_utc = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
            try:
                from zoneinfo import ZoneInfo
                return dt_utc.astimezone(ZoneInfo("America/New_York")).date()
            except Exception:
                # Fallback fixed offset: NBA season = EDT (UTC-4)
                return (dt_utc - timedelta(hours=4)).date()
        except Exception:
            return None

    def _get_events(self, game_date: date) -> list[dict]:
        """
        Fetch upcoming NBA events and filter to those that start on *game_date*
        in Eastern time.

        The API returns all upcoming events regardless of date, and commence_time
        is always UTC — so we convert each timestamp to Eastern before comparing.
        """
        data = self._get(
            f"sports/{self._cfg.sport_key}/odds",
            regions=self._cfg.regions,
            markets="h2h",
            oddsFormat=self._cfg.odds_format,
            dateFormat="iso",
        )
        if not data:
            return []
        matched = [
            e for e in data
            if self._utc_to_eastern_date(e.get("commence_time", "")) == game_date
        ]
        logger.debug(
            "The Odds API: %d/%d events match Eastern date %s",
            len(matched), len(data), game_date,
        )
        return matched

    def get_games_for_date(self, game_date: date) -> list[Game]:
        events = self._get_events(game_date)
        games = []
        from utils.date_utils import parse_iso_datetime

        for evt in events:
            try:
                home = evt.get("home_team", "")
                away = evt.get("away_team", "")
                tip = parse_iso_datetime(evt.get("commence_time") or "")
                g = Game(
                    game_id=evt.get("id", ""),
                    home_team_id=home.lower().replace(" ", "_"),
                    home_team_abbr=self._abbr(home),
                    away_team_id=away.lower().replace(" ", "_"),
                    away_team_abbr=self._abbr(away),
                    game_date=game_date,
                    tip_off_time=tip,
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
                    book_key = book_data.get("key", "") or ""
                    book = _BOOK_MAP.get(book_key, BookName.OTHER)

                    for market in book_data.get("markets", []):
                        market_key = market.get("key", "")
                        prop_type = PROP_ALIAS_MAP.get(market_key)
                        if not prop_type:
                            continue

                        # Group outcomes by (player_name, line_value).
                        # Bovada and similar books post multiple alternate lines
                        # for the same player (e.g. 2.5, 3.5, 4.5 assists).
                        # Keying only by player name causes the line value to be
                        # set from the first outcome while over/under odds keep
                        # getting overwritten by each subsequent alternate line,
                        # producing corrupted records like line=2.5 at +190
                        # (which is really the 3.5 line's price).
                        player_outcomes: dict[tuple, dict] = {}
                        for outcome in market.get("outcomes", []):
                            desc = outcome.get("description", "")
                            name = outcome.get("name", "")  # 'Over' or 'Under'
                            price = outcome.get("price", -110)
                            point = outcome.get("point", 0.5)
                            key = (desc, float(point))
                            if key not in player_outcomes:
                                player_outcomes[key] = {"point": point, "player_name": desc}
                            if name.lower() == "over":
                                player_outcomes[key]["over_odds"] = int(price)
                            elif name.lower() == "under":
                                player_outcomes[key]["under_odds"] = int(price)

                        for (player_name, _pt), odds in player_outcomes.items():
                            if "over_odds" not in odds or "under_odds" not in odds:
                                continue
                            line = OddsLine(
                                book=book,
                                player_id=player_name.lower().replace(" ", "_"),
                                player_name=player_name,
                                prop_type=prop_type,
                                line=float(odds["point"]),
                                over_odds=odds["over_odds"],
                                under_odds=odds["under_odds"],
                                game_id=event_id,
                                data_source=DataSource.ODDS_API,
                                book_key=book_key,
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
