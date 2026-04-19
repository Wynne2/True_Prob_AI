"""
Splits Service.

Builds per-player split context from nba_api data.

This service is the PRIMARY source for:
  - Home / away splits        (nba_api LeagueDashPlayerStats with location filter)
  - Last N games splits       (nba_api LeagueDashPlayerStats with LastNGames filter)
  - Vs-opponent history       (nba_api PlayerGameLog, filtered post-fetch)

SportsDataIO may be used as a supplemental source for trend verification.

SOURCE: nba_api primary (splits), SportsDataIO supplement.
"""

from __future__ import annotations

import logging
import statistics
from datetime import date
from typing import Optional

from data.loaders.nba_api_loader import (
    fetch_recent_player_logs_batch,
    fetch_split_context_batch,
    index_by_player_id,
)
from domain.constants import NBA_SEASON
from domain.provider_models import SplitContext

logger = logging.getLogger(__name__)

_CURRENT_SEASON = NBA_SEASON

# Module-level split indexes (keyed by nba_api player_id)
_season_index: dict[str, dict] = {}
_home_index: dict[str, dict] = {}
_away_index: dict[str, dict] = {}
_last5_index: dict[str, dict] = {}
_last10_index: dict[str, dict] = {}
# Secondary name-based indexes for cross-source ID mismatch (SDIO vs nba_api IDs)
_season_name_index: dict[str, dict] = {}
_home_name_index: dict[str, dict] = {}
_away_name_index: dict[str, dict] = {}
_last5_name_index: dict[str, dict] = {}
_last10_name_index: dict[str, dict] = {}
_loaded_date: Optional[date] = None


def _normalize_name(name: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def _build_name_index(records: list[dict]) -> dict[str, dict]:
    return {_normalize_name(r.get("player_name", "")): r for r in records if r.get("player_name")}


def refresh(season: str = _CURRENT_SEASON, force: bool = False) -> None:
    """
    Fetch and index all split data for the current day.

    Makes 4 nba_api calls (season, home, away, last10) — each cached
    separately so reruns within the day skip the network.
    """
    global _season_index, _home_index, _away_index, _last5_index, _last10_index
    global _season_name_index, _home_name_index, _away_name_index
    global _last5_name_index, _last10_name_index, _loaded_date

    today = date.today()
    if not force and _loaded_date == today and _season_index:
        return

    date_str = today.isoformat()

    # Full season (SOURCE: nba_api primary)
    season_data = fetch_split_context_batch(season=season, date_str=date_str)
    _season_index = index_by_player_id(season_data)
    _season_name_index = _build_name_index(season_data)

    # Home splits (SOURCE: nba_api primary)
    home_data = fetch_split_context_batch(season=season, location="Home", date_str=date_str)
    _home_index = index_by_player_id(home_data)
    _home_name_index = _build_name_index(home_data)

    # Away splits (SOURCE: nba_api primary)
    away_data = fetch_split_context_batch(season=season, location="Road", date_str=date_str)
    _away_index = index_by_player_id(away_data)
    _away_name_index = _build_name_index(away_data)

    # Last 10 games (SOURCE: nba_api primary)
    last10_data = fetch_split_context_batch(season=season, last_n_games=10, date_str=date_str)
    _last10_index = index_by_player_id(last10_data)
    _last10_name_index = _build_name_index(last10_data)

    # Last 5 games (SOURCE: nba_api primary)
    last5_data = fetch_split_context_batch(season=season, last_n_games=5, date_str=date_str)
    _last5_index = index_by_player_id(last5_data)
    _last5_name_index = _build_name_index(last5_data)

    _loaded_date = today
    logger.info(
        "SplitsService: loaded splits for %d players (season), %d (home), %d (away), "
        "%d (last10), %d (last5)",
        len(_season_index), len(_home_index), len(_away_index),
        len(_last10_index), len(_last5_index),
    )


def _stat_key(prop_type: str) -> str:
    """Map prop type string to the raw data key."""
    return {
        "points": "pts",
        "rebounds": "reb",
        "assists": "ast",
        "threes": "fg3m",
        "pra": None,   # handled specially
        "blocks": "blk",
        "steals": "stl",
        "turnovers": "tov",
    }.get(prop_type, "pts")


def get_split_context(
    player_id: str,
    prop_type: str,
    opponent_team_id: Optional[str] = None,
    is_home: bool = True,
    player_name: str = "",
    season: str = _CURRENT_SEASON,
) -> SplitContext:
    """
    Return SplitContext for *player_id* and *prop_type*.

    nba_api IDs differ from SportsDataIO IDs.  When the ID lookup misses,
    we fall back to a normalized player-name lookup.
    """
    if not _season_index:
        refresh(season)

    stat = _stat_key(prop_type)
    norm_name = _normalize_name(player_name) if player_name else ""

    def _lookup(id_idx: dict, name_idx: dict) -> dict:
        rec = id_idx.get(player_id, {})
        if not rec and norm_name:
            rec = name_idx.get(norm_name, {})
        return rec

    def _get_stat(id_idx: dict, name_idx: dict) -> float:
        rec = _lookup(id_idx, name_idx)
        if prop_type == "pra":
            return (
                float(rec.get("pts", 0) or 0)
                + float(rec.get("reb", 0) or 0)
                + float(rec.get("ast", 0) or 0)
            )
        return float(rec.get(stat, 0) or 0) if stat else 0.0

    season_avg = _get_stat(_season_index, _season_name_index)
    home_avg = _get_stat(_home_index, _home_name_index)
    away_avg = _get_stat(_away_index, _away_name_index)
    last5_avg = _get_stat(_last5_index, _last5_name_index)
    last10_avg = _get_stat(_last10_index, _last10_name_index)

    home_games = int(_lookup(_home_index, _home_name_index).get("gp", 0) or 0)
    away_games = int(_lookup(_away_index, _away_name_index).get("gp", 0) or 0)
    season_games = int(_lookup(_season_index, _season_name_index).get("gp", 0) or 0)

    # Compute split factors (ratio to season avg; clamped to ±25%)
    def _factor(split_val: float, base: float) -> float:
        if base <= 0 or split_val <= 0:
            return 1.0
        return max(0.75, min(1.25, split_val / base))

    home_factor = _factor(home_avg, season_avg)
    away_factor = _factor(away_avg, season_avg)
    trend_factor = _factor(last5_avg, season_avg)

    # Vs-opponent: pull individual game logs and filter
    vs_opp_avg = 0.0
    vs_opp_games = 0
    if opponent_team_id:
        vs_opp_avg, vs_opp_games = _compute_vs_opp(
            player_id, opponent_team_id, prop_type, season
        )

    vs_opp_factor = _factor(vs_opp_avg, season_avg) if vs_opp_games >= 2 else 1.0

    resolved_name = _lookup(_season_index, _season_name_index).get("player_name", player_name)
    return SplitContext(
        player_id=player_id,
        player_name=resolved_name,
        prop_type=prop_type,
        season_avg=season_avg,
        season_games=season_games,
        last_5_avg=last5_avg,
        last_10_avg=last10_avg,
        last_10_std_dev=0.0,   # computed from game logs if available
        home_avg=home_avg,
        away_avg=away_avg,
        home_games=home_games,
        away_games=away_games,
        vs_opp_avg=vs_opp_avg,
        vs_opp_games=vs_opp_games,
        home_split_factor=home_factor,
        away_split_factor=away_factor,
        recent_trend_factor=trend_factor,
        vs_opp_factor=vs_opp_factor,
    )


def _compute_vs_opp(
    player_id: str,
    opponent_team_id: str,
    prop_type: str,
    season: str,
) -> tuple[float, int]:
    """
    Fetch player game logs and compute average vs specific opponent.

    Returns (avg, num_games).  Fetches all logs (cached) and filters by
    opponent matchup string.
    """
    try:
        logs_by_player = fetch_recent_player_logs_batch(
            [player_id], last_n=82, season=season
        )
        logs = logs_by_player.get(player_id, [])
    except Exception as exc:
        logger.warning("SplitsService vs-opp fetch failed for %s: %s", player_id, exc)
        return 0.0, 0

    stat = _stat_key(prop_type)
    # opponent team ID in game logs appears as part of the matchup string ("vs. OPP" or "@ OPP")
    relevant = [
        l for l in logs
        if opponent_team_id.lower() in (l.get("matchup", "") or "").lower()
        or str(l.get("opponent_team_id", "")) == str(opponent_team_id)
    ]

    if not relevant:
        return 0.0, 0

    vals: list[float] = []
    for l in relevant:
        if prop_type == "pra":
            vals.append(
                float(l.get("pts", 0) or 0)
                + float(l.get("reb", 0) or 0)
                + float(l.get("ast", 0) or 0)
            )
        else:
            vals.append(float(l.get(stat, 0) or 0) if stat else 0.0)

    return (sum(vals) / len(vals) if vals else 0.0), len(vals)


def enrich_split_context_with_logs(
    ctx: SplitContext,
    player_id: str,
    prop_type: str,
    season: str = _CURRENT_SEASON,
) -> SplitContext:
    """
    Populate last_5_games, last_10_games, last_10_minutes, and last_10_std_dev
    from per-game logs.

    Paired minutes lists enable per-36 efficiency calculations in the models,
    which separate genuine efficiency improvement from minutes-driven inflation.
    """
    try:
        logs_by_player = fetch_recent_player_logs_batch([player_id], last_n=10, season=season)
        logs = logs_by_player.get(player_id, [])
    except Exception as exc:
        logger.warning("enrich_split_context_with_logs failed: %s", exc)
        return ctx

    stat = _stat_key(prop_type)
    vals: list[float] = []
    minutes: list[float] = []
    for lg in logs:
        if prop_type == "pra":
            vals.append(
                float(lg.get("pts", 0) or 0)
                + float(lg.get("reb", 0) or 0)
                + float(lg.get("ast", 0) or 0)
            )
        else:
            vals.append(float(lg.get(stat or "pts", 0) or 0))
        minutes.append(float(lg.get("min", 0) or 0))

    ctx.last_10_games = vals
    ctx.last_5_games = vals[:5]
    ctx.last_10_minutes = minutes
    ctx.last_5_minutes = minutes[:5]
    if len(vals) >= 2:
        ctx.last_10_std_dev = statistics.stdev(vals)
    return ctx
