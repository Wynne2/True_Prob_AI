"""
nba_api Loader  –  PRIMARY source for usage, touches, possessions, splits.

All functions here return raw normalised dicts (not domain entities) so they
can be composed by the service layer.  Every function:
  - Pulls at the broadest granularity available (team/league dashboards, not
    per-player endpoints) to minimise request volume.
  - Caches results to disk under data/cache/nba_api/ with date-stamped keys.
  - Never calls nba_api inside a per-prop loop.
  - Sleeps briefly between sequential requests to respect NBA.com rate limits
    (~60 req/min; 0.6 s sleep keeps us comfortably under that ceiling).

Source of truth (per architecture spec):
  - Usage rate      -> nba_api (LeagueDashPlayerStats advanced)
  - Touches         -> nba_api (LeagueDashPtStats tracking)
  - Possessions/pace-> nba_api (LeagueDashTeamStats / LeagueDashPlayerStats)
  - Splits          -> nba_api (LeagueDashPlayerStats w/ split filters)
  - Recent logs     -> nba_api (PlayerGameLog, per player, cached)
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Optional

from services.cache_service import get_cache

logger = logging.getLogger(__name__)

_CACHE = get_cache("nba_api", default_ttl=86_400)   # 24-hour default TTL
_RATE_LIMIT_SLEEP = 0.6   # seconds between sequential nba_api calls

# Current season string – matches the nba_api convention (year the season ends)
_CURRENT_SEASON = "2025-26"


def _sleep() -> None:
    """Rate-limit guard between sequential nba_api calls."""
    time.sleep(_RATE_LIMIT_SLEEP)


# ---------------------------------------------------------------------------
# Usage Rate Dashboard (PRIMARY: nba_api -> LeagueDashPlayerStats advanced)
# ---------------------------------------------------------------------------

def fetch_usage_dashboard_batch(
    season: str = _CURRENT_SEASON,
    date_str: Optional[str] = None,
) -> list[dict]:
    """
    Fetch advanced per-player stats including usage rate for the full league.

    Returns a list of player dicts with keys including:
        player_id, player_name, team_id, team_abbr,
        usg_pct, min, pts, reb, ast, stl, blk, tov,
        fga, fg3a, fta, ts_pct, ast_ratio, reb_pct

    Caches by season+date so re-runs within the same day skip the network.

    SOURCE: nba_api.stats.endpoints.LeagueDashPlayerStats (MeasureType=Advanced)
    """
    cache_key = f"usage_advanced_{season}_{date_str or date.today().isoformat()}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        logger.debug("Cache hit: %s", cache_key)
        return cached

    try:
        from nba_api.stats.endpoints import LeagueDashPlayerStats

        logger.info("nba_api: fetching usage dashboard (advanced) for season %s", season)
        endpoint = LeagueDashPlayerStats(
            season=season,
            measure_type_detailed_defense="Advanced",
            per_mode_detailed="PerGame",
            timeout=30,
        )
        _sleep()
        df = endpoint.get_data_frames()[0]

        records = []
        for _, row in df.iterrows():
            records.append({
                "player_id": str(row.get("PLAYER_ID", "")),
                "player_name": str(row.get("PLAYER_NAME", "")),
                "team_id": str(row.get("TEAM_ID", "")),
                "team_abbr": str(row.get("TEAM_ABBREVIATION", "")),
                "age": float(row.get("AGE", 0) or 0),
                "gp": int(row.get("GP", 0) or 0),
                "min": float(row.get("MIN", 0) or 0),
                # Usage and advanced context (SOURCE: nba_api primary)
                "usg_pct": float(row.get("USG_PCT", 0) or 0),
                "ts_pct": float(row.get("TS_PCT", 0) or 0),
                "ast_ratio": float(row.get("AST_RATIO", 0) or 0),
                "ast_pct": float(row.get("AST_PCT", 0) or 0),
                "reb_pct": float(row.get("REB_PCT", 0) or 0),
                "oreb_pct": float(row.get("OREB_PCT", 0) or 0),
                "dreb_pct": float(row.get("DREB_PCT", 0) or 0),
                "pace": float(row.get("PACE", 0) or 0),
                "pace_per40": float(row.get("PACE_PER40", 0) or 0),
                "pie": float(row.get("PIE", 0) or 0),
                # Possessions context
                "poss": float(row.get("POSS", 0) or 0),
            })

        _CACHE.set(cache_key, records)
        logger.info("nba_api usage dashboard: fetched %d player records", len(records))
        return records

    except Exception as exc:
        logger.error("nba_api usage dashboard failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Tracking Dashboard (PRIMARY: nba_api -> LeagueDashPtStats)
# ---------------------------------------------------------------------------

def fetch_today_player_tracking_batch(
    season: str = _CURRENT_SEASON,
    pt_measure_type: str = "Possessions",
    date_str: Optional[str] = None,
) -> list[dict]:
    """
    Fetch player tracking metrics for the full league.

    pt_measure_type options: 'Possessions', 'Passing', 'Rebounding',
    'Defense', 'Efficiency', 'SpeedDistance', 'CatchShoot', 'PullUpShot'

    Returns dicts with keys including:
        player_id, player_name, team_id, touches, time_of_poss,
        front_ct_touches, elbow_touches, post_touches, paint_touches,
        ast_pts_created, pass_ast, secondary_ast, potential_ast, pts

    SOURCE: nba_api.stats.endpoints.LeagueDashPtStats (PRIMARY: touches/possessions)
    """
    cache_key = f"tracking_{pt_measure_type}_{season}_{date_str or date.today().isoformat()}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        logger.debug("Cache hit: %s", cache_key)
        return cached

    try:
        from nba_api.stats.endpoints import LeagueDashPtStats

        logger.info(
            "nba_api: fetching player tracking (%s) for season %s",
            pt_measure_type, season,
        )
        endpoint = LeagueDashPtStats(
            season=season,
            pt_measure_type=pt_measure_type,
            per_mode_simple="PerGame",
            player_or_team="Player",
            timeout=30,
        )
        _sleep()
        df = endpoint.get_data_frames()[0]

        records = []
        for _, row in df.iterrows():
            rec: dict[str, Any] = {
                "player_id": str(row.get("PLAYER_ID", "")),
                "player_name": str(row.get("PLAYER_NAME", "")),
                "team_id": str(row.get("TEAM_ID", "")),
                "team_abbr": str(row.get("TEAM_ABBREVIATION", "")),
                "gp": int(row.get("GP", 0) or 0),
                "min": float(row.get("MIN", 0) or 0),
            }
            # Possessions measure fields (SOURCE: nba_api PRIMARY for touches)
            if pt_measure_type == "Possessions":
                rec.update({
                    "touches": float(row.get("TOUCHES", 0) or 0),
                    "front_ct_touches": float(row.get("FRONT_CT_TOUCHES", 0) or 0),
                    "time_of_poss": float(row.get("TIME_OF_POSS", 0) or 0),
                    "elbow_touches": float(row.get("ELBOW_TOUCHES", 0) or 0),
                    "post_touches": float(row.get("POST_TOUCHES", 0) or 0),
                    "paint_touches": float(row.get("PAINT_TOUCHES", 0) or 0),
                    "pts_per_touch": float(row.get("PTS_PER_TOUCH", 0) or 0),
                })
            elif pt_measure_type == "Passing":
                rec.update({
                    "ast": float(row.get("AST", 0) or 0),
                    "potential_ast": float(row.get("POTENTIAL_AST", 0) or 0),
                    "ast_pts_created": float(row.get("AST_PTS_CREATED", 0) or 0),
                    "pass_adj_ast": float(row.get("PASS_ADJ_AST", 0) or 0),
                    "secondary_ast": float(row.get("SECONDARY_AST", 0) or 0),
                })
            elif pt_measure_type == "Rebounding":
                rec.update({
                    "oreb_chances": float(row.get("OREB_CHANCES", 0) or 0),
                    "dreb_chances": float(row.get("DREB_CHANCES", 0) or 0),
                    "reb_chances": float(row.get("REB_CHANCES", 0) or 0),
                    "oreb": float(row.get("OREB", 0) or 0),
                    "dreb": float(row.get("DREB", 0) or 0),
                })
            else:
                # Generic passthrough of all numeric columns
                for col in df.columns:
                    if col not in ("PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "TEAM_ABBREVIATION"):
                        try:
                            rec[col.lower()] = float(row.get(col, 0) or 0)
                        except (ValueError, TypeError):
                            rec[col.lower()] = str(row.get(col, ""))

            records.append(rec)

        _CACHE.set(cache_key, records)
        logger.info(
            "nba_api tracking (%s): fetched %d player records", pt_measure_type, len(records)
        )
        return records

    except Exception as exc:
        logger.error("nba_api tracking (%s) failed: %s", pt_measure_type, exc)
        return []


# ---------------------------------------------------------------------------
# Recent Game Logs (nba_api -> PlayerGameLog, batched per player, cached)
# ---------------------------------------------------------------------------

def fetch_recent_player_logs_batch(
    player_ids: list[str],
    last_n: int = 10,
    season: str = _CURRENT_SEASON,
) -> dict[str, list[dict]]:
    """
    Fetch the last *last_n* game logs for each player in *player_ids*.

    Returns dict: player_id -> list of game-log dicts.

    Each game-log dict contains:
        game_id, game_date, matchup, wl, min,
        pts, reb, ast, stl, blk, tov, fg3m, plus_minus

    Caches each player individually so partial runs benefit from partial caches.
    Sleeps between each player call to respect rate limits.

    SOURCE: nba_api.stats.endpoints.PlayerGameLog
    """
    result: dict[str, list[dict]] = {}

    for player_id in player_ids:
        cache_key = f"gamelogs_{player_id}_{season}_{last_n}"
        cached = _CACHE.get(cache_key)
        if cached is not None:
            result[player_id] = cached
            continue

        try:
            from nba_api.stats.endpoints import PlayerGameLog

            logger.debug("nba_api: fetching game logs for player %s", player_id)
            endpoint = PlayerGameLog(
                player_id=player_id,
                season=season,
                timeout=20,
            )
            _sleep()
            df = endpoint.get_data_frames()[0]

            logs = []
            for _, row in df.head(last_n).iterrows():
                logs.append({
                    "game_id": str(row.get("Game_ID", "")),
                    "game_date": str(row.get("GAME_DATE", "")),
                    "matchup": str(row.get("MATCHUP", "")),
                    "wl": str(row.get("WL", "")),
                    "min": float(row.get("MIN", 0) or 0),
                    "pts": float(row.get("PTS", 0) or 0),
                    "reb": float(row.get("REB", 0) or 0),
                    "ast": float(row.get("AST", 0) or 0),
                    "stl": float(row.get("STL", 0) or 0),
                    "blk": float(row.get("BLK", 0) or 0),
                    "tov": float(row.get("TOV", 0) or 0),
                    "fg3m": float(row.get("FG3M", 0) or 0),
                    "fga": float(row.get("FGA", 0) or 0),
                    "fgm": float(row.get("FGM", 0) or 0),
                    "fta": float(row.get("FTA", 0) or 0),
                    "ftm": float(row.get("FTM", 0) or 0),
                    "plus_minus": float(row.get("PLUS_MINUS", 0) or 0),
                })

            _CACHE.set(cache_key, logs, ttl_seconds=43_200)  # 12-hour for logs
            result[player_id] = logs

        except Exception as exc:
            logger.warning("nba_api game logs failed for player %s: %s", player_id, exc)
            result[player_id] = []

    return result


# ---------------------------------------------------------------------------
# Split Context Dashboard (nba_api -> LeagueDashPlayerStats with split filters)
# ---------------------------------------------------------------------------

def fetch_split_context_batch(
    season: str = _CURRENT_SEASON,
    last_n_games: int = 0,
    location: Optional[str] = None,
    date_str: Optional[str] = None,
) -> list[dict]:
    """
    Fetch per-player stats with optional split filters.

    Parameters
    ----------
    last_n_games : int
        0 = full season; 5 = last 5; 10 = last 10, etc.
    location : str | None
        None = all; "Home" = home games only; "Road" = away only.

    Returns list of player dicts with box-score averages for the split.

    SOURCE: nba_api.stats.endpoints.LeagueDashPlayerStats (PRIMARY: splits)
    """
    split_tag = f"L{last_n_games}" if last_n_games else "season"
    loc_tag = location.lower() if location else "all"
    cache_key = f"splits_{season}_{split_tag}_{loc_tag}_{date_str or date.today().isoformat()}"

    cached = _CACHE.get(cache_key)
    if cached is not None:
        logger.debug("Cache hit: %s", cache_key)
        return cached

    try:
        from nba_api.stats.endpoints import LeagueDashPlayerStats

        logger.info(
            "nba_api: fetching split context (last_n=%d, location=%s) for %s",
            last_n_games, location, season,
        )
        kwargs: dict[str, Any] = {
            "season": season,
            "measure_type_detailed_defense": "Base",
            "per_mode_detailed": "PerGame",
            "timeout": 30,
        }
        if last_n_games:
            kwargs["last_n_games"] = last_n_games
        if location:
            kwargs["location_nullable"] = location

        endpoint = LeagueDashPlayerStats(**kwargs)
        _sleep()
        df = endpoint.get_data_frames()[0]

        records = []
        for _, row in df.iterrows():
            records.append({
                "player_id": str(row.get("PLAYER_ID", "")),
                "player_name": str(row.get("PLAYER_NAME", "")),
                "team_id": str(row.get("TEAM_ID", "")),
                "team_abbr": str(row.get("TEAM_ABBREVIATION", "")),
                "gp": int(row.get("GP", 0) or 0),
                "min": float(row.get("MIN", 0) or 0),
                "pts": float(row.get("PTS", 0) or 0),
                "reb": float(row.get("REB", 0) or 0),
                "ast": float(row.get("AST", 0) or 0),
                "stl": float(row.get("STL", 0) or 0),
                "blk": float(row.get("BLK", 0) or 0),
                "tov": float(row.get("TOV", 0) or 0),
                "fg3m": float(row.get("FG3M", 0) or 0),
                "fga": float(row.get("FGA", 0) or 0),
                "fgm": float(row.get("FGM", 0) or 0),
                "fta": float(row.get("FTA", 0) or 0),
                "split_tag": split_tag,
                "location": loc_tag,
            })

        _CACHE.set(cache_key, records)
        logger.info("nba_api splits (%s/%s): fetched %d records", split_tag, loc_tag, len(records))
        return records

    except Exception as exc:
        logger.error("nba_api splits fetch failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Team-Level Pace / Possessions Dashboard
# ---------------------------------------------------------------------------

def fetch_team_pace_batch(
    season: str = _CURRENT_SEASON,
    date_str: Optional[str] = None,
) -> list[dict]:
    """
    Fetch team-level pace and possessions context.

    Returns list of team dicts with:
        team_id, team_name, gp, poss, pace, off_rating, def_rating, net_rating

    SOURCE: nba_api.stats.endpoints.LeagueDashTeamStats (PRIMARY: pace/possessions)
    """
    cache_key = f"team_pace_{season}_{date_str or date.today().isoformat()}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        from nba_api.stats.endpoints import LeagueDashTeamStats

        logger.info("nba_api: fetching team pace dashboard for season %s", season)
        endpoint = LeagueDashTeamStats(
            season=season,
            measure_type_detailed_defense="Advanced",
            per_mode_detailed="PerGame",
            timeout=30,
        )
        _sleep()
        df = endpoint.get_data_frames()[0]

        records = []
        for _, row in df.iterrows():
            records.append({
                "team_id": str(row.get("TEAM_ID", "")),
                "team_name": str(row.get("TEAM_NAME", "")),
                "team_abbr": str(row.get("TEAM_ABBREVIATION", "")),
                "gp": int(row.get("GP", 0) or 0),
                "poss": float(row.get("POSS", 0) or 0),
                "pace": float(row.get("PACE", 0) or 0),
                "pace_per40": float(row.get("PACE_PER40", 0) or 0),
                "off_rating": float(row.get("OFF_RATING", 0) or 0),
                "def_rating": float(row.get("DEF_RATING", 0) or 0),
                "net_rating": float(row.get("NET_RATING", 0) or 0),
                "ast_pct": float(row.get("AST_PCT", 0) or 0),
                "ast_to": float(row.get("AST_TO", 0) or 0),
                "ts_pct": float(row.get("TS_PCT", 0) or 0),
            })

        _CACHE.set(cache_key, records)
        logger.info("nba_api team pace: fetched %d team records", len(records))
        return records

    except Exception as exc:
        logger.error("nba_api team pace fetch failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Player Info (position, age, etc.)
# ---------------------------------------------------------------------------

def fetch_player_info_batch(
    player_ids: list[str],
) -> dict[str, dict]:
    """
    Fetch basic player bio for a list of player IDs.

    Returns dict: player_id -> {position, height, weight, team_id, ...}

    Caches indefinitely (bio data rarely changes mid-season).

    SOURCE: nba_api.stats.endpoints.CommonPlayerInfo
    """
    result: dict[str, dict] = {}

    for player_id in player_ids:
        cache_key = f"player_info_{player_id}"
        cached = _CACHE.get(cache_key)
        if cached is not None:
            result[player_id] = cached
            continue

        try:
            from nba_api.stats.endpoints import CommonPlayerInfo

            endpoint = CommonPlayerInfo(player_id=player_id, timeout=15)
            _sleep()
            df = endpoint.get_data_frames()[0]
            if df.empty:
                result[player_id] = {}
                continue
            row = df.iloc[0]
            info = {
                "player_id": str(row.get("PERSON_ID", player_id)),
                "full_name": str(row.get("DISPLAY_FIRST_LAST", "")),
                "team_id": str(row.get("TEAM_ID", "")),
                "team_abbr": str(row.get("TEAM_ABBREVIATION", "")),
                "position": str(row.get("POSITION", "")),
                "height": str(row.get("HEIGHT", "")),
                "weight": str(row.get("WEIGHT", "")),
                "country": str(row.get("COUNTRY", "")),
                "from_year": str(row.get("FROM_YEAR", "")),
                "to_year": str(row.get("TO_YEAR", "")),
            }
            _CACHE.set(cache_key, info, ttl_seconds=604_800)  # 7-day cache for bio
            result[player_id] = info

        except Exception as exc:
            logger.warning("nba_api player info failed for %s: %s", player_id, exc)
            result[player_id] = {}

    return result


# ---------------------------------------------------------------------------
# Convenience: build a player_id → record lookup from a list of records
# ---------------------------------------------------------------------------

def index_by_player_id(records: list[dict]) -> dict[str, dict]:
    """Return a dict keyed by player_id for fast lookup."""
    return {r["player_id"]: r for r in records if r.get("player_id")}
