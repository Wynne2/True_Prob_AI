"""
Usage Tracking Service.

Fetches and indexes usage rate, touches, and possession context from nba_api.

This service is the PRIMARY source for:
  - Usage rate        (nba_api LeagueDashPlayerStats Advanced)
  - Touches           (nba_api LeagueDashPtStats Possessions)
  - Possessions/game  (nba_api LeagueDashPlayerStats Advanced)
  - Pace context      (nba_api LeagueDashTeamStats Advanced)
  - Passing context   (nba_api LeagueDashPtStats Passing)
  - Rebound chances   (nba_api LeagueDashPtStats Rebounding)

All data is fetched in league-wide batches (not per-player) to minimise
request volume, then indexed for fast per-player lookup.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from data.loaders.nba_api_loader import (
    fetch_today_player_tracking_batch,
    fetch_usage_dashboard_batch,
    fetch_team_pace_batch,
    index_by_player_id,
)
from domain.constants import NBA_SEASON
from domain.provider_models import UsageTrackingContext

logger = logging.getLogger(__name__)

_CURRENT_SEASON = NBA_SEASON

# Module-level indexes (populated once per day)
_usage_index: dict[str, dict] = {}          # nba_api player_id -> advanced stats
_usage_name_index: dict[str, dict] = {}     # normalized name -> advanced stats (fallback)
_poss_tracking_index: dict[str, dict] = {}  # nba_api player_id -> possessions tracking
_poss_name_index: dict[str, dict] = {}      # normalized name -> possessions tracking
_passing_tracking_index: dict[str, dict] = {}
_passing_name_index: dict[str, dict] = {}
_rebound_tracking_index: dict[str, dict] = {}
_rebound_name_index: dict[str, dict] = {}
_team_pace_index: dict[str, dict] = {}      # team_id -> pace stats
_loaded_date: Optional[date] = None


def _normalize_name(name: str) -> str:
    """Lowercase, strip accents/punctuation for fuzzy name matching."""
    import unicodedata
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def _build_name_index(records: list[dict]) -> dict[str, dict]:
    return {_normalize_name(r.get("player_name", "")): r for r in records if r.get("player_name")}


def refresh(season: str = _CURRENT_SEASON, force: bool = False) -> None:
    """
    Fetch and index all usage/tracking data for the current day.

    Safe to call multiple times — data is served from disk cache after
    the first pull.  Set force=True to bypass cache entirely.
    """
    global _usage_index, _usage_name_index
    global _poss_tracking_index, _poss_name_index
    global _passing_tracking_index, _passing_name_index
    global _rebound_tracking_index, _rebound_name_index
    global _team_pace_index, _loaded_date

    today = date.today()
    if not force and _loaded_date == today and _usage_index:
        logger.debug("UsageTrackingService: already loaded for %s", today)
        return

    date_str = today.isoformat()

    # --- Usage rate and advanced context (SOURCE: nba_api PRIMARY) ---
    logger.info("UsageTrackingService: fetching advanced dashboard (season=%s)", season)
    advanced = fetch_usage_dashboard_batch(season=season, date_str=date_str)
    _usage_index = index_by_player_id(advanced)
    _usage_name_index = _build_name_index(advanced)

    # --- Possessions tracking (SOURCE: nba_api PRIMARY: touches) ---
    logger.info("UsageTrackingService: fetching possessions tracking")
    poss = fetch_today_player_tracking_batch(
        season=season, pt_measure_type="Possessions", date_str=date_str
    )
    _poss_tracking_index = index_by_player_id(poss)
    _poss_name_index = _build_name_index(poss)

    # --- Passing context (SOURCE: nba_api) ---
    logger.info("UsageTrackingService: fetching passing tracking")
    passing = fetch_today_player_tracking_batch(
        season=season, pt_measure_type="Passing", date_str=date_str
    )
    _passing_tracking_index = index_by_player_id(passing)
    _passing_name_index = _build_name_index(passing)

    # --- Rebounding context (SOURCE: nba_api) ---
    logger.info("UsageTrackingService: fetching rebounding tracking")
    rebounding = fetch_today_player_tracking_batch(
        season=season, pt_measure_type="Rebounding", date_str=date_str
    )
    _rebound_tracking_index = index_by_player_id(rebounding)
    _rebound_name_index = _build_name_index(rebounding)

    # --- Team pace (SOURCE: nba_api PRIMARY) ---
    logger.info("UsageTrackingService: fetching team pace dashboard")
    team_pace = fetch_team_pace_batch(season=season, date_str=date_str)
    _team_pace_index = {r["team_id"]: r for r in team_pace if r.get("team_id")}

    _loaded_date = today
    logger.info(
        "UsageTrackingService: loaded %d players (advanced), %d (possessions), %d teams",
        len(_usage_index), len(_poss_tracking_index), len(_team_pace_index),
    )


def get_usage_context(
    player_id: str,
    team_id: str,
    player_name: str = "",
    season: str = _CURRENT_SEASON,
) -> UsageTrackingContext:
    """
    Return UsageTrackingContext for *player_id*.

    nba_api uses NBA.com player IDs which differ from SportsDataIO IDs.
    When the ID lookup misses (always, for SDIO-sourced player_ids), we fall
    back to a normalized player-name lookup so usage data is never lost.

    Auto-refreshes if not yet loaded for today.
    """
    if not _usage_index:
        refresh(season)

    norm_name = _normalize_name(player_name) if player_name else ""

    def _lookup(id_idx: dict, name_idx: dict) -> dict:
        rec = id_idx.get(player_id, {})
        if not rec and norm_name:
            rec = name_idx.get(norm_name, {})
        return rec

    adv = _lookup(_usage_index, _usage_name_index)
    poss = _lookup(_poss_tracking_index, _poss_name_index)
    passing = _lookup(_passing_tracking_index, _passing_name_index)
    rebounding = _lookup(_rebound_tracking_index, _rebound_name_index)
    team_pace = _team_pace_index.get(team_id, {})

    # nba_api returns USG_PCT as a 0-1 fraction (e.g. 0.213 = 21.3%).
    # Do NOT divide by 100 — the value is already in the domain's 0-1 contract.
    raw_usg = float(adv.get("usg_pct", 0) or 0)

    return UsageTrackingContext(
        player_id=player_id,
        player_name=adv.get("player_name", player_name),
        team_id=team_id,
        usage_rate=raw_usg,
        possessions_per_game=float(adv.get("poss", 0) or 0),
        team_pace=float(team_pace.get("pace", 0) or 0),
        touches_per_game=float(poss.get("touches", 0) or 0),
        time_of_possession=float(poss.get("time_of_poss", 0) or 0),
        front_ct_touches=float(poss.get("front_ct_touches", 0) or 0),
        paint_touches=float(poss.get("paint_touches", 0) or 0),
        elbow_touches=float(poss.get("elbow_touches", 0) or 0),
        post_touches=float(poss.get("post_touches", 0) or 0),
        potential_assists=float(passing.get("potential_ast", 0) or 0),
        rebound_chances=float(rebounding.get("reb_chances", 0) or 0),
        oreb_chances=float(rebounding.get("oreb_chances", 0) or 0),
        dreb_chances=float(rebounding.get("dreb_chances", 0) or 0),
    )


def get_team_pace(team_id: str) -> float:
    """Return pace (possessions per 48 min) for *team_id*."""
    if not _team_pace_index:
        refresh()
    return float(_team_pace_index.get(team_id, {}).get("pace", 0) or 0)


def get_all_usage() -> dict[str, dict]:
    """Return the full player_id → advanced stats index."""
    return _usage_index
