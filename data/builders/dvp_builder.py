"""
DvP Builder  –  Defense vs Position computation.

Builds DvP tables from raw player game logs sourced from nba_api and
SportsDataIO.  The DvP data is entirely derived internally — no external
DvP provider is used.

DvP construction logic (per architecture spec):
-------------------------------------------------
key = (defense_team_id, player_position)

For each entry accumulate:
  pts, reb, ast, stl, blk, tov

Fantasy points formula:
  fantasy = pts + 1.2*reb + 1.5*ast + 3*stl + 3*blk - tov

Aggregate windows:
  - season (all games)
  - last 10 games
  - last 5 games

Normalisation:
  normalized_dvp_stat = team_allowed_stat / league_avg_for_position_stat
  > 1.0  →  weaker defense vs that position/stat
  < 1.0  →  stronger defense vs that position/stat

Position sourcing priority:
  1. SportsDataIO depth chart / roster position
  2. nba_api player metadata
  3. Internal bucket map: G→PG/SG, F→SF/PF, C→C

SOURCE: computed internally from nba_api + SportsDataIO game logs.
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Optional

from domain.provider_models import DvPEntry
from services.cache_service import get_cache

logger = logging.getLogger(__name__)

_CACHE = get_cache("derived", default_ttl=86_400)

# DraftKings fantasy scoring weights (per architecture spec)
_FANTASY_WEIGHTS = {
    "pts": 1.0,
    "reb": 1.2,
    "ast": 1.5,
    "stl": 3.0,
    "blk": 3.0,
    "tov": -1.0,
}

# Standard positions for DvP bucketing
_STANDARD_POSITIONS = {"PG", "SG", "SF", "PF", "C"}

# Fallback position bucket map
_POSITION_BUCKET: dict[str, str] = {
    "G": "PG",
    "GF": "SG",
    "F": "SF",
    "FC": "PF",
    "C": "C",
    "PG": "PG",
    "SG": "SG",
    "SF": "SF",
    "PF": "PF",
}

# League average per-game stats allowed per position (2024-25 estimates)
# Used to compute normalised DvP factors.
_LEAGUE_AVG_BY_POSITION: dict[str, dict[str, float]] = {
    "PG": {"pts": 20.5, "reb": 3.5, "ast": 6.8, "fantasy": 37.5},
    "SG": {"pts": 19.0, "reb": 4.0, "ast": 3.8, "fantasy": 32.5},
    "SF": {"pts": 17.5, "reb": 5.5, "ast": 3.0, "fantasy": 31.5},
    "PF": {"pts": 16.5, "reb": 7.0, "ast": 2.5, "fantasy": 32.0},
    "C":  {"pts": 15.0, "reb": 9.5, "ast": 2.0, "fantasy": 36.0},
}


def _normalise_position(raw_position: str) -> str:
    """Map any provider position string to a standard 5-category position."""
    pos = raw_position.strip().upper()
    if pos in _STANDARD_POSITIONS:
        return pos
    return _POSITION_BUCKET.get(pos, "SF")  # default to SF if truly unknown


def _fantasy_pts(log: dict) -> float:
    """Compute fantasy points from a game log dict using DraftKings formula."""
    return (
        log.get("pts", 0) * _FANTASY_WEIGHTS["pts"]
        + log.get("reb", 0) * _FANTASY_WEIGHTS["reb"]
        + log.get("ast", 0) * _FANTASY_WEIGHTS["ast"]
        + log.get("stl", 0) * _FANTASY_WEIGHTS["stl"]
        + log.get("blk", 0) * _FANTASY_WEIGHTS["blk"]
        + log.get("tov", 0) * _FANTASY_WEIGHTS["tov"]
    )


def build_dvp_tables(
    player_game_logs: list[dict],
    position_map: dict[str, str],
) -> dict[str, dict[str, DvPEntry]]:
    """
    Build the full DvP table from raw player game logs.

    Parameters
    ----------
    player_game_logs : list[dict]
        Each dict must contain at minimum:
          player_id, opponent_team_id (the defending team),
          pts, reb, ast, stl, blk, tov
        Sorted from newest to oldest (for windowed calculations).
    position_map : dict[str, str]
        player_id -> normalised position string (PG/SG/SF/PF/C).
        Built from SportsDataIO depth chart (primary) + nba_api metadata.

    Returns
    -------
    dict
        { defense_team_id: { position: DvPEntry } }
    """
    # Accumulate logs per (defense_team_id, position)
    # Structure: key -> list of game log dicts (newest first)
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for log in player_game_logs:
        player_id = str(log.get("player_id", ""))
        defense_team_id = str(log.get("opponent_team_id", "") or log.get("opponent", ""))
        if not defense_team_id or not player_id:
            continue

        raw_pos = position_map.get(player_id, "SF")
        position = _normalise_position(raw_pos)
        buckets[(defense_team_id, position)].append(log)

    # Build DvPEntry for each (team, position) combination
    result: dict[str, dict[str, DvPEntry]] = defaultdict(dict)

    for (defense_team_id, position), logs in buckets.items():
        if not logs:
            continue

        def _avg(key: str, window: Optional[int] = None) -> float:
            subset = logs[:window] if window else logs
            vals = [float(l.get(key, 0) or 0) for l in subset]
            return sum(vals) / len(vals) if vals else 0.0

        def _fp_avg(window: Optional[int] = None) -> float:
            subset = logs[:window] if window else logs
            vals = [_fantasy_pts(l) for l in subset]
            return sum(vals) / len(vals) if vals else 0.0

        league_avg = _LEAGUE_AVG_BY_POSITION.get(position, _LEAGUE_AVG_BY_POSITION["SF"])

        pts_season = _avg("pts")
        reb_season = _avg("reb")
        ast_season = _avg("ast")
        fp_season = _fp_avg()

        entry = DvPEntry(
            defense_team_id=defense_team_id,
            position=position,
            pts_allowed=pts_season,
            reb_allowed=reb_season,
            ast_allowed=ast_season,
            stl_forced=_avg("stl"),
            blk_forced=_avg("blk"),
            tov_forced=_avg("tov"),
            fantasy_allowed=fp_season,
            last_10_pts=_avg("pts", 10),
            last_10_reb=_avg("reb", 10),
            last_10_ast=_avg("ast", 10),
            last_10_fantasy=_fp_avg(10),
            last_5_pts=_avg("pts", 5),
            last_5_reb=_avg("reb", 5),
            last_5_ast=_avg("ast", 5),
            last_5_fantasy=_fp_avg(5),
            # Normalised factors (>1.0 = weaker defence vs this position/stat)
            norm_pts=pts_season / league_avg["pts"] if league_avg["pts"] else 1.0,
            norm_reb=reb_season / league_avg["reb"] if league_avg["reb"] else 1.0,
            norm_ast=ast_season / league_avg["ast"] if league_avg["ast"] else 1.0,
            norm_fantasy=fp_season / league_avg["fantasy"] if league_avg["fantasy"] else 1.0,
            games_sample=len(logs),
        )
        result[defense_team_id][position] = entry

    return dict(result)


def build_and_cache_dvp(
    player_game_logs: list[dict],
    position_map: dict[str, str],
    cache_date: Optional[date] = None,
) -> dict[str, dict[str, DvPEntry]]:
    """
    Build DvP tables and cache the result to disk.

    Returns the DvP table (always, even on cache hit) and updates the cache
    if the data is fresh.
    """
    ds = (cache_date or date.today()).isoformat()
    cache_key = f"dvp_{ds}"

    cached_raw = _CACHE.get(cache_key)
    if cached_raw is not None:
        logger.debug("DvP cache hit for %s", ds)
        return _deserialise_dvp(cached_raw)

    logger.info("Building DvP tables from %d game log records", len(player_game_logs))
    dvp = build_dvp_tables(player_game_logs, position_map)

    # Serialise for disk cache
    _CACHE.set(cache_key, _serialise_dvp(dvp))
    logger.info(
        "DvP tables built: %d teams, %d (team, position) entries",
        len(dvp),
        sum(len(v) for v in dvp.values()),
    )
    return dvp


# ---------------------------------------------------------------------------
# Serialisation helpers (DvPEntry <-> JSON-safe dict)
# ---------------------------------------------------------------------------

def _serialise_dvp(dvp: dict[str, dict[str, DvPEntry]]) -> dict:
    out: dict = {}
    for team_id, pos_map in dvp.items():
        out[team_id] = {}
        for pos, entry in pos_map.items():
            out[team_id][pos] = entry.__dict__
    return out


def _deserialise_dvp(raw: dict) -> dict[str, dict[str, DvPEntry]]:
    result: dict[str, dict[str, DvPEntry]] = {}
    for team_id, pos_map in raw.items():
        result[team_id] = {}
        for pos, d in pos_map.items():
            result[team_id][pos] = DvPEntry(**d)
    return result


# ---------------------------------------------------------------------------
# League-average reference accessors (for normalisation outside this module)
# ---------------------------------------------------------------------------

def get_league_avg_for_position(position: str) -> dict[str, float]:
    """Return league-average allowed stats for *position*."""
    return _LEAGUE_AVG_BY_POSITION.get(
        _normalise_position(position),
        _LEAGUE_AVG_BY_POSITION["SF"],
    )
