"""BALLDONTLIE adapter for the initial pregame points/rebounds/assists scope."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

from config import get_bdl_config
from domain.entities import Game, OddsLine, Player
from domain.enums import BookName, DataSource, InjuryStatus, PlayerRole, Position, PropType
from providers.balldontlie_client import BallDontLieClient, BallDontLieRequestError
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)

_SUPPORTED_MARKETS = {
    "points": PropType.POINTS,
    "player_points": PropType.POINTS,
    "rebounds": PropType.REBOUNDS,
    "player_rebounds": PropType.REBOUNDS,
    "assists": PropType.ASSISTS,
    "player_assists": PropType.ASSISTS,
}
_BOOKS = {
    "fanduel": BookName.FANDUEL,
    "draftkings": BookName.DRAFTKINGS,
    "betmgm": BookName.BETMGM,
    "caesars": BookName.CAESARS,
    "betrivers": BookName.BETRIVERS,
    "bet365": BookName.BET365,
    "pinnacle": BookName.PINNACLE,
}
_POSITION = {
    "PG": Position.PG, "SG": Position.SG, "SF": Position.SF, "PF": Position.PF,
    "C": Position.C, "G": Position.G, "F": Position.F, "G-F": Position.GF,
    "F-G": Position.GF, "F-C": Position.FC,
}
_STATUS = {
    "out": InjuryStatus.OUT,
    "doubtful": InjuryStatus.DOUBTFUL,
    "questionable": InjuryStatus.QUESTIONABLE,
    "probable": InjuryStatus.UNREPORTED,
    "day-to-day": InjuryStatus.DAY_TO_DAY,
    "active": InjuryStatus.UNREPORTED,
}


def _as_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _minutes(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        text = str(value)
        if ":" in text:
            whole, seconds = text.split(":", 1)
            return float(whole) + float(seconds) / 60
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def _stat_datetime(row: dict[str, Any]) -> Optional[datetime]:
    game = row.get("game") or {}
    return _as_datetime(game.get("date") or row.get("game_date") or row.get("date"))


class BallDontLieProvider(BaseProvider):
    """Single live source; maps provider payloads without inventing context."""

    source_name = DataSource.BALLDONTLIE

    def __init__(self, api_key: str) -> None:
        self._client = BallDontLieClient(api_key, get_bdl_config())
        self._games: dict[str, Game] = {}
        self._injuries: dict[str, dict[str, Any]] = {}
        self._injury_feed_available = False

    def is_available(self) -> bool:
        return True

    def get_games_for_date(self, game_date: date) -> list[Game]:
        rows, _ = self._client.get_all(
            "/nba/v1/games",
            params={"dates[]": [game_date.isoformat()], "per_page": 100},
            cache_ttl=60,
        )
        games: list[Game] = []
        for row in rows:
            home = row.get("home_team") or {}
            away = row.get("visitor_team") or row.get("away_team") or {}
            game_time = _as_datetime(row.get("datetime") or row.get("date"))
            status = str(row.get("status") or "unknown")
            if not home.get("id") or not away.get("id") or not row.get("id"):
                continue
            games.append(Game(
                game_id=str(row["id"]),
                home_team_id=str(home["id"]),
                home_team_abbr=str(home.get("abbreviation") or ""),
                away_team_id=str(away["id"]),
                away_team_abbr=str(away.get("abbreviation") or ""),
                game_date=game_date,
                tip_off_time=game_time,
                status=status,
                data_source=DataSource.BALLDONTLIE,
            ))
        self._games = {game.game_id: game for game in games}
        return games

    def _active_players(self) -> dict[str, dict[str, Any]]:
        rows, _ = self._client.get_all(
            "/nba/v1/players/active",
            params={"per_page": 100},
            cache_ttl=get_bdl_config().stable_cache_seconds,
        )
        return {str(row["id"]): row for row in rows if row.get("id") is not None}

    def _load_injuries(self) -> None:
        rows, fetched_at = self._client.get_all(
            "/nba/v1/player_injuries",
            params={"per_page": 100},
            cache_ttl=60,
        )
        self._injuries = {
            str((row.get("player") or {}).get("id") or row.get("player_id")): {
                **row, "_fetched_at": fetched_at,
            }
            for row in rows
            if (row.get("player") or {}).get("id") or row.get("player_id")
        }
        self._injury_feed_available = True

    def get_players_for_game(
        self,
        game_id: str,
        candidate_player_ids: Optional[set[str]] = None,
    ) -> list[Player]:
        """Resolve roster membership pregame; never consult target-game box scores."""
        game = self._games.get(str(game_id))
        if game is None:
            raise BallDontLieRequestError(
                "unknown_game",
                "Game must be loaded from the BDL schedule before participants are resolved.",
            )
        active = self._active_players()
        self._load_injuries()
        team_ids = {game.home_team_id, game.away_team_id}
        selected = [
            row for player_id, row in active.items()
            if str((row.get("team") or {}).get("id")) in team_ids
            and (candidate_player_ids is None or player_id in candidate_player_ids)
        ]
        cutoff = (game.tip_off_time or datetime.combine(game.game_date, time.min, timezone.utc))
        return [self._build_player(row, cutoff) for row in selected]

    def _build_player(self, raw: dict[str, Any], cutoff: datetime) -> Player:
        player_id = str(raw["id"])
        team = raw.get("team") or {}
        stats = self.get_player_recent_form(player_id, 20, before=cutoff)
        stats = [
            row for row in stats
            if (stat_time := _stat_datetime(row)) is not None and stat_time < cutoff
        ]
        if not stats:
            return Player(
                player_id=player_id,
                name=f"{raw.get('first_name', '')} {raw.get('last_name', '')}".strip(),
                team_id=str(team.get("id") or ""),
                team_abbr=str(team.get("abbreviation") or ""),
                position=_POSITION.get(str(raw.get("position") or "").upper(), Position.G),
                role=PlayerRole.UNKNOWN,
                injury_status=InjuryStatus.UNKNOWN,
                availability_quality="history_missing",
                data_source=DataSource.BALLDONTLIE,
            )

        injury = self._injuries.get(player_id)
        injury_status = InjuryStatus.UNREPORTED if self._injury_feed_available else InjuryStatus.UNKNOWN
        availability_quality = "injury_feed_observed" if self._injury_feed_available else "injury_feed_unavailable"
        observed_at = None
        if injury:
            injury_status = _STATUS.get(str(_value(injury, "status", "injury_status") or "").lower(), InjuryStatus.UNKNOWN)
            observed_at = injury.get("_fetched_at")
        minutes = [_minutes(row.get("min") or row.get("minutes")) for row in stats]

        def average(key: str, *aliases: str) -> float:
            values = [float(_value(row, key, *aliases) or 0) for row in stats]
            return sum(values) / len(values)

        return Player(
            player_id=player_id,
            name=f"{raw.get('first_name', '')} {raw.get('last_name', '')}".strip(),
            team_id=str(team.get("id") or ""),
            team_abbr=str(team.get("abbreviation") or ""),
            position=_POSITION.get(str(raw.get("position") or "").upper(), Position.G),
            role=PlayerRole.UNKNOWN,
            injury_status=injury_status,
            minutes_per_game=sum(minutes) / len(minutes),
            points_per_game=average("pts", "points"),
            rebounds_per_game=average("reb", "rebounds"),
            assists_per_game=average("ast", "assists"),
            field_goal_attempts=average("fga"),
            free_throw_attempts=average("fta"),
            three_point_attempts=average("fg3a"),
            threes_per_game=average("fg3m"),
            blocks_per_game=average("blk"),
            steals_per_game=average("stl"),
            turnovers_per_game=average("turnover", "tov"),
            is_starter=None,
            availability_observed_at=observed_at,
            availability_quality=availability_quality,
            last5_points=[float(_value(row, "pts", "points") or 0) for row in stats[:5]],
            last5_rebounds=[float(_value(row, "reb", "rebounds") or 0) for row in stats[:5]],
            last5_assists=[float(_value(row, "ast", "assists") or 0) for row in stats[:5]],
            last5_minutes=minutes[:5],
            last10_points=[float(_value(row, "pts", "points") or 0) for row in stats[:10]],
            last10_rebounds=[float(_value(row, "reb", "rebounds") or 0) for row in stats[:10]],
            last10_assists=[float(_value(row, "ast", "assists") or 0) for row in stats[:10]],
            last10_minutes=minutes[:10],
            data_source=DataSource.BALLDONTLIE,
        )

    def get_player_recent_form(
        self,
        player_id: str,
        n: int = 20,
        *,
        before: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"player_ids[]": [player_id], "per_page": min(100, n * 3)}
        if before:
            params["end_date"] = (before.date() - timedelta(days=1)).isoformat()
        rows, _ = self._client.get_all("/nba/v1/stats", params=params, cache_ttl=300)
        return sorted(
            rows,
            key=lambda row: _stat_datetime(row) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:n]

    def get_injuries(self, game_date: Optional[date] = None) -> list[dict[str, Any]]:
        self._load_injuries()
        return [
            {
                "player_id": player_id,
                "player_name": f"{(row.get('player') or {}).get('first_name', '')} {(row.get('player') or {}).get('last_name', '')}".strip(),
                "team_id": str((row.get("team") or {}).get("id") or ""),
                "status": str(_value(row, "status", "injury_status") or "unknown"),
                "source_updated_at": _as_datetime(_value(row, "updated_at", "date")),
                "fetched_at": row.get("_fetched_at"),
            }
            for player_id, row in self._injuries.items()
        ]

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        if not self._games:
            self.get_games_for_date(game_date)
        quotes: list[OddsLine] = []
        for game in self._games.values():
            rows, fetched_at = self._client.get_all(
                "/nba/v2/odds/player_props",
                params={"game_id": game.game_id},
                cache_ttl=get_bdl_config().odds_refresh_seconds,
            )
            for row in rows:
                quotes.extend(self._map_prop_row(row, game, fetched_at))
        return quotes

    def _map_prop_row(self, raw: dict[str, Any], game: Game, fetched_at: datetime) -> list[OddsLine]:
        """Normalize documented nested market/vendor outcomes without inference."""
        market = raw.get("market") or {}
        market_key = str(_value(raw, "prop_type", "type", "stat_type") or market.get("type") or "").lower()
        prop_type = _SUPPORTED_MARKETS.get(market_key)
        if prop_type is None:
            return []
        if str(market.get("type") or "over_under").lower() not in {"over_under", "ou", ""}:
            return []
        player = raw.get("player") or {}
        player_id = str(_value(raw, "player_id") or player.get("id") or "")
        if not player_id:
            return []
        player_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
        source_updated_at = _as_datetime(_value(raw, "updated_at", "last_updated"))
        vendors = raw.get("odds") or raw.get("vendors") or raw.get("bookmakers") or []
        if isinstance(vendors, dict):
            vendors = [vendors]
        result: list[OddsLine] = []
        for vendor_quote in vendors:
            if not isinstance(vendor_quote, dict):
                continue
            vendor = str(_value(vendor_quote, "vendor", "book", "name") or "").strip().lower()
            if not vendor:
                continue
            over = vendor_quote.get("over") or {}
            under = vendor_quote.get("under") or {}
            if not isinstance(over, dict):
                over = {"price": vendor_quote.get("over_price"), "line": vendor_quote.get("line_value")}
            if not isinstance(under, dict):
                under = {"price": vendor_quote.get("under_price"), "line": vendor_quote.get("line_value")}
            line = _value(over, "line", "line_value", "value") or _value(under, "line", "line_value", "value") or _value(raw, "line_value", "line")
            over_price = _value(over, "price", "odds") or vendor_quote.get("over_odds")
            under_price = _value(under, "price", "odds") or vendor_quote.get("under_odds")
            try:
                line_value = float(line)
                over_odds = int(float(over_price))
                under_odds = int(float(under_price))
            except (TypeError, ValueError):
                continue
            if abs(over_odds) < 100 or abs(under_odds) < 100:
                continue
            result.append(OddsLine(
                book=_BOOKS.get(vendor, BookName.OTHER),
                book_key=vendor,
                player_id=player_id,
                player_name=player_name,
                prop_type=prop_type,
                line=line_value,
                over_odds=over_odds,
                under_odds=under_odds,
                game_id=game.game_id,
                event_home_team_abbr=game.home_team_abbr,
                event_away_team_abbr=game.away_team_abbr,
                timestamp=source_updated_at,
                source_updated_at=source_updated_at,
                fetched_at=fetched_at,
                provider_quote_id=str(_value(vendor_quote, "id") or raw.get("id") or ""),
                market_key=market_key,
                data_source=DataSource.BALLDONTLIE,
            ))
        return result

    def get_final_player_totals(self, game_id: str) -> dict[str, dict[str, float]]:
        """Normalize final P/R/A box-score records for append-only settlement."""
        rows, _ = self._client.get_all(
            "/nba/v1/box_scores",
            params={"game_ids[]": [game_id], "per_page": 100},
            cache_ttl=300,
        )
        totals: dict[str, dict[str, float]] = {}
        for row in rows:
            player = row.get("player") or {}
            player_id = str(player.get("id") or row.get("player_id") or "")
            if not player_id:
                continue
            totals[player_id] = {
                "points": float(_value(row, "pts", "points") or 0),
                "rebounds": float(_value(row, "reb", "rebounds") or 0),
                "assists": float(_value(row, "ast", "assists") or 0),
            }
        return totals
