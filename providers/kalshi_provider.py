"""
Kalshi prediction-market provider (public market data, no API key).

Pulls every open NBA player-prop and game market from Kalshi's public REST
API and keeps the ones whose game falls on the requested date.

Endpoint:
  GET {base}/events?series_ticker=<series>&status=open&with_nested_markets=true

Each prop market is a Yes/No contract such as "Franz Wagner: 15+ points"
(``strike_type='greater'``, ``floor_strike=14.5``), so Yes = Over 14.5 and
No = Under 14.5.  Yes/No asks are converted to fee-inclusive American odds.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Optional

import requests

from config import get_prediction_market_config
from domain.entities import Game, GameLine, OddsLine, Player
from domain.enums import BookName, DataSource, PropType
from odds.prediction_market import binary_odds_pair, to_float
from providers.base_provider import BaseProvider
from utils.names import player_key

logger = logging.getLogger(__name__)

PROP_SERIES: dict[str, PropType] = {
    "KXNBAPTS": PropType.POINTS,
    "KXNBAREB": PropType.REBOUNDS,
    "KXNBAAST": PropType.ASSISTS,
    "KXNBA3PT": PropType.THREES,
    "KXNBAPRA": PropType.PRA,
    "KXNBABLK": PropType.BLOCKS,
    "KXNBASTL": PropType.STEALS,
    "KXNBAPR": PropType.POINTS_REBOUNDS,
    "KXNBAPA": PropType.POINTS_ASSISTS,
    "KXNBARA": PropType.REBOUNDS_ASSISTS,
    "KXNBA2D": PropType.DOUBLE_DOUBLE,
    "KXNBA3D": PropType.TRIPLE_DOUBLE,
}

GAME_SERIES: dict[str, str] = {
    "KXNBAGAME": "moneyline",
    "KXNBASPREAD": "spread",
    "KXNBATOTAL": "total",
}

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1
)}

# Event ticker game segment, e.g. "26NOV01BOSORL" (an optional HHMM may follow the day).
_EVENT_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})(?:\d{4})?([A-Z]{3})([A-Z]{3})$")

_MAX_PAGES = 20


def parse_event_ticker(event_ticker: str) -> Optional[tuple[date, str, str]]:
    """
    Parse 'KXNBAPTS-26NOV01BOSORL' -> (date(2026, 11, 1), 'BOS', 'ORL').

    The ticker date is the game's US Eastern calendar date; the first team is
    the away team and the second is the home team.
    """
    parts = (event_ticker or "").split("-")
    if len(parts) < 2:
        return None
    m = _EVENT_RE.match(parts[1])
    if not m:
        return None
    yy, mon, dd, away, home = m.groups()
    month = _MONTHS.get(mon)
    if not month:
        return None
    try:
        return date(2000 + int(yy), month, int(dd)), away, home
    except ValueError:
        return None


def _market_side_prices(market: dict) -> tuple[Optional[float], Optional[float]]:
    yes_ask = to_float(market.get("yes_ask_dollars"))
    no_ask = to_float(market.get("no_ask_dollars"))
    if yes_ask is None and market.get("yes_ask") is not None:
        yes_ask = to_float(market.get("yes_ask"))
        yes_ask = yes_ask / 100.0 if yes_ask is not None else None
    if no_ask is None and market.get("no_ask") is not None:
        no_ask = to_float(market.get("no_ask"))
        no_ask = no_ask / 100.0 if no_ask is not None else None
    return yes_ask, no_ask


def _is_open(market: dict) -> bool:
    return str(market.get("status", "")).lower() in {"active", "open", ""}


class KalshiProvider(BaseProvider):
    """Kalshi NBA player props and game lines from the public API."""

    source_name = DataSource.KALSHI

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self._cfg = get_prediction_market_config()
        self._session = session or requests.Session()
        self._session.headers.setdefault("Accept", "application/json")
        self.last_errors: list[str] = []

    def is_available(self) -> bool:
        return self._cfg.kalshi_enabled

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _fetch_series(self, series_ticker: str) -> list[dict]:
        """Return every open event (with nested markets) for one series."""
        url = f"{self._cfg.kalshi_base_url.rstrip('/')}/events"
        events: list[dict] = []
        cursor: Optional[str] = None
        for _ in range(_MAX_PAGES):
            params: dict[str, object] = {
                "series_ticker": series_ticker,
                "status": "open",
                "with_nested_markets": "true",
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            try:
                resp = self._session.get(url, params=params, timeout=self._cfg.timeout)
                resp.raise_for_status()
                payload = resp.json()
            except (requests.RequestException, ValueError) as exc:
                msg = f"Kalshi {series_ticker}: {exc}"
                logger.warning(msg)
                self.last_errors.append(msg)
                break
            events.extend(payload.get("events") or [])
            cursor = payload.get("cursor") or None
            if not cursor:
                break
        return events

    def _fetch_many(self, series: list[str]) -> dict[str, list[dict]]:
        with ThreadPoolExecutor(max_workers=min(6, max(1, len(series)))) as pool:
            results = list(pool.map(self._fetch_series, series))
        return dict(zip(series, results))

    # ------------------------------------------------------------------
    # BaseProvider interface
    # ------------------------------------------------------------------

    def get_games_for_date(self, game_date: date) -> list[Game]:
        # Kalshi is an odds source only; the slate comes from BALLDONTLIE.
        return []

    def get_players_for_game(self, game_id: str) -> list[Player]:
        raise NotImplementedError

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        if not self.is_available():
            return []
        self.last_errors = []
        fetched_at = datetime.now(timezone.utc)
        by_series = self._fetch_many(list(PROP_SERIES))
        lines: list[OddsLine] = []
        skipped = 0
        for series, events in by_series.items():
            prop_type = PROP_SERIES[series]
            for event in events:
                parsed = parse_event_ticker(event.get("event_ticker", ""))
                if not parsed or parsed[0] != game_date:
                    continue
                _, away, home = parsed
                for market in event.get("markets") or []:
                    line = self._parse_prop_market(
                        market, prop_type, event.get("event_ticker", ""), away, home, fetched_at
                    )
                    if line is None:
                        skipped += 1
                    else:
                        lines.append(line)
        logger.info(
            "Kalshi: loaded %d prop lines for %s (%d untradeable/unparsed skipped)",
            len(lines), game_date, skipped,
        )
        return lines

    def get_game_lines(self, game_date: date) -> list[GameLine]:
        if not self.is_available():
            return []
        by_series = self._fetch_many(list(GAME_SERIES))
        lines: list[GameLine] = []
        for series, events in by_series.items():
            market_type = GAME_SERIES[series]
            for event in events:
                event_ticker = event.get("event_ticker", "")
                parsed = parse_event_ticker(event_ticker)
                if not parsed or parsed[0] != game_date:
                    continue
                _, away, home = parsed
                for market in event.get("markets") or []:
                    lines.extend(
                        self._parse_game_market(market, market_type, event_ticker, game_date, away, home)
                    )
        logger.info("Kalshi: loaded %d game-line sides for %s", len(lines), game_date)
        return lines

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _priced(self, yes_ask: Optional[float], no_ask: Optional[float]):
        cfg = self._cfg
        return binary_odds_pair(
            yes_ask, no_ask, cfg.kalshi_fee_rate, cfg.max_spread, cfg.min_price, cfg.max_price
        )

    def _parse_prop_market(
        self,
        market: dict,
        prop_type: PropType,
        event_ticker: str,
        away: str,
        home: str,
        fetched_at: datetime,
    ) -> Optional[OddsLine]:
        if not _is_open(market):
            return None
        if str(market.get("strike_type", "greater")).lower() != "greater":
            return None
        strike = to_float(market.get("floor_strike"))
        title = market.get("title") or ""
        name = title.split(":", 1)[0].strip() if ":" in title else ""
        if not name:
            name = (market.get("yes_sub_title") or "").split(":", 1)[0].strip()
        if strike is None or not name:
            return None
        yes_ask, no_ask = _market_side_prices(market)
        priced = self._priced(yes_ask, no_ask)
        if priced is None:
            return None
        over_odds, under_odds, _, _ = priced

        team_abbr, opponent_abbr = "", ""
        suffix = (market.get("ticker") or "").split("-")
        if len(suffix) >= 3:
            code = suffix[2][:3]
            if code == away:
                team_abbr, opponent_abbr = away, home
            elif code == home:
                team_abbr, opponent_abbr = home, away

        return OddsLine(
            book=BookName.KALSHI,
            player_id=player_key(name),
            player_name=name,
            prop_type=prop_type,
            line=float(strike),
            over_odds=over_odds,
            under_odds=under_odds,
            game_id=event_ticker,
            team_abbr=team_abbr,
            opponent_abbr=opponent_abbr,
            timestamp=fetched_at,
            data_source=DataSource.KALSHI,
            book_key="kalshi",
        )

    def _parse_game_market(
        self,
        market: dict,
        market_type: str,
        event_ticker: str,
        game_date: date,
        away: str,
        home: str,
    ) -> list[GameLine]:
        if not _is_open(market):
            return []
        yes_ask, no_ask = _market_side_prices(market)
        priced = self._priced(yes_ask, no_ask)
        if priced is None:
            return []
        yes_am, no_am, yes_cost, no_cost = priced
        suffix = (market.get("ticker") or "").rsplit("-", 1)[-1]

        def gl(selection: str, line: Optional[float], cost: float, american: int) -> GameLine:
            return GameLine(
                book=BookName.KALSHI,
                game_date=game_date,
                away_abbr=away,
                home_abbr=home,
                market=market_type,
                selection=selection,
                line=line,
                price=round(cost, 4),
                american_odds=american,
                event_id=event_ticker,
                data_source=DataSource.KALSHI,
            )

        if market_type == "moneyline":
            team = suffix[:3]
            if team not in (away, home):
                return []
            return [gl(team, None, yes_cost, yes_am)]

        strike = to_float(market.get("floor_strike"))
        if strike is None:
            return []

        if market_type == "spread":
            team = suffix[:3]
            if team not in (away, home):
                return []
            other = home if team == away else away
            # Yes: team wins by more than *strike* (team -strike); No: other team +strike.
            return [gl(team, -strike, yes_cost, yes_am), gl(other, strike, no_cost, no_am)]

        if market_type == "total":
            return [gl("over", strike, yes_cost, yes_am), gl("under", strike, no_cost, no_am)]
        return []
