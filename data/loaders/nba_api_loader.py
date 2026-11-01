"""
Stats loader — BALLDONTLIE GOAT, same records the service layer already expects.

This module used to call NBA.com through the ``nba_api`` package. Those calls
are gone. Function names are unchanged so usage, splits, pace, and game-log
services keep working.

Missing statistics stay ``None``. A stored ``0`` is only written when the API
returned zero. Each record includes ``missing_fields``.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Optional

from data.loaders.balldontlie_client import BallDontLieAuthError, BallDontLieClient, BallDontLieError
from domain.constants import NBA_SEASON, balldontlie_season_start_year
from services.cache_service import get_cache

logger = logging.getLogger(__name__)

_CACHE = get_cache("balldontlie", default_ttl=86_400)
_CURRENT_SEASON = NBA_SEASON

_USAGE_KEYS = (
    "usg_pct", "usage_percentage", "usage_pct", "usage",
)
_TS_KEYS = ("ts_pct", "true_shooting_percentage", "ts")
_AST_PCT_KEYS = ("ast_pct", "assist_percentage")
_AST_RATIO_KEYS = ("ast_ratio", "assist_ratio")
_REB_PCT_KEYS = ("reb_pct", "rebound_percentage")
_OREB_PCT_KEYS = ("oreb_pct", "offensive_rebound_percentage")
_DREB_PCT_KEYS = ("dreb_pct", "defensive_rebound_percentage")
_PACE_KEYS = ("pace",)
_POSS_KEYS = ("poss", "possessions", "player_possessions")
_POTENTIAL_AST_KEYS = (
    "potential_ast", "potential_assists", "potential_assist",
)
_PASSES_KEYS = ("passes", "pass")
_SECONDARY_AST_KEYS = ("secondary_ast", "secondary_assists")
_TOUCHES_KEYS = ("touches", "touches_per_game")
_TIME_POSS_KEYS = ("time_of_poss", "time_of_possession")
_REB_CHANCE_KEYS = ("reb_chances", "rebound_chances", "total_rebound_chances")
_OREB_CHANCE_KEYS = ("oreb_chances", "offensive_rebound_chances")
_DREB_CHANCE_KEYS = ("dreb_chances", "defensive_rebound_chances")
_DEF_RATING_KEYS = ("def_rating", "defensive_rating")
_OFF_RATING_KEYS = ("off_rating", "offensive_rating")


_CLIENT: BallDontLieClient | None = None


def _client() -> BallDontLieClient:
    """One client per process so the rate limit applies across calls."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = BallDontLieClient()
    return _CLIENT


def _stats_blob(row: dict) -> dict:
    stats = row.get("stats")
    if isinstance(stats, dict):
        return stats
    return row


def _pick(stats: dict, keys: tuple[str, ...]) -> Any:
    """Return the first present value. Missing stays None. Zero stays zero."""
    for key in keys:
        if key in stats and stats[key] is not None:
            return stats[key]
    return None


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, str) and ":" in value:
        parts = value.split(":")
        try:
            return float(parts[0]) + float(parts[1]) / 60.0
        except (ValueError, IndexError):
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _usage_fraction(value: Any) -> Optional[float]:
    number = _as_float(value)
    if number is None:
        return None
    if number > 1.5:
        return number / 100.0
    return number


def _player_bits(row: dict) -> dict[str, Any]:
    player = row.get("player") if isinstance(row.get("player"), dict) else {}
    team = row.get("team") if isinstance(row.get("team"), dict) else {}
    if not team and isinstance(player.get("team"), dict):
        team = player["team"]
    first = player.get("first_name") or ""
    last = player.get("last_name") or ""
    name = f"{first} {last}".strip() or str(row.get("player_name") or "")
    pid = player.get("id", row.get("player_id"))
    return {
        "player_id": "" if pid is None else str(pid),
        "player_name": name,
        "team_id": "" if team.get("id") is None else str(team.get("id")),
        "team_abbr": str(team.get("abbreviation") or ""),
        "position": str(player.get("position") or ""),
    }


def _teams_for_player_ids(player_ids: list[str]) -> dict[str, dict]:
    """Current team for specific players. Season-average rows omit the team."""
    directory: dict[str, dict] = {}
    client = _client()
    unique = [pid for pid in dict.fromkeys(player_ids) if pid]
    for start in range(0, len(unique), 50):
        chunk = unique[start:start + 50]
        try:
            rows = client.paginate("nba/v1/players", {"player_ids": chunk})
        except (BallDontLieAuthError, BallDontLieError) as exc:
            logger.error("BALLDONTLIE player team lookup failed: %s", exc)
            continue
        for row in rows:
            pid = row.get("id")
            if pid is None:
                continue
            team = row.get("team") if isinstance(row.get("team"), dict) else {}
            directory[str(pid)] = {
                "team_id": "" if team.get("id") is None else str(team.get("id")),
                "team_abbr": str(team.get("abbreviation") or ""),
                "position": str(row.get("position") or ""),
            }
    return directory


def _fill_team_from_directory(records: list[dict]) -> None:
    missing = [rec for rec in records if rec.get("player_id") and not rec.get("team_id")]
    if not missing:
        return
    directory = _teams_for_player_ids([str(rec["player_id"]) for rec in missing])
    for rec in missing:
        extra = directory.get(str(rec.get("player_id") or "")) or {}
        if extra.get("team_id"):
            rec["team_id"] = extra["team_id"]
            rec["team_abbr"] = extra.get("team_abbr") or rec.get("team_abbr") or ""
        if not rec.get("position") and extra.get("position"):
            rec["position"] = extra["position"]


def _note_missing(record: dict, field: str, value: Any) -> None:
    record[field] = value
    if value is None:
        record.setdefault("missing_fields", []).append(field)


def _fetch_category(category: str, stat_type: str, season: str) -> list[dict]:
    year = balldontlie_season_start_year(season)
    path = f"nba/v1/season_averages/{category}"
    try:
        return _client().paginate(
            path,
            {"season": year, "season_type": "regular", "type": stat_type},
        )
    except BallDontLieAuthError as exc:
        logger.error("BALLDONTLIE auth failed for %s/%s: %s", category, stat_type, exc)
        return []
    except BallDontLieError as exc:
        logger.error("BALLDONTLIE %s/%s failed: %s", category, stat_type, exc)
        return []


def fetch_usage_dashboard_batch(
    season: str = _CURRENT_SEASON,
    date_str: Optional[str] = None,
) -> list[dict]:
    """League advanced + usage season averages, keyed like the old dashboard."""
    cache_key = f"usage_advanced_{season}_{date_str or date.today().isoformat()}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    advanced = _fetch_category("general", "advanced", season)
    usage = _fetch_category("general", "usage", season)
    usage_by_id = {_player_bits(row)["player_id"]: _stats_blob(row) for row in usage}

    records = []
    for row in advanced:
        ident = _player_bits(row)
        stats = dict(_stats_blob(row))
        extra = usage_by_id.get(ident["player_id"]) or {}
        stats.update({k: v for k, v in extra.items() if v is not None})
        rec: dict[str, Any] = {
            **ident,
            "raw": stats,
            "missing_fields": [],
            "source": "balldontlie",
        }
        _note_missing(rec, "gp", _as_float(_pick(stats, ("gp", "games_played"))))
        _note_missing(rec, "min", _as_float(_pick(stats, ("min", "minutes"))))
        _note_missing(rec, "usg_pct", _usage_fraction(_pick(stats, _USAGE_KEYS)))
        _note_missing(rec, "ts_pct", _as_float(_pick(stats, _TS_KEYS)))
        _note_missing(rec, "ast_ratio", _as_float(_pick(stats, _AST_RATIO_KEYS)))
        _note_missing(rec, "ast_pct", _as_float(_pick(stats, _AST_PCT_KEYS)))
        _note_missing(rec, "reb_pct", _as_float(_pick(stats, _REB_PCT_KEYS)))
        _note_missing(rec, "oreb_pct", _as_float(_pick(stats, _OREB_PCT_KEYS)))
        _note_missing(rec, "dreb_pct", _as_float(_pick(stats, _DREB_PCT_KEYS)))
        _note_missing(rec, "pace", _as_float(_pick(stats, _PACE_KEYS)))
        _note_missing(rec, "poss", _as_float(_pick(stats, _POSS_KEYS)))
        _note_missing(rec, "off_rating", _as_float(_pick(stats, _OFF_RATING_KEYS)))
        if rec["player_id"]:
            records.append(rec)

    _CACHE.set(cache_key, records)
    logger.info("BALLDONTLIE usage dashboard: %d players", len(records))
    return records


def fetch_today_player_tracking_batch(
    season: str = _CURRENT_SEASON,
    pt_measure_type: str = "Possessions",
    date_str: Optional[str] = None,
) -> list[dict]:
    """Tracking season averages. Measure names match the old nba_api switches."""
    cache_key = f"tracking_{pt_measure_type}_{season}_{date_str or date.today().isoformat()}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    type_map = {
        "Possessions": "possessions",
        "Passing": "passing",
        "Rebounding": "rebounding",
    }
    stat_type = type_map.get(pt_measure_type, pt_measure_type.lower())
    rows = _fetch_category("tracking", stat_type, season)
    records = []
    for row in rows:
        ident = _player_bits(row)
        stats = _stats_blob(row)
        rec: dict[str, Any] = {
            **ident,
            "raw": stats,
            "missing_fields": [],
            "source": "balldontlie",
            "gp": _as_float(_pick(stats, ("gp", "games_played"))),
            "min": _as_float(_pick(stats, ("min", "minutes"))),
        }
        if pt_measure_type == "Possessions":
            _note_missing(rec, "touches", _as_float(_pick(stats, _TOUCHES_KEYS)))
            _note_missing(rec, "time_of_poss", _as_float(_pick(stats, _TIME_POSS_KEYS)))
            _note_missing(rec, "front_ct_touches", _as_float(_pick(stats, ("front_ct_touches", "front_court_touches"))))
            _note_missing(rec, "elbow_touches", _as_float(_pick(stats, ("elbow_touches",))))
            _note_missing(rec, "post_touches", _as_float(_pick(stats, ("post_touches",))))
            _note_missing(rec, "paint_touches", _as_float(_pick(stats, ("paint_touches",))))
        elif pt_measure_type == "Passing":
            _note_missing(rec, "ast", _as_float(_pick(stats, ("ast", "assists"))))
            _note_missing(rec, "potential_ast", _as_float(_pick(stats, _POTENTIAL_AST_KEYS)))
            _note_missing(rec, "passes", _as_float(_pick(stats, _PASSES_KEYS)))
            _note_missing(rec, "secondary_ast", _as_float(_pick(stats, _SECONDARY_AST_KEYS)))
            _note_missing(rec, "ast_pts_created", _as_float(_pick(stats, ("ast_pts_created", "assist_points_created"))))
        elif pt_measure_type == "Rebounding":
            _note_missing(rec, "reb_chances", _as_float(_pick(stats, _REB_CHANCE_KEYS)))
            _note_missing(rec, "oreb_chances", _as_float(_pick(stats, _OREB_CHANCE_KEYS)))
            _note_missing(rec, "dreb_chances", _as_float(_pick(stats, _DREB_CHANCE_KEYS)))
            _note_missing(rec, "oreb", _as_float(_pick(stats, ("oreb", "offensive_rebounds"))))
            _note_missing(rec, "dreb", _as_float(_pick(stats, ("dreb", "defensive_rebounds"))))
            _note_missing(rec, "contested_rebounds", _as_float(_pick(stats, ("contested_rebounds", "reb_contested"))))
            _note_missing(rec, "uncontested_rebounds", _as_float(_pick(stats, ("uncontested_rebounds", "reb_uncontested"))))
        else:
            for key, value in stats.items():
                if key not in rec:
                    rec[key] = value
        if rec["player_id"]:
            records.append(rec)

    _CACHE.set(cache_key, records)
    logger.info("BALLDONTLIE tracking %s: %d players", pt_measure_type, len(records))
    return records


def fetch_split_context_batch(
    season: str = _CURRENT_SEASON,
    last_n_games: int = 0,
    location: Optional[str] = None,
    date_str: Optional[str] = None,
) -> list[dict]:
    """
    Season box averages from ``general/base``.

    Home, road, and last-N windows are not a GOAT season-average filter.
    Those calls return an empty list instead of copying the season line and
    pretending it is a split. Recent form still comes from game logs when
    a caller requests them per player.
    """
    if last_n_games or location:
        logger.info(
            "BALLDONTLIE has no season-average filter for last_n=%s location=%s; leaving that split empty",
            last_n_games, location,
        )
        return []

    cache_key = f"splits_{season}_season_all_v2_{date_str or date.today().isoformat()}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    rows = _fetch_category("general", "base", season)
    records = []
    for row in rows:
        ident = _player_bits(row)
        stats = _stats_blob(row)
        rec = {
            **ident,
            "raw": stats,
            "missing_fields": [],
            "source": "balldontlie",
            "split_tag": "season",
            "location": "all",
        }
        for field, keys in (
            ("gp", ("gp", "games_played")),
            ("min", ("min", "minutes")),
            ("pts", ("pts", "points")),
            ("reb", ("reb", "rebounds")),
            ("ast", ("ast", "assists")),
            ("stl", ("stl", "steals")),
            ("blk", ("blk", "blocks")),
            ("tov", ("tov", "turnovers", "turnover")),
            ("fg3m", ("fg3m", "fg3_made", "three_pointers_made")),
            ("fg3a", ("fg3a", "fg3_attempted")),
            ("fga", ("fga", "field_goals_attempted")),
            ("fgm", ("fgm", "field_goals_made")),
            ("fta", ("fta", "free_throws_attempted")),
            ("ftm", ("ftm", "free_throws_made")),
        ):
            _note_missing(rec, field, _as_float(_pick(stats, keys)))
        if rec["player_id"]:
            records.append(rec)
    _fill_team_from_directory(records)
    _CACHE.set(cache_key, records)
    logger.info("BALLDONTLIE season base averages: %d players", len(records))
    return records


def fetch_team_pace_batch(
    season: str = _CURRENT_SEASON,
    date_str: Optional[str] = None,
) -> list[dict]:
    cache_key = f"team_pace_{season}_{date_str or date.today().isoformat()}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    year = balldontlie_season_start_year(season)
    try:
        rows = _client().paginate(
            "nba/v1/team_season_averages/general",
            {"season": year, "season_type": "regular", "type": "advanced"},
        )
    except (BallDontLieAuthError, BallDontLieError) as exc:
        logger.error("BALLDONTLIE team pace failed: %s", exc)
        return []

    records = []
    for row in rows:
        team = row.get("team") if isinstance(row.get("team"), dict) else {}
        stats = _stats_blob(row)
        tid = team.get("id", row.get("team_id"))
        rec = {
            "team_id": "" if tid is None else str(tid),
            "team_name": str(team.get("full_name") or team.get("name") or ""),
            "team_abbr": str(team.get("abbreviation") or ""),
            "raw": stats,
            "missing_fields": [],
            "source": "balldontlie",
        }
        _note_missing(rec, "gp", _as_float(_pick(stats, ("gp", "games_played"))))
        _note_missing(rec, "poss", _as_float(_pick(stats, _POSS_KEYS)))
        _note_missing(rec, "pace", _as_float(_pick(stats, _PACE_KEYS)))
        _note_missing(rec, "off_rating", _as_float(_pick(stats, _OFF_RATING_KEYS)))
        _note_missing(rec, "def_rating", _as_float(_pick(stats, _DEF_RATING_KEYS)))
        _note_missing(rec, "net_rating", _as_float(_pick(stats, ("net_rating",))))
        if rec["team_id"]:
            records.append(rec)
    _CACHE.set(cache_key, records)
    logger.info("BALLDONTLIE team pace: %d teams", len(records))
    return records


def fetch_recent_player_logs_batch(
    player_ids: list[str],
    last_n: int = 10,
    season: str = _CURRENT_SEASON,
) -> dict[str, list[dict]]:
    """Box scores from ``GET /v1/stats``. Missing box fields stay None."""
    year = balldontlie_season_start_year(season)
    result: dict[str, list[dict]] = {}
    client = _client()
    for player_id in player_ids:
        cache_key = f"gamelogs_{player_id}_{season}_{last_n}"
        cached = _CACHE.get(cache_key)
        if cached is not None:
            result[player_id] = cached
            continue
        try:
            rows = client.paginate(
                "v1/stats",
                {"player_ids": [player_id], "seasons": [year], "per_page": 100},
            )
        except (BallDontLieAuthError, BallDontLieError) as exc:
            logger.warning("BALLDONTLIE game logs failed for %s: %s", player_id, exc)
            result[player_id] = []
            continue
        logs = []
        for row in rows:
            stats = _stats_blob(row)
            game = row.get("game") if isinstance(row.get("game"), dict) else {}
            box = row if "pts" in row or "reb" in row else stats
            def _box(name: str, *aliases: str) -> Optional[float]:
                for key in (name, *aliases):
                    if key in box and box[key] is not None:
                        return _as_float(box[key])
                return None
            logs.append({
                "game_id": "" if game.get("id") is None else str(game.get("id")),
                "game_date": str(game.get("date") or "")[:10],
                "min": _box("min"),
                "pts": _box("pts"),
                "reb": _box("reb"),
                "ast": _box("ast"),
                "stl": _box("stl"),
                "blk": _box("blk"),
                "tov": _box("turnover", "tov"),
                "fg3m": _box("fg3m"),
                "raw": row,
                "source": "balldontlie",
            })
        logs.sort(key=lambda item: item.get("game_date") or "", reverse=True)
        trimmed = logs[:last_n]
        _CACHE.set(cache_key, trimmed)
        result[player_id] = trimmed
    return result


def fetch_player_info_batch(player_ids: list[str]) -> dict[str, dict]:
    """Player bio from ``GET /v1/players``. BDL positions are often G/F/C, not PG–C."""
    result: dict[str, dict] = {}
    client = _client()
    for player_id in player_ids:
        cache_key = f"player_info_{player_id}"
        cached = _CACHE.get(cache_key)
        if cached is not None:
            result[player_id] = cached
            continue
        try:
            body = client.get("v1/players", {"player_ids": [player_id]})
        except (BallDontLieAuthError, BallDontLieError) as exc:
            logger.warning("BALLDONTLIE player info failed for %s: %s", player_id, exc)
            result[player_id] = {}
            continue
        data = body.get("data") or []
        row = data[0] if data else {}
        team = row.get("team") if isinstance(row.get("team"), dict) else {}
        info = {
            "player_id": str(row.get("id") or player_id),
            "full_name": f"{row.get('first_name', '')} {row.get('last_name', '')}".strip(),
            "team_id": "" if team.get("id") is None else str(team.get("id")),
            "team_abbr": str(team.get("abbreviation") or ""),
            "position": str(row.get("position") or ""),
            "position_granularity": "broad" if str(row.get("position") or "") in {"G", "F", "C"} else "specific",
            "source": "balldontlie",
        }
        _CACHE.set(cache_key, info, ttl_seconds=604_800)
        result[player_id] = info
    return result


def index_by_player_id(records: list[dict]) -> dict[str, dict]:
    return {r["player_id"]: r for r in records if r.get("player_id")}


def map_box_score_to_dvp_log(row: dict) -> dict:
    """Turn one BALLDONTLIE box-score row into a DvP game log."""
    player = row.get("player") if isinstance(row.get("player"), dict) else {}
    team = row.get("team") if isinstance(row.get("team"), dict) else {}
    if not team and isinstance(player.get("team"), dict):
        team = player["team"]
    game = row.get("game") if isinstance(row.get("game"), dict) else {}
    team_id = str(team.get("id") or "")
    home = game.get("home_team") if isinstance(game.get("home_team"), dict) else {}
    away = game.get("visitor_team") if isinstance(game.get("visitor_team"), dict) else {}
    home_id = str(home.get("id") or game.get("home_team_id") or "")
    away_id = str(away.get("id") or game.get("visitor_team_id") or "")
    if team_id and team_id == home_id:
        opponent_id = away_id
    elif team_id and team_id == away_id:
        opponent_id = home_id
    else:
        opponent_id = ""
    pid = player.get("id", row.get("player_id"))
    return {
        "player_id": "" if pid is None else str(pid),
        "player_name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
        "team_id": team_id,
        "opponent_team_id": opponent_id,
        "position": str(player.get("position") or ""),
        "game_date": str(game.get("date") or "")[:10],
        "pts": row.get("pts"),
        "reb": row.get("reb"),
        "ast": row.get("ast"),
        "fg3m": row.get("fg3m"),
        "stl": row.get("stl"),
        "blk": row.get("blk"),
        "tov": row.get("turnover") if row.get("turnover") is not None else row.get("tov"),
    }


def fetch_recent_league_box_scores(
    game_date: date,
    days: int = 21,
) -> list[dict]:
    """
    League box scores over a recent window from ``GET /v1/stats``.

    One paginated pull, cached for the day. Used to build positional
    fantasy-points-allowed. Missing opponent ids are dropped by the DvP builder.
    """
    cache_key = f"league_box_{game_date.isoformat()}_{days}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    start = game_date - timedelta(days=days)
    try:
        rows = _client().paginate(
            "v1/stats",
            {
                "start_date": start.isoformat(),
                "end_date": game_date.isoformat(),
            },
        )
    except (BallDontLieAuthError, BallDontLieError) as exc:
        logger.error("BALLDONTLIE recent box scores failed: %s", exc)
        return []

    logs = [map_box_score_to_dvp_log(row) for row in rows]
    logs.sort(key=lambda r: r.get("game_date") or "", reverse=True)
    _CACHE.set(cache_key, logs)
    logger.info("BALLDONTLIE recent box scores: %d rows over %d days", len(logs), days)
    return logs


def _parse_minutes(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if ":" in text:
        minutes, seconds = text.split(":", 1)
        try:
            return int(minutes) + int(seconds) / 60.0
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def map_box_score_to_backtest_log(row: dict) -> dict:
    """Box score fields needed to rebuild a pregame state. No future games are added here."""
    base = map_box_score_to_dvp_log(row)
    base["min"] = _parse_minutes(row.get("min"))
    base["fga"] = row.get("fga")
    base["fg3a"] = row.get("fg3a")
    base["fta"] = row.get("fta")
    return base


def fetch_box_scores_between(start: date, end_inclusive: date) -> list[dict]:
    """
    League box scores from ``start`` through ``end_inclusive``.

    Callers that need a pregame state must pass an end date before tip-off.
    """
    cache_key = f"backtest_box_{start.isoformat()}_{end_inclusive.isoformat()}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        rows = _client().paginate(
            "v1/stats",
            {"start_date": start.isoformat(), "end_date": end_inclusive.isoformat()},
        )
    except (BallDontLieAuthError, BallDontLieError) as exc:
        logger.error("BALLDONTLIE backtest box scores failed: %s", exc)
        return []
    logs = [map_box_score_to_backtest_log(row) for row in rows]
    logs.sort(key=lambda r: r.get("game_date") or "", reverse=True)
    _CACHE.set(cache_key, logs)
    logger.info(
        "BALLDONTLIE backtest box scores: %d rows from %s to %s",
        len(logs), start.isoformat(), end_inclusive.isoformat(),
    )
    return logs


def fetch_balldontlie_injuries() -> list[dict]:
    """Current injuries from ``GET /v1/player_injuries``. Missing fields stay blank."""
    cache_key = f"injuries_{date.today().isoformat()}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        rows = _client().paginate("v1/player_injuries", {})
    except (BallDontLieAuthError, BallDontLieError) as exc:
        logger.error("BALLDONTLIE injuries failed: %s", exc)
        return []

    records = []
    for row in rows:
        player = row.get("player") if isinstance(row.get("player"), dict) else {}
        team = player.get("team") if isinstance(player.get("team"), dict) else {}
        pid = player.get("id", row.get("player_id"))
        if pid is None:
            continue
        team_id = player.get("team_id", team.get("id"))
        records.append({
            "player_id": str(pid),
            "player_name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
            "team_id": "" if team_id is None else str(team_id),
            "position": str(player.get("position") or ""),
            "status": str(row.get("status") or ""),
            "injury": str(row.get("description") or row.get("comment") or ""),
        })
    _CACHE.set(cache_key, records, ttl_seconds=900)
    logger.info("BALLDONTLIE injuries: %d rows", len(records))
    return records
