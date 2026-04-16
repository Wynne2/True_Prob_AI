"""
BallDontLie provider — single source of truth for all NBA data.

Implements BaseProvider using the BallDontLie v1/v2 REST API.
All HTTP calls go through _BDLClient which handles Authorization headers,
in-process caching, cursor-based pagination, and 429 back-off.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Optional

import requests

from config import get_bdl_config
from domain.constants import LEAGUE_AVG_DEF_EFF, LEAGUE_AVG_PACE, PROP_ALIAS_MAP
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vendor → BookName mapping for odds endpoints
# ---------------------------------------------------------------------------

_VENDOR_MAP: dict[str, BookName] = {
    "fanduel": BookName.FANDUEL,
    "draftkings": BookName.DRAFTKINGS,
    "betmgm": BookName.BETMGM,
    "caesars": BookName.CAESARS,
    "pointsbet": BookName.POINTSBET,
    "betrivers": BookName.BETRIVERS,
    "bovada": BookName.BOVADA,
    "bet365": BookName.BET365,
    "pinnacle": BookName.PINNACLE,
    "mybookie": BookName.MYBOOKIE,
    "mybookieag": BookName.MYBOOKIE,
    "lowvig": BookName.LOWVIG,
    "betonline": BookName.BETONLINE,
    "betonlineag": BookName.BETONLINE,
}

# BDL stat_type strings → PropType
_STAT_TYPE_MAP: dict[str, PropType] = {
    "points": PropType.POINTS,
    "pts": PropType.POINTS,
    "rebounds": PropType.REBOUNDS,
    "reb": PropType.REBOUNDS,
    "assists": PropType.ASSISTS,
    "ast": PropType.ASSISTS,
    "threes": PropType.THREES,
    "three_pointers_made": PropType.THREES,
    "fg3m": PropType.THREES,
    "blocks": PropType.BLOCKS,
    "blk": PropType.BLOCKS,
    "steals": PropType.STEALS,
    "stl": PropType.STEALS,
    "turnovers": PropType.TURNOVERS,
    "turnover": PropType.TURNOVERS,
    "tov": PropType.TURNOVERS,
    "pts_reb_ast": PropType.PRA,
    "pra": PropType.PRA,
}

# BDL position strings → Position enum
_POSITION_MAP: dict[str, Position] = {
    "PG": Position.PG,
    "SG": Position.SG,
    "SF": Position.SF,
    "PF": Position.PF,
    "C": Position.C,
    "G": Position.G,
    "F": Position.F,
    "FC": Position.FC,
    "GF": Position.GF,
    "G-F": Position.GF,
    "F-C": Position.FC,
    "F-G": Position.GF,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_minutes(min_str: str | None) -> float:
    """Convert 'MM:SS' string to decimal minutes."""
    if not min_str:
        return 0.0
    try:
        if ":" in str(min_str):
            parts = str(min_str).split(":")
            return float(parts[0]) + float(parts[1]) / 60
        return float(min_str)
    except (ValueError, IndexError):
        return 0.0


def _map_position(raw: str | None) -> Position:
    """Map a raw BDL position string to a Position enum value."""
    if not raw:
        return Position.G
    return _POSITION_MAP.get(raw.strip(), Position.G)


def _bdl_season_year(game_date: date) -> int:
    """
    Return the BallDontLie season year (the calendar year the season starts).
    The NBA season starts in October, so an April 2026 game belongs to
    the 2025-26 season → season year 2025.
    """
    return game_date.year if game_date.month >= 10 else game_date.year - 1


def _safe_float(val) -> float:
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _safe_int(val) -> int:
    try:
        return int(val) if val is not None else 0
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class _BDLClient:
    """
    Thin HTTP client for the BallDontLie API.

    Features:
    - Authorization header injection
    - In-process dict cache keyed by (path, sorted-params)
    - HTTP 429 back-off with 1-second retry (up to 3 attempts)
    - Cursor-based pagination via get_all()
    """

    def __init__(self, api_key: str, base_url: str, timeout: int) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._single_cache: dict[tuple, dict | None] = {}
        self._list_cache: dict[tuple, list] = {}

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._key}

    def get(self, path: str, params: dict | None = None) -> dict | None:
        """Single GET; returns parsed JSON body or None on error."""
        params = params or {}
        cache_key = (path, tuple(sorted(
            (k, v) for k, v in params.items() if not isinstance(v, list)
        )))
        if cache_key in self._single_cache:
            return self._single_cache[cache_key]

        url = f"{self._base}/{path}"
        for attempt in range(3):
            try:
                resp = requests.get(
                    url,
                    headers=self._headers(),
                    params=params,
                    timeout=self._timeout,
                )
                if resp.status_code == 429:
                    logger.warning(
                        "BallDontLie: rate-limited, waiting 1 s (attempt %d)", attempt + 1
                    )
                    time.sleep(1)
                    continue
                if not resp.ok:
                    logger.warning(
                        "BallDontLie: GET %s → HTTP %d", url, resp.status_code
                    )
                    self._single_cache[cache_key] = None
                    return None
                result = resp.json()
                self._single_cache[cache_key] = result
                return result
            except requests.RequestException as exc:
                logger.error("BallDontLie request error %s: %s", url, exc)
                return None
        return None

    def get_all(self, path: str, params: dict | None = None) -> list:
        """
        Paginate through all pages using BallDontLie cursor-based pagination.
        Returns the combined list of all 'data' items across pages.
        """
        params = params or {}
        # Build a hashable cache key from scalar params only
        scalar_items = tuple(sorted(
            (k, v) for k, v in params.items()
            if not isinstance(v, list)
        ))
        list_items = tuple(sorted(
            (k, tuple(v)) for k, v in params.items()
            if isinstance(v, list)
        ))
        cache_key = (path, scalar_items, list_items)
        if cache_key in self._list_cache:
            return self._list_cache[cache_key]

        all_items: list = []
        cursor: int | None = None

        while True:
            page_params = dict(params)
            if cursor is not None:
                page_params["cursor"] = cursor

            result = self.get(path, page_params)
            if not result:
                break

            items = result.get("data", [])
            all_items.extend(items)

            meta = result.get("meta", {})
            next_cursor = meta.get("next_cursor")
            if not next_cursor:
                break
            # Invalidate the single-call cache for the next page so it isn't
            # confused with the previous page's cache entry
            cursor = next_cursor
            # Clear the single cache for this path so cursor-paged calls go through
            keys_to_drop = [k for k in self._single_cache if k[0] == path]
            for k in keys_to_drop:
                del self._single_cache[k]

        self._list_cache[cache_key] = all_items
        return all_items

    def clear_cache(self) -> None:
        self._single_cache.clear()
        self._list_cache.clear()


# ---------------------------------------------------------------------------
# Data mapping helpers
# ---------------------------------------------------------------------------

def _map_game(raw: dict) -> Game:
    """Map a BallDontLie game dict to a Game domain entity."""
    home = raw.get("home_team", {})
    away = raw.get("visitor_team", {})

    game_date_str: str = raw.get("date", "")
    try:
        game_date = date.fromisoformat(game_date_str[:10])
    except (ValueError, TypeError):
        game_date = date.today()

    # Parse UTC datetime → tip-off time
    tip_off: datetime | None = None
    dt_str = raw.get("datetime") or raw.get("status")
    if dt_str and "T" in str(dt_str):
        try:
            tip_off = datetime.fromisoformat(
                str(dt_str).replace("Z", "+00:00")
            )
        except ValueError:
            tip_off = None

    return Game(
        game_id=str(raw.get("id", "")),
        home_team_id=str(home.get("id", "")),
        home_team_abbr=home.get("abbreviation", ""),
        away_team_id=str(away.get("id", "")),
        away_team_abbr=away.get("abbreviation", ""),
        game_date=game_date,
        tip_off_time=tip_off,
        arena=raw.get("arena", "") or "",
        city=home.get("city", "") or "",
        data_source=DataSource.BALLDONTLIE,
    )


def _map_player(
    stat_row: dict,
    avg_row: dict | None,
    recent_stats: list[dict],
) -> Player:
    """
    Build a Player from a BDL box-score stat row, season-average row, and
    a list of recent game stat rows (used for last5 / last10 lists).
    """
    p = stat_row.get("player", {})
    team = stat_row.get("team", {})
    avg = avg_row or {}

    # Recent form lists (sorted newest-first by caller)
    def _extract(rows: list[dict], key: str) -> list[float]:
        return [_safe_float(r.get(key)) for r in rows if r.get(key) is not None]

    last5 = recent_stats[:5]
    last10 = recent_stats[:10]

    # Decide starter/bench from minutes played today
    minutes_today = _parse_minutes(stat_row.get("min"))
    is_starter = minutes_today >= 20.0

    # Season averages take priority; fall back to today's game
    pts_avg = _safe_float(avg.get("pts")) or _safe_float(stat_row.get("pts"))
    reb_avg = _safe_float(avg.get("reb")) or _safe_float(stat_row.get("reb"))
    ast_avg = _safe_float(avg.get("ast")) or _safe_float(stat_row.get("ast"))
    fg3m_avg = _safe_float(avg.get("fg3m")) or _safe_float(stat_row.get("fg3m"))
    blk_avg = _safe_float(avg.get("blk")) or _safe_float(stat_row.get("blk"))
    stl_avg = _safe_float(avg.get("stl")) or _safe_float(stat_row.get("stl"))
    tov_avg = (
        _safe_float(avg.get("turnover"))
        or _safe_float(avg.get("tov"))
        or _safe_float(stat_row.get("turnover"))
    )
    min_avg = _safe_float(avg.get("min")) or minutes_today
    fga_avg = _safe_float(avg.get("fga")) or _safe_float(stat_row.get("fga"))
    fta_avg = _safe_float(avg.get("fta")) or _safe_float(stat_row.get("fta"))
    fg3a_avg = _safe_float(avg.get("fg3a")) or _safe_float(stat_row.get("fg3a"))
    fg3_pct = (
        _safe_float(avg.get("fg3_pct"))
        or (_safe_float(avg.get("fg3m")) / _safe_float(avg.get("fg3a"))
            if _safe_float(avg.get("fg3a")) > 0 else 0.0)
    )

    return Player(
        player_id=str(p.get("id", "")),
        name=f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
        team_id=str(team.get("id", "")),
        team_abbr=team.get("abbreviation", ""),
        position=_map_position(p.get("position")),
        role=PlayerRole.STARTER if is_starter else PlayerRole.BENCH,
        injury_status=InjuryStatus.ACTIVE,
        minutes_per_game=min_avg,
        points_per_game=pts_avg,
        rebounds_per_game=reb_avg,
        assists_per_game=ast_avg,
        threes_per_game=fg3m_avg,
        blocks_per_game=blk_avg,
        steals_per_game=stl_avg,
        turnovers_per_game=tov_avg,
        field_goal_attempts=fga_avg,
        free_throw_attempts=fta_avg,
        three_point_attempts=fg3a_avg,
        three_point_pct=fg3_pct,
        is_starter=is_starter,
        last5_points=_extract(last5, "pts"),
        last5_rebounds=_extract(last5, "reb"),
        last5_assists=_extract(last5, "ast"),
        last5_minutes=[_parse_minutes(r.get("min")) for r in last5],
        last5_threes=_extract(last5, "fg3m"),
        last10_points=_extract(last10, "pts"),
        last10_rebounds=_extract(last10, "reb"),
        last10_assists=_extract(last10, "ast"),
        last10_minutes=[_parse_minutes(r.get("min")) for r in last10],
        data_source=DataSource.BALLDONTLIE,
    )


def _map_odds_line(raw: dict, game_id: str) -> OddsLine | None:
    """
    Map a BallDontLie player-prop row to an OddsLine.
    Returns None if the stat type is not recognised or odds are missing.
    """
    stat_type = str(raw.get("stat_type", "") or raw.get("type", "")).lower()
    prop_type = _STAT_TYPE_MAP.get(stat_type)
    if prop_type is None:
        return None

    line = _safe_float(raw.get("line") or raw.get("value"))
    over_odds = _safe_int(raw.get("over_odds") or raw.get("over"))
    under_odds = _safe_int(raw.get("under_odds") or raw.get("under"))
    if line == 0.0 and over_odds == 0 and under_odds == 0:
        return None

    vendor = str(raw.get("vendor") or raw.get("book") or "").lower()
    book = _VENDOR_MAP.get(vendor, BookName.SAMPLE)

    player = raw.get("player") or {}
    player_id = str(raw.get("player_id") or player.get("id") or "")
    first = player.get("first_name", "") or ""
    last = player.get("last_name", "") or ""
    player_name = f"{first} {last}".strip()

    team = raw.get("team") or {}
    team_abbr = team.get("abbreviation", "") if isinstance(team, dict) else ""

    return OddsLine(
        book=book,
        player_id=player_id,
        player_name=player_name,
        prop_type=prop_type,
        line=line,
        over_odds=over_odds,
        under_odds=under_odds,
        game_id=game_id,
        team_abbr=team_abbr,
        timestamp=datetime.now(tz=timezone.utc),
        data_source=DataSource.BALLDONTLIE,
    )


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class BallDontLieProvider(BaseProvider):
    """
    Data provider backed exclusively by the BallDontLie API.

    Chain of calls per scan run:
      get_games_for_date  → /nba/v1/games?dates[]=
      get_players_for_game → /nba/v1/stats?game_ids[]= + /nba/v1/season_averages
      get_player_props    → /nba/v2/player_props?dates[]=
      get_team_defense    → returns league-average neutral TeamDefense
    """

    source_name: DataSource = DataSource.BALLDONTLIE

    def __init__(self, api_key: str) -> None:
        cfg = get_bdl_config()
        self._api_key = api_key
        self._client = _BDLClient(api_key, cfg.base_url, cfg.timeout)

    def is_available(self) -> bool:
        return bool(self._api_key)

    # ------------------------------------------------------------------
    # Games
    # ------------------------------------------------------------------

    def get_games_for_date(self, game_date: date) -> list[Game]:
        date_str = game_date.isoformat()
        rows = self._client.get_all(
            "nba/v1/games",
            {"dates[]": [date_str], "per_page": 100},
        )
        if not rows:
            logger.info("BallDontLie: no games found for %s", date_str)
            return []

        games = []
        for raw in rows:
            try:
                games.append(_map_game(raw))
            except Exception as exc:
                logger.warning("BallDontLie: failed to map game %s: %s", raw.get("id"), exc)
        logger.info("BallDontLie: %d game(s) for %s", len(games), date_str)
        return games

    # ------------------------------------------------------------------
    # Players
    # ------------------------------------------------------------------

    def get_players_for_game(self, game_id: str) -> list[Player]:
        # 1. Fetch box-score rows for the game
        stat_rows = self._client.get_all(
            "nba/v1/stats",
            {"game_ids[]": [game_id], "per_page": 100},
        )
        if not stat_rows:
            logger.info("BallDontLie: no stats found for game %s", game_id)
            return []

        # 2. Collect all unique player IDs
        player_ids = list({
            str(row["player"]["id"])
            for row in stat_rows
            if row.get("player") and row["player"].get("id")
        })

        # 3. Determine season year from one of the stat rows
        first_game = stat_rows[0].get("game", {})
        game_date_str = first_game.get("date", "")
        try:
            gd = date.fromisoformat(game_date_str[:10])
            season_year = _bdl_season_year(gd)
        except (ValueError, TypeError):
            from datetime import date as _date
            season_year = _bdl_season_year(_date.today())

        # 4. Fetch season averages for all players in one batch call
        avg_map: dict[str, dict] = {}
        if player_ids:
            avg_result = self._client.get(
                "nba/v1/season_averages",
                {
                    "season": season_year,
                    "player_ids[]": player_ids,
                },
            )
            if avg_result:
                for avg_row in avg_result.get("data", []):
                    pid = str(avg_row.get("player_id", ""))
                    if pid:
                        avg_map[pid] = avg_row

        # 5. Fetch recent game stats (last 10) for each player for form lists.
        #    We fetch recent stats per player in one call using player_ids[].
        recent_map: dict[str, list[dict]] = {pid: [] for pid in player_ids}
        if player_ids:
            recent_rows = self._client.get_all(
                "nba/v1/stats",
                {
                    "player_ids[]": player_ids,
                    "seasons[]": [season_year],
                    "per_page": 100,
                },
            )
            # Group by player, sort by game date desc
            from collections import defaultdict
            grouped: dict[str, list[dict]] = defaultdict(list)
            for row in recent_rows:
                p = row.get("player", {})
                pid = str(p.get("id", ""))
                if pid:
                    grouped[pid].append(row)

            for pid, rows in grouped.items():
                rows.sort(
                    key=lambda r: r.get("game", {}).get("date", ""),
                    reverse=True,
                )
                recent_map[pid] = rows[:10]

        # 6. Build Player objects (one per stat_row; deduplicate by player_id)
        seen: set[str] = set()
        players: list[Player] = []
        for row in stat_rows:
            p = row.get("player", {})
            pid = str(p.get("id", ""))
            if not pid or pid in seen:
                continue
            seen.add(pid)
            try:
                player = _map_player(
                    row,
                    avg_map.get(pid),
                    recent_map.get(pid, []),
                )
                players.append(player)
            except Exception as exc:
                logger.warning(
                    "BallDontLie: failed to map player %s: %s", pid, exc
                )

        logger.info(
            "BallDontLie: %d player(s) built for game %s", len(players), game_id
        )
        return players

    # ------------------------------------------------------------------
    # Odds / props
    # ------------------------------------------------------------------

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        date_str = game_date.isoformat()

        # First try v2 player props endpoint
        prop_rows = self._client.get_all(
            "nba/v2/player_props",
            {"dates[]": [date_str], "per_page": 100},
        )

        if not prop_rows:
            logger.info(
                "BallDontLie: no player props for %s (endpoint may be empty or unavailable)",
                date_str,
            )
            return []

        # Group prop rows by game_id for OddsLine mapping
        lines: list[OddsLine] = []
        for raw in prop_rows:
            gid = str(raw.get("game_id", "") or "")
            try:
                line = _map_odds_line(raw, gid)
                if line:
                    lines.append(line)
            except Exception as exc:
                logger.warning("BallDontLie: failed to map prop row: %s", exc)

        logger.info(
            "BallDontLie: %d odds line(s) for %s", len(lines), date_str
        )
        return lines

    # ------------------------------------------------------------------
    # Team defense — returns league-average neutral defaults
    # ------------------------------------------------------------------

    def get_team_defense(self, team_id: str) -> Optional[TeamDefense]:
        """
        BallDontLie does not expose team defensive splits by position.
        Return a neutral TeamDefense (all factors at league average) so
        projection models run without crashing.  The evaluator already
        handles defense=None; returning a neutral object is cleaner.
        """
        return TeamDefense(
            team_id=team_id,
            team_abbr="",
            defensive_efficiency=LEAGUE_AVG_DEF_EFF,
            pace=LEAGUE_AVG_PACE,
            data_source=DataSource.BALLDONTLIE,
        )
