"""
SportsDataIO Loader  –  PRIMARY source for injuries, rosters, lineups, season
stats, game logs, depth charts, and team defensive stats.

All functions return raw normalised dicts and cache to disk under
data/cache/sportsdataio/.  The service layer converts these into
domain entities.

Source of truth (per architecture spec):
  - Injuries / availability      -> SportsDataIO primary
  - Depth charts / lineups       -> SportsDataIO primary
  - Season averages (box-score)  -> SportsDataIO primary
  - Game logs                    -> SportsDataIO primary
  - Team defensive stats         -> SportsDataIO primary
  - Schedules / slates           -> SportsDataIO (Sportradar fallback handled by registry)
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

import requests

from config import get_credentials, get_sdio_config
from services.cache_service import get_cache
from utils.date_utils import format_date, today_eastern

logger = logging.getLogger(__name__)

_CACHE = get_cache("sportsdataio", default_ttl=3_600)  # 1-hour default TTL
_CACHE_INJURY = get_cache("sportsdataio", default_ttl=900)  # 15 min for injuries

# Track which 401/403 endpoint prefixes we've already warned about so we don't
# flood the log with the same subscription-tier message for every game.
_warned_auth_prefixes: set[str] = set()


def _get_api_key() -> Optional[str]:
    return get_credentials().sportsdataio_key


def _get(endpoint: str, base_override: Optional[str] = None, **params) -> Any:
    """Make a single authenticated GET to the SportsDataIO NBA API."""
    api_key = _get_api_key()
    if not api_key:
        logger.warning("SportsDataIO: no API key configured, skipping request")
        return None

    cfg = get_sdio_config()
    base = base_override or cfg.base_url
    url = f"{base}/{endpoint}"
    try:
        resp = requests.get(
            url,
            headers={"Ocp-Apim-Subscription-Key": api_key},
            params=params,
            timeout=cfg.timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            # Warn once per endpoint family — these require a higher subscription tier
            prefix = "/".join(endpoint.split("/")[:2])
            if prefix not in _warned_auth_prefixes:
                _warned_auth_prefixes.add(prefix)
                logger.warning(
                    "SportsDataIO %s for %s — this endpoint requires a paid subscription "
                    "tier not covered by your current key. Data from this endpoint will be "
                    "skipped. Subsequent calls to the same endpoint group are suppressed.",
                    status, url,
                )
        elif status == 404:
            # Also warn once for 404s (endpoint doesn't exist in this tier/subscription)
            prefix = "/".join(endpoint.split("/")[:2])
            if prefix not in _warned_auth_prefixes:
                _warned_auth_prefixes.add(prefix)
                logger.warning(
                    "SportsDataIO 404 for %s — endpoint not found or not available in "
                    "your subscription. Skipping. Subsequent 404s for this group are suppressed.",
                    url,
                )
        else:
            logger.error("SportsDataIO HTTP %s for %s: %s", status, url, exc)
    except requests.RequestException as exc:
        logger.error("SportsDataIO request error for %s: %s", url, exc)
    return None


# ---------------------------------------------------------------------------
# Schedule / Games
# ---------------------------------------------------------------------------

def fetch_games_for_date(game_date: date) -> list[dict]:
    """
    Fetch all NBA games on *game_date*.

    SOURCE: SportsDataIO (slate/schedule)
    Returns list of game dicts: game_id, home_team, away_team, date, total, spread.
    """
    date_str = format_date(game_date)
    cache_key = f"games_{date_str}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    data = _get(f"scores/json/GamesByDate/{date_str}")
    if not data:
        return []

    games = []
    for raw in data:
        games.append({
            "game_id": str(raw.get("GameID", "")),
            "season": raw.get("Season", ""),
            "status": raw.get("Status", ""),
            "home_team_id": str(raw.get("HomeTeamID", "")),
            "home_team": raw.get("HomeTeam", ""),
            "away_team_id": str(raw.get("AwayTeamID", "")),
            "away_team": raw.get("AwayTeam", ""),
            "game_date": date_str,
            "date_time": raw.get("DateTime", ""),
            "over_under": float(raw.get("OverUnder", 0) or 0),
            "point_spread": float(raw.get("PointSpread", 0) or 0),
            "home_score": raw.get("HomeTeamScore"),
            "away_score": raw.get("AwayTeamScore"),
        })

    _CACHE.set(cache_key, games)
    logger.info("SportsDataIO: fetched %d games for %s", len(games), date_str)
    return games


# ---------------------------------------------------------------------------
# Player Season Stats (box-score averages)
# ---------------------------------------------------------------------------

def fetch_player_season_stats(season: str = "2026") -> list[dict]:
    """
    Fetch season-average box-score stats for all players.

    SOURCE: SportsDataIO (PRIMARY: season averages)
    Endpoint: /stats/json/PlayerSeasonStats/{season}
    """
    cache_key = f"player_season_stats_{season}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    data = _get(f"stats/json/PlayerSeasonStats/{season}")
    if not data:
        return []

    records = []
    for raw in data:
        # SportsDataIO PlayerSeason returns cumulative season totals (not per-game).
        # Divide all counting stats by Games to produce per-game averages.
        games = int(raw.get("Games", 0) or 0)
        gp = max(games, 1)  # guard against division by zero

        def _pg(field: str) -> float:
            return float(raw.get(field, 0) or 0) / gp

        records.append({
            "player_id": str(raw.get("PlayerID", "")),
            "player_name": raw.get("Name", ""),
            "team_id": str(raw.get("TeamID", "")),
            "team": raw.get("Team", ""),
            "position": raw.get("Position", ""),
            "started": int(raw.get("Started", 0) or 0),
            "games": games,
            # Per-game averages (totals ÷ games)
            "min": _pg("Minutes"),
            "pts": _pg("Points"),
            "reb": _pg("Rebounds"),
            "ast": _pg("Assists"),
            "stl": _pg("Steals"),
            "blk": _pg("BlockedShots"),
            "tov": _pg("Turnovers"),
            "fg3m": _pg("ThreePointersMade"),
            "fg3a": _pg("ThreePointersAttempted"),
            "fga": _pg("FieldGoalsAttempted"),
            "fgm": _pg("FieldGoalsMade"),
            "fta": _pg("FreeThrowsAttempted"),
            "ftm": _pg("FreeThrowsMade"),
            "fantasy_points": _pg("FantasyPointsDraftKings"),
            # Advanced (already a rate — do NOT divide)
            "usg_pct": float(raw.get("UsageRatePercentage", 0) or 0) / 100.0,
            "ts_pct": float(raw.get("TrueShootingPercentage", 0) or 0) / 100.0,
            "per": float(raw.get("PlayerEfficiencyRating", 0) or 0),
        })

    _CACHE.set(cache_key, records, ttl_seconds=21_600)  # 6-hour cache for season stats
    logger.info("SportsDataIO: fetched %d player season stats (per-game)", len(records))
    return records


# ---------------------------------------------------------------------------
# Player Game Logs
# ---------------------------------------------------------------------------

def fetch_player_game_logs(
    player_id: str,
    season: str = "2026",
    num_games: int = 10,
) -> list[dict]:
    """
    Fetch recent game logs for a single player, excluding DNP/inactive entries.

    SOURCE: SportsDataIO (PRIMARY: game logs / recent form)
    Endpoint: /stats/json/PlayerGameStatsBySeason/{season}/{playerid}/{numberofgames}
    The API accepts num_games as an integer or "all".
    DNP games (Minutes=0) are excluded so recent-form averages are based on
    games actually played.
    """
    cache_key = f"player_game_logs_{player_id}_{season}_{num_games}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    # Request more games than needed to account for DNPs being stripped
    fetch_count = min(num_games * 2, 30)
    data = _get(f"stats/json/PlayerGameStatsBySeason/{season}/{player_id}/{fetch_count}")
    if not data:
        return []

    logs = []
    for raw in sorted(data, key=lambda x: x.get("Day", ""), reverse=True):
        minutes = int(raw.get("Minutes", 0) or 0)
        if minutes == 0:
            continue  # Skip DNP / inactive games
        logs.append({
            "player_id": str(raw.get("PlayerID", "")),
            "game_id": str(raw.get("GameID", "")),
            "team": raw.get("Team", ""),
            "opponent": raw.get("Opponent", ""),
            "game_date": raw.get("Day", ""),
            "home_or_away": raw.get("HomeOrAway", ""),
            "min": float(minutes),
            "pts": float(raw.get("Points", 0) or 0),
            "reb": float(raw.get("Rebounds", 0) or 0),
            "ast": float(raw.get("Assists", 0) or 0),
            "stl": float(raw.get("Steals", 0) or 0),
            "blk": float(raw.get("BlockedShots", 0) or 0),
            "tov": float(raw.get("Turnovers", 0) or 0),
            "fg3m": float(raw.get("ThreePointersMade", 0) or 0),
            "fga": float(raw.get("FieldGoalsAttempted", 0) or 0),
            "fgm": float(raw.get("FieldGoalsMade", 0) or 0),
            "fantasy_points": float(raw.get("FantasyPointsDraftKings", 0) or 0),
        })
        if len(logs) >= num_games:
            break

    _CACHE.set(cache_key, logs, ttl_seconds=43_200)  # 12-hour for logs
    logger.info("SportsDataIO: fetched %d game logs (excl. DNPs) for player %s", len(logs), player_id)
    return logs


# ---------------------------------------------------------------------------
# Injuries (PRIMARY: SportsDataIO)
# ---------------------------------------------------------------------------

def fetch_injuries() -> list[dict]:
    """
    Fetch the current NBA injury report.

    SOURCE: SportsDataIO (PRIMARY: injury status)
    Endpoint: /projections/json/InjuredPlayers
    Returns Player[] — all injured players with InjuryStatus, InjuryBodyPart,
    InjuryStartDate, InjuryNotes.

    Falls back to inline injury fields from /scores/json/Players when the
    projections endpoint is unavailable (subscription tier limitation).
    """
    cache_key = "injuries_current"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    # Primary: dedicated injury endpoint (requires Projections tier)
    data = _get("projections/json/InjuredPlayers")
    if data:
        records = []
        for raw in data:
            inj_status = raw.get("InjuryStatus") or ""
            if inj_status.lower() == "scrambled":
                inj_status = ""
            records.append({
                "player_id": str(raw.get("PlayerID", "")),
                "player_name": f"{raw.get('FirstName', '')} {raw.get('LastName', '')}".strip(),
                "team_id": str(raw.get("TeamID", "")),
                "team": raw.get("Team", ""),
                "position": raw.get("Position", ""),
                "status": raw.get("Status", "Active"),
                "injury": inj_status,
                "injury_body_part": "" if (raw.get("InjuryBodyPart") or "").lower() == "scrambled" else (raw.get("InjuryBodyPart") or ""),
                "injury_notes": "" if (raw.get("InjuryNotes") or "").lower() == "scrambled" else (raw.get("InjuryNotes") or ""),
                "last_updated": raw.get("InjuryStartDate", ""),
            })
        _CACHE.set(cache_key, records, ttl_seconds=900)
        logger.info("SportsDataIO: fetched %d injury records", len(records))
        return records

    # Fallback: extract from player roster (free-tier alternative; fields may be scrambled)
    players = fetch_depth_charts()
    records = []
    for p in players:
        inj_status = p.get("injury_status", "")
        if not inj_status or inj_status.lower() in ("", "scrambled", "none"):
            continue
        records.append({
            "player_id": p.get("player_id", ""),
            "player_name": p.get("player_name", ""),
            "team_id": p.get("team_id", ""),
            "team": p.get("team", ""),
            "position": p.get("position", ""),
            "status": p.get("status", "Active"),
            "injury": inj_status,
            "injury_body_part": p.get("injury_body_part", ""),
            "injury_notes": p.get("injury_notes", ""),
            "last_updated": "",
        })

    _CACHE.set(cache_key, records, ttl_seconds=900)
    logger.info("SportsDataIO: derived %d injury records from player roster (fallback)", len(records))
    return records


# ---------------------------------------------------------------------------
# Starting Lineups (PRIMARY for game-day player lists)
# ---------------------------------------------------------------------------

def fetch_starting_lineups(game_date: date) -> list[dict]:
    """
    Fetch projected and confirmed starting lineups for *game_date*.

    SOURCE: SportsDataIO
    Endpoint: /projections/json/StartingLineupsByDate/{date}
    Returns StartingLineups[] — each entry covers one game with HomeLineup and
    AwayLineup arrays of player records.

    Returns a flat list of player dicts with team_id and game_id added.
    """
    date_str = format_date(game_date)
    cache_key = f"starting_lineups_{date_str}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    data = _get(f"projections/json/StartingLineupsByDate/{date_str}")
    if not data:
        return []

    records: list[dict] = []
    for game_entry in data:
        game_id = str(game_entry.get("GameID", ""))
        home_team_id = str(game_entry.get("HomeTeamID", ""))
        away_team_id = str(game_entry.get("AwayTeamID", ""))

        def _add_lineup(lineup: list, team_id: str) -> None:
            for p in (lineup or []):
                records.append({
                    "player_id": str(p.get("PlayerID", "")),
                    "player_name": p.get("Name", ""),
                    "team_id": team_id,
                    "team": p.get("Team", ""),
                    "position": p.get("Position", ""),
                    "game_id": game_id,
                    "is_starter": True,
                    "injury_status": p.get("InjuryStatus", ""),
                })

        _add_lineup(game_entry.get("HomeLineup", []), home_team_id)
        _add_lineup(game_entry.get("AwayLineup", []), away_team_id)

    _CACHE.set(cache_key, records, ttl_seconds=300)  # 5-min TTL — lineups change quickly
    logger.info("SportsDataIO: fetched %d lineup entries for %s", len(records), date_str)
    return records


# ---------------------------------------------------------------------------
# Projected Player Game Stats (full projections with minutes + stat lines)
# ---------------------------------------------------------------------------

def fetch_projected_lineups(game_date: date) -> list[dict]:
    """
    Fetch SportsDataIO's proprietary game-day player projections.

    SOURCE: SportsDataIO (Legacy Projections tier)
    Endpoint: /projections/json/PlayerGameProjectionStatsByDate/{date}
    Returns PlayerGameProjection[] — includes projected minutes, points, etc.

    Falls back to fetch_starting_lineups() if the projections tier is unavailable.
    """
    date_str = format_date(game_date)
    cache_key = f"projected_lineups_{date_str}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    data = _get(f"projections/json/PlayerGameProjectionStatsByDate/{date_str}")
    if not data:
        # Fallback: use starting lineups which don't require the projections tier
        return fetch_starting_lineups(game_date)

    records = []
    for raw in data:
        records.append({
            "player_id": str(raw.get("PlayerID", "")),
            "player_name": raw.get("Name", ""),
            "team_id": str(raw.get("TeamID", "")),
            "team": raw.get("Team", ""),
            "opponent": raw.get("Opponent", ""),
            "game_id": str(raw.get("GameID", "")),
            "position": raw.get("Position", ""),
            "started": bool(raw.get("Started", False)),
            "injury_status": raw.get("InjuryStatus", ""),
            "projected_min": float(raw.get("Minutes", 0) or 0),
            "projected_pts": float(raw.get("Points", 0) or 0),
            "projected_reb": float(raw.get("Rebounds", 0) or 0),
            "projected_ast": float(raw.get("Assists", 0) or 0),
            "projected_fantasy": float(raw.get("FantasyPointsDraftKings", 0) or 0),
        })

    _CACHE.set(cache_key, records, ttl_seconds=1_800)  # 30-min TTL for lineups
    logger.info("SportsDataIO: fetched %d projected players for %s", len(records), date_str)
    return records


# ---------------------------------------------------------------------------
# Depth Charts
# ---------------------------------------------------------------------------

def fetch_depth_chart_positions() -> dict[str, dict]:
    """
    Fetch structured depth chart position data for all teams.

    SOURCE: SportsDataIO
    Endpoint: /scores/json/DepthCharts
    Returns TeamDepthChart[] — each team has DepthChart entries with
    PlayerID, Position, DepthOrder.

    Returns dict: player_id -> {depth_position, depth_order, team_id}
    """
    cache_key = "depth_chart_positions"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    data = _get("scores/json/DepthCharts")
    if not data:
        return {}

    result: dict[str, dict] = {}
    for team_entry in data:
        team_id = str(team_entry.get("TeamID", ""))
        for slot in team_entry.get("DepthCharts", []) or []:
            pid = str(slot.get("PlayerID", ""))
            if pid:
                result[pid] = {
                    "team_id": team_id,
                    "depth_position": slot.get("Position", ""),
                    "depth_order": int(slot.get("DepthOrder", 99) or 99),
                }

    _CACHE.set(cache_key, result, ttl_seconds=3_600)
    logger.info("SportsDataIO: fetched depth chart positions for %d players", len(result))
    return result


def fetch_depth_charts() -> list[dict]:
    """
    Fetch all active NBA players with bio, team, position, depth and injury info.

    SOURCE: SportsDataIO
    Endpoint: /scores/json/Players  (Player Details - by Active)
    Returns Player[] — includes DepthChartPosition, DepthChartOrder, InjuryStatus.

    Depth chart position data from /scores/json/DepthCharts is blended in when
    DepthChartPosition is missing from a player record.
    """
    cache_key = "depth_charts_all"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    data = _get("scores/json/Players")
    if not data:
        return []

    # Optionally blend in structured depth chart positions
    depth_positions = fetch_depth_chart_positions()

    records = []
    for raw in data:
        pid = str(raw.get("PlayerID", ""))
        inj_status = raw.get("InjuryStatus") or ""
        if inj_status.lower() == "scrambled":
            inj_status = ""

        # Prefer explicit depth chart position; fall back to structured endpoint
        depth_pos = raw.get("DepthChartPosition", "") or ""
        depth_ord = int(raw.get("DepthChartOrder", 99) or 99)
        if not depth_pos and pid in depth_positions:
            depth_pos = depth_positions[pid].get("depth_position", "")
            depth_ord = depth_positions[pid].get("depth_order", 99)

        records.append({
            "player_id": pid,
            "player_name": f"{raw.get('FirstName', '')} {raw.get('LastName', '')}".strip(),
            "team_id": str(raw.get("TeamID", "")),
            "team": raw.get("Team", ""),
            "position": raw.get("Position", ""),
            "depth_position": depth_pos,
            "depth_order": depth_ord,
            "status": raw.get("Status", "Active"),
            "injury_status": inj_status,
            "injury_body_part": "" if (raw.get("InjuryBodyPart") or "").lower() == "scrambled" else (raw.get("InjuryBodyPart") or ""),
            "injury_notes": "" if (raw.get("InjuryNotes") or "").lower() == "scrambled" else (raw.get("InjuryNotes") or ""),
        })

    _CACHE.set(cache_key, records, ttl_seconds=3_600)
    logger.info("SportsDataIO: fetched %d player records with depth chart data", len(records))
    return records


# ---------------------------------------------------------------------------
# Team Season Stats (for DvP and matchup context)
# ---------------------------------------------------------------------------

def fetch_team_season_stats(season: str = "2026") -> list[dict]:
    """
    Fetch season-level team stats.

    SOURCE: SportsDataIO (PRIMARY: team defensive stats / matchup context)
    Endpoint: /scores/json/TeamSeasonStats/{season}
    """
    cache_key = f"team_season_stats_{season}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    data = _get(f"scores/json/TeamSeasonStats/{season}")
    if not data:
        return []

    records = []
    for raw in data:
        # TeamSeason returns cumulative season totals — divide by Games for per-game averages.
        games = int(raw.get("Games", 0) or 0)
        gp = max(games, 1)

        def _pg(field: str) -> float:
            return float(raw.get(field, 0) or 0) / gp

        # Opponent stats are nested under OpponentStat sub-object
        opp = raw.get("OpponentStat") or {}

        records.append({
            "team_id": str(raw.get("TeamID", "")),
            "team": raw.get("Team", ""),
            "season": raw.get("Season", ""),
            "games": games,
            "wins": int(raw.get("Wins", 0) or 0),
            "losses": int(raw.get("Losses", 0) or 0),
            # Per-game offensive averages
            "pts": _pg("Points"),
            "reb": _pg("Rebounds"),
            "ast": _pg("Assists"),
            "fga": _pg("FieldGoalsAttempted"),
            "fgm": _pg("FieldGoalsMade"),
            "fg3a": _pg("ThreePointersAttempted"),
            "fg3m": _pg("ThreePointersMade"),
            "stl": _pg("Steals"),
            "blk": _pg("BlockedShots"),
            "poss": _pg("Possessions"),
            # Per-game defensive averages (opponent stats sub-object)
            "opp_pts": float(opp.get("Points", 0) or 0) / gp,
            "opp_reb": float(opp.get("Rebounds", 0) or 0) / gp,
            "opp_ast": float(opp.get("Assists", 0) or 0) / gp,
            "opp_fg3m": float(opp.get("ThreePointersMade", 0) or 0) / gp,
            "opp_fgm": float(opp.get("FieldGoalsMade", 0) or 0) / gp,
            "opp_fga": float(opp.get("FieldGoalsAttempted", 0) or 0) / gp,
            "opp_stl": float(opp.get("Steals", 0) or 0) / gp,
            "opp_blk": float(opp.get("BlockedShots", 0) or 0) / gp,
        })

    _CACHE.set(cache_key, records, ttl_seconds=21_600)
    logger.info("SportsDataIO: fetched %d team season stat records", len(records))
    return records


# ---------------------------------------------------------------------------
# Team Stats Allowed by Position (DvP source)
# ---------------------------------------------------------------------------

def fetch_team_stats_allowed_by_position(season: str = "2026") -> list[dict]:
    """
    Fetch season totals of stats allowed by each team broken down by opponent position.

    SOURCE: SportsDataIO
    Endpoint: /stats/json/TeamStatsAllowedByPosition/{season}
    Returns TeamSeason[] where each entry represents one (team, opponent_position) pair.
    The OpponentPosition field identifies the position (PG, SG, SF, PF, C).

    Primary use: building DvP (Defense vs Position) tables for matchup analysis.
    """
    cache_key = f"team_dvp_{season}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    data = _get(f"stats/json/TeamStatsAllowedByPosition/{season}")
    if not data:
        return []

    records = []
    for raw in data:
        games = int(raw.get("Games", 0) or 0)
        gp = max(games, 1)

        def _pg(field: str) -> float:
            return float(raw.get(field, 0) or 0) / gp

        records.append({
            "team_id": str(raw.get("TeamID", "")),
            "team": raw.get("Team", ""),
            "opponent_position": raw.get("OpponentPosition", ""),
            "games": games,
            # Per-game stats allowed to this position
            "pts_allowed": _pg("Points"),
            "reb_allowed": _pg("Rebounds"),
            "ast_allowed": _pg("Assists"),
            "fg3m_allowed": _pg("ThreePointersMade"),
            "fg3a_allowed": _pg("ThreePointersAttempted"),
            "fgm_allowed": _pg("FieldGoalsMade"),
            "fga_allowed": _pg("FieldGoalsAttempted"),
            "stl_allowed": _pg("Steals"),
            "blk_allowed": _pg("BlockedShots"),
            "tov_allowed": _pg("Turnovers"),
            "min_allowed": _pg("Minutes"),
            "fantasy_allowed": _pg("FantasyPointsDraftKings"),
        })

    _CACHE.set(cache_key, records, ttl_seconds=21_600)
    logger.info(
        "SportsDataIO: fetched %d team-vs-position DvP records for season %s",
        len(records), season,
    )
    return records


# ---------------------------------------------------------------------------
# All active players (roster base)
# ---------------------------------------------------------------------------

def fetch_all_players() -> list[dict]:
    """
    Fetch all active NBA players with basic bio and team info.

    SOURCE: SportsDataIO (roster / bio)
    Endpoint: /scores/json/PlayersByActive
    """
    # Reuse depth chart data which includes player roster info
    return fetch_depth_charts()


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------

def index_by_player_id(records: list[dict]) -> dict[str, dict]:
    """Return a dict keyed by player_id for fast lookup."""
    return {r["player_id"]: r for r in records if r.get("player_id")}


def index_by_team_id(records: list[dict]) -> dict[str, list[dict]]:
    """Return a dict keyed by team_id mapping to list of records."""
    result: dict[str, list[dict]] = {}
    for r in records:
        tid = r.get("team_id", "")
        if tid:
            result.setdefault(tid, []).append(r)
    return result
