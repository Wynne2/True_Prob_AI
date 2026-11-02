"""
Polymarket prediction-market provider (public Gamma API, no API key).

Pulls every open NBA game event for the requested date and extracts:
  - player props (points, rebounds, assists, threes, blocks, steals)
  - game lines (moneyline, full-game spread, full-game total)

Endpoint:
  GET {base}/events?series_id=<nba>&closed=false&end_date_min=..&end_date_max=..

Prices come from each market's top-of-book ``bestBid``/``bestAsk`` (the Yes
token).  Buying No costs ``1 - bestBid``.  Asks are converted to
fee-inclusive American odds using the market's taker fee schedule.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

from config import get_prediction_market_config
from domain.entities import Game, GameLine, OddsLine, Player
from domain.enums import BookName, DataSource, PropType
from odds.prediction_market import binary_odds_pair, to_float
from providers.base_provider import BaseProvider
from utils.names import player_key

logger = logging.getLogger(__name__)

PROP_MARKET_TYPES: dict[str, PropType] = {
    "points": PropType.POINTS,
    "rebounds": PropType.REBOUNDS,
    "assists": PropType.ASSISTS,
    "threes": PropType.THREES,
    "blocks": PropType.BLOCKS,
    "steals": PropType.STEALS,
}

GAME_MARKET_TYPES: dict[str, str] = {
    "moneyline": "moneyline",
    "spreads": "spread",
    "totals": "total",
}

# Main game events only, e.g. "nba-tor-lal-2026-11-01" (not "...-exact-margin").
_SLUG_RE = re.compile(r"^nba-([a-z]{2,4})-([a-z]{2,4})-(\d{4}-\d{2}-\d{2})$")

_PAGE_SIZE = 100
_MAX_PAGES = 20
_CACHE_TTL_SECONDS = 60


def parse_event_slug(slug: str) -> Optional[tuple[date, str, str]]:
    """'nba-tor-lal-2026-11-01' -> (date(2026, 11, 1), 'TOR', 'LAL'); away team first."""
    m = _SLUG_RE.match(slug or "")
    if not m:
        return None
    away, home, day = m.groups()
    try:
        return date.fromisoformat(day), away.upper(), home.upper()
    except ValueError:
        return None


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            return []
    return []


def _fee_rate(market: dict) -> float:
    if not market.get("feesEnabled"):
        return 0.0
    schedule = market.get("feeSchedule") or {}
    rate = to_float(schedule.get("rate")) if isinstance(schedule, dict) else None
    return rate or 0.0


def _is_tradeable(market: dict) -> bool:
    if market.get("closed") is True:
        return False
    if market.get("active") is False:
        return False
    if market.get("acceptingOrders") is False:
        return False
    return True


def _side_asks(market: dict) -> tuple[Optional[float], Optional[float]]:
    """(first-outcome ask, second-outcome ask) from the Yes-token top of book."""
    best_ask = to_float(market.get("bestAsk"))
    best_bid = to_float(market.get("bestBid"))
    second = (1.0 - best_bid) if best_bid is not None else None
    return best_ask, second


class PolymarketProvider(BaseProvider):
    """Polymarket NBA player props and game lines from the public Gamma API."""

    source_name = DataSource.POLYMARKET

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self._cfg = get_prediction_market_config()
        self._session = session or requests.Session()
        self._session.headers.setdefault("Accept", "application/json")
        self._cache: dict[date, tuple[float, list[dict]]] = {}
        self.last_errors: list[str] = []

    def is_available(self) -> bool:
        return self._cfg.polymarket_enabled

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _events_for_date(self, game_date: date) -> list[dict]:
        """Open NBA game events whose slug date is *game_date* (cached ~60s per run)."""
        cached = self._cache.get(game_date)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

        self.last_errors = []
        url = f"{self._cfg.polymarket_base_url.rstrip('/')}/events"
        # endDate is the scheduled tip-off (UTC); ET evening games tip after midnight UTC.
        window_start = datetime(game_date.year, game_date.month, game_date.day, tzinfo=timezone.utc)
        window_end = window_start + timedelta(days=1, hours=12)
        events: list[dict] = []
        for page in range(_MAX_PAGES):
            params = {
                "series_id": self._cfg.polymarket_nba_series_id,
                "closed": "false",
                "end_date_min": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_date_max": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": _PAGE_SIZE,
                "offset": page * _PAGE_SIZE,
            }
            try:
                resp = self._session.get(url, params=params, timeout=self._cfg.timeout)
                resp.raise_for_status()
                batch = resp.json()
            except (requests.RequestException, ValueError) as exc:
                msg = f"Polymarket events: {exc}"
                logger.warning(msg)
                self.last_errors.append(msg)
                break
            if not isinstance(batch, list):
                break
            events.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break

        matched = []
        for event in events:
            parsed = parse_event_slug(event.get("slug", ""))
            if parsed and parsed[0] == game_date:
                matched.append(event)
        logger.info(
            "Polymarket: %d NBA game events on %s (%d events in window)",
            len(matched), game_date, len(events),
        )
        self._cache[game_date] = (time.monotonic(), matched)
        return matched

    # ------------------------------------------------------------------
    # BaseProvider interface
    # ------------------------------------------------------------------

    def get_games_for_date(self, game_date: date) -> list[Game]:
        # Polymarket is an odds source only; the slate comes from BALLDONTLIE.
        return []

    def get_players_for_game(self, game_id: str) -> list[Player]:
        raise NotImplementedError

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        if not self.is_available():
            return []
        fetched_at = datetime.now(timezone.utc)
        lines: list[OddsLine] = []
        skipped = 0
        for event in self._events_for_date(game_date):
            event_id = str(event.get("id") or event.get("slug") or "")
            for market in event.get("markets") or []:
                prop_type = PROP_MARKET_TYPES.get(market.get("sportsMarketType") or "")
                if prop_type is None:
                    continue
                line = self._parse_prop_market(market, prop_type, event_id, fetched_at)
                if line is None:
                    skipped += 1
                else:
                    lines.append(line)
        logger.info(
            "Polymarket: loaded %d prop lines for %s (%d untradeable/unparsed skipped)",
            len(lines), game_date, skipped,
        )
        return lines

    def get_game_lines(self, game_date: date) -> list[GameLine]:
        if not self.is_available():
            return []
        lines: list[GameLine] = []
        for event in self._events_for_date(game_date):
            parsed = parse_event_slug(event.get("slug", ""))
            if not parsed:
                continue
            _, away, home = parsed
            nicknames = self._nickname_map(event.get("title") or "", away, home)
            event_id = str(event.get("id") or event.get("slug") or "")
            for market in event.get("markets") or []:
                market_type = GAME_MARKET_TYPES.get(market.get("sportsMarketType") or "")
                if market_type is None:
                    continue
                lines.extend(
                    self._parse_game_market(market, market_type, event_id, game_date, away, home, nicknames)
                )
        logger.info("Polymarket: loaded %d game-line sides for %s", len(lines), game_date)
        return lines

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _priced(self, market: dict):
        first_ask, second_ask = _side_asks(market)
        cfg = self._cfg
        return binary_odds_pair(
            first_ask, second_ask, _fee_rate(market), cfg.max_spread, cfg.min_price, cfg.max_price
        )

    def _parse_prop_market(
        self, market: dict, prop_type: PropType, event_id: str, fetched_at: datetime
    ) -> Optional[OddsLine]:
        if not _is_tradeable(market):
            return None
        question = market.get("question") or market.get("groupItemTitle") or ""
        if ":" not in question:
            return None
        name = question.split(":", 1)[0].strip()
        line = to_float(market.get("line"))
        if line is None:
            m = re.search(r"O/U\s*([0-9]+(?:\.[0-9]+)?)", question)
            line = float(m.group(1)) if m else None
        if not name or line is None:
            return None
        outcomes = [str(o).strip().lower() for o in _json_list(market.get("outcomes"))]
        if outcomes and outcomes[0] not in ("yes", "over"):
            return None
        priced = self._priced(market)
        if priced is None:
            return None
        over_odds, under_odds, _, _ = priced
        return OddsLine(
            book=BookName.POLYMARKET,
            player_id=player_key(name),
            player_name=name,
            prop_type=prop_type,
            line=float(line),
            over_odds=over_odds,
            under_odds=under_odds,
            game_id=event_id,
            timestamp=fetched_at,
            data_source=DataSource.POLYMARKET,
            book_key="polymarket",
        )

    @staticmethod
    def _nickname_map(title: str, away: str, home: str) -> dict[str, str]:
        """'Raptors vs. Lakers' + (TOR, LAL) -> {'raptors': 'TOR', 'lakers': 'LAL'}."""
        parts = re.split(r"\s+vs\.?\s+", title, maxsplit=1)
        mapping = {away.lower(): away, home.lower(): home}
        if len(parts) == 2:
            mapping[parts[0].strip().lower()] = away
            mapping[parts[1].strip().lower()] = home
        return mapping

    def _parse_game_market(
        self,
        market: dict,
        market_type: str,
        event_id: str,
        game_date: date,
        away: str,
        home: str,
        nicknames: dict[str, str],
    ) -> list[GameLine]:
        if not _is_tradeable(market):
            return []
        outcomes = [str(o).strip() for o in _json_list(market.get("outcomes"))]
        if len(outcomes) != 2:
            return []
        priced = self._priced(market)
        if priced is None:
            return []
        first_am, second_am, first_cost, second_cost = priced

        def gl(selection: str, line: Optional[float], cost: float, american: int) -> GameLine:
            return GameLine(
                book=BookName.POLYMARKET,
                game_date=game_date,
                away_abbr=away,
                home_abbr=home,
                market=market_type,
                selection=selection,
                line=line,
                price=round(cost, 4),
                american_odds=american,
                event_id=event_id,
                data_source=DataSource.POLYMARKET,
            )

        if market_type == "total":
            line = to_float(market.get("line"))
            if line is None or outcomes[0].lower() != "over":
                return []
            return [gl("over", line, first_cost, first_am), gl("under", line, second_cost, second_am)]

        first_team = nicknames.get(outcomes[0].lower())
        second_team = nicknames.get(outcomes[1].lower())
        if not first_team or not second_team or first_team == second_team:
            return []

        if market_type == "moneyline":
            return [gl(first_team, None, first_cost, first_am), gl(second_team, None, second_cost, second_am)]

        if market_type == "spread":
            line = to_float(market.get("line"))
            if line is None:
                return []
            # 'line' is the first outcome's spread, e.g. Lakers -5.5 / Raptors +5.5.
            return [gl(first_team, line, first_cost, first_am), gl(second_team, -line, second_cost, second_am)]
        return []
