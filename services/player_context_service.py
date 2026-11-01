"""
Player Context Service.

Orchestrates all service-layer outputs to produce a fully populated
Player domain entity (for backward compatibility with the existing
stat model layer).

Data flow:
  1. Season averages          → SportsDataIO primary
  2. Recent form (last5/10)   → nba_api splits + SportsDataIO game logs
  3. Usage / tracking         → nba_api primary (UsageTrackingService)
  4. Injury / role            → SportsDataIO primary (InjuryContextService)
  5. Home / away splits       → nba_api primary (SplitsService)

The service blends SportsDataIO box-score averages with nba_api advanced
context so that neither source is required alone to populate a Player.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from data.loaders.nba_api_loader import fetch_split_context_batch, index_by_player_id
from domain.entities import Player
from domain.enums import DataSource, InjuryStatus, PlayerRole, Position
from services import injury_context_service, usage_tracking_service, splits_service

logger = logging.getLogger(__name__)

from domain.constants import NBA_SEASON
_CURRENT_SEASON_NBA = NBA_SEASON

# Module-level indexes
_season_stats_index: dict[str, dict] = {}
_depth_index: dict[str, dict] = {}
_loaded_date: Optional[date] = None

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
}

_STATUS_MAP: dict[str, InjuryStatus] = {
    "active": InjuryStatus.ACTIVE,
    "questionable": InjuryStatus.QUESTIONABLE,
    "doubtful": InjuryStatus.DOUBTFUL,
    "out": InjuryStatus.OUT,
    "day_to_day": InjuryStatus.DAY_TO_DAY,
    "day-to-day": InjuryStatus.DAY_TO_DAY,
    "suspended": InjuryStatus.SUSPENDED,
    "not_with_team": InjuryStatus.NOT_WITH_TEAM,
}


def refresh(game_date: Optional[date] = None, force: bool = False) -> None:
    """Load or refresh all player context data for *game_date*."""
    global _season_stats_index, _depth_index, _loaded_date

    today = game_date or date.today()
    if not force and _loaded_date == today and _season_stats_index:
        return

    season_raw = fetch_split_context_batch(season=_CURRENT_SEASON_NBA)
    _season_stats_index = index_by_player_id(season_raw)
    _depth_index = {
        pid: {"player_id": pid, "position": rec.get("position") or "", "team_id": rec.get("team_id")}
        for pid, rec in _season_stats_index.items()
    }

    # Warm up sub-services (they have their own caching)
    injury_context_service.refresh(today)
    usage_tracking_service.refresh(season=_CURRENT_SEASON_NBA)
    splits_service.refresh(season=_CURRENT_SEASON_NBA)

    _loaded_date = today
    logger.info(
        "PlayerContextService: loaded %d season-stat records, %d depth-chart records",
        len(_season_stats_index), len(_depth_index),
    )


def get_player(
    player_id: str,
    team_id: str,
    game_date: Optional[date] = None,
) -> Optional[Player]:
    """
    Build and return a fully populated Player domain entity for *player_id*.

    Returns None if no season-stat data is available for this player.
    """
    if not _season_stats_index:
        refresh(game_date)

    sdio = _season_stats_index.get(player_id, {})
    depth = _depth_index.get(player_id, {})

    if not sdio and not depth:
        logger.debug("PlayerContextService: no data for player_id=%s", player_id)
        return None

    # --- Identity ---
    raw_pos = str(depth.get("position") or sdio.get("position") or "G").upper().strip()
    raw_pos = raw_pos.replace("-", "").replace("/", "")
    position = _POSITION_MAP.get(raw_pos, Position.G)

    # --- Injury / role context (SOURCE: SportsDataIO primary) ---
    inj_ctx = injury_context_service.get_injury_context(
        player_id, team_id, game_date
    )
    injury_status = _STATUS_MAP.get(inj_ctx.status, InjuryStatus.ACTIVE)

    mpg = float(sdio.get("min", 0) or 0)
    # BALLDONTLIE does not publish projected lineups. A player at 28+ minutes
    # per game is treated as a starter so the minutes cap is not the bench cap.
    # That label is inferred, not a confirmed starting assignment.
    is_starter = bool(inj_ctx.is_starter)
    if not injury_context_service._lineup_index and mpg >= 28:
        is_starter = True
    role = PlayerRole.STARTER if is_starter else PlayerRole.BENCH
    if not is_starter and 0 < mpg < 12:
        role = PlayerRole.RESERVE
    if injury_status in (InjuryStatus.OUT, InjuryStatus.SUSPENDED):
        role = PlayerRole.OUT

    # Season averages from BALLDONTLIE general/base.
    ppg = float(sdio.get("pts", 0) or 0)
    rpg = float(sdio.get("reb", 0) or 0)
    apg = float(sdio.get("ast", 0) or 0)
    tpg = float(sdio.get("fg3m", 0) or 0)
    bpg = float(sdio.get("blk", 0) or 0)
    spg = float(sdio.get("stl", 0) or 0)
    tovpg = float(sdio.get("tov", 0) or 0)
    fga = float(sdio.get("fga", 0) or 0)
    fta = float(sdio.get("fta", 0) or 0)
    fg3a = float(sdio.get("fg3a", 0) or 0)
    fg3pct = (tpg / fg3a) if fg3a > 0 else 0.0

    # --- Identity / name for cross-source lookups ---
    player_name = str(sdio.get("player_name") or depth.get("player_name") or "")
    norm_name = splits_service._normalize_name(player_name) if player_name else ""

    # --- Usage / tracking (SOURCE: nba_api primary, name-fallback for ID mismatch) ---
    usage_ctx = usage_tracking_service.get_usage_context(
        player_id, team_id, player_name=player_name
    )

    # --- Splits (SOURCE: nba_api primary) ---
    # We pre-warm splits so they are available; per-prop SplitContext is built
    # in the feature builder, not here.
    splits_svc = splits_service

    def _adv_lookup(id_idx: dict, name_idx: dict) -> dict:
        rec = id_idx.get(player_id, {})
        if not rec and norm_name:
            rec = name_idx.get(norm_name, {})
        return rec

    adv_season = _adv_lookup(
        usage_tracking_service._usage_index,
        usage_tracking_service._usage_name_index,
    )

    # Home / away averages from splits index (name fallback)
    home_ppg = float(_adv_lookup(splits_svc._home_index, splits_svc._home_name_index).get("pts", 0) or 0)
    away_ppg = float(_adv_lookup(splits_svc._away_index, splits_svc._away_name_index).get("pts", 0) or 0)

    # Recent form from last-5 and last-10 split indexes (name fallback)
    def _split_stat(id_idx: dict, name_idx: dict, key: str) -> list[float]:
        rec = _adv_lookup(id_idx, name_idx)
        if not rec:
            return []
        # Split dashboards return averages not game arrays; return a single-element
        # list as a sentinel; the feature builder will enrich with log-level data.
        raw = rec.get(key)
        if raw is None:
            return []
        val = float(raw or 0)
        # Use explicit None check (not falsy) so a real 0.0 average is preserved.
        return [val]

    last5_pts  = _split_stat(splits_svc._last5_index, splits_svc._last5_name_index, "pts")
    last5_reb  = _split_stat(splits_svc._last5_index, splits_svc._last5_name_index, "reb")
    last5_ast  = _split_stat(splits_svc._last5_index, splits_svc._last5_name_index, "ast")
    last5_min  = _split_stat(splits_svc._last5_index, splits_svc._last5_name_index, "min")
    last5_fg3  = _split_stat(splits_svc._last5_index, splits_svc._last5_name_index, "fg3m")
    last5_blk  = _split_stat(splits_svc._last5_index, splits_svc._last5_name_index, "blk")
    last5_stl  = _split_stat(splits_svc._last5_index, splits_svc._last5_name_index, "stl")
    last5_tov  = _split_stat(splits_svc._last5_index, splits_svc._last5_name_index, "tov")

    last10_pts = _split_stat(splits_svc._last10_index, splits_svc._last10_name_index, "pts")
    last10_reb = _split_stat(splits_svc._last10_index, splits_svc._last10_name_index, "reb")
    last10_ast = _split_stat(splits_svc._last10_index, splits_svc._last10_name_index, "ast")
    last10_min = _split_stat(splits_svc._last10_index, splits_svc._last10_name_index, "min")
    last10_fg3 = _split_stat(splits_svc._last10_index, splits_svc._last10_name_index, "fg3m")
    last10_blk = _split_stat(splits_svc._last10_index, splits_svc._last10_name_index, "blk")
    last10_stl = _split_stat(splits_svc._last10_index, splits_svc._last10_name_index, "stl")
    last10_tov = _split_stat(splits_svc._last10_index, splits_svc._last10_name_index, "tov")

    player = Player(
        player_id=player_id,
        name=sdio.get("player_name") or depth.get("player_name", f"Player {player_id}"),
        team_id=team_id,
        team_abbr=sdio.get("team") or depth.get("team", ""),
        position=position,
        role=role,
        injury_status=injury_status,

        # Season averages (SOURCE: SportsDataIO primary)
        minutes_per_game=mpg,
        points_per_game=ppg,
        rebounds_per_game=rpg,
        assists_per_game=apg,
        threes_per_game=tpg,
        blocks_per_game=bpg,
        steals_per_game=spg,
        turnovers_per_game=tovpg,

        # Advanced / tracking (SOURCE: nba_api primary)
        usage_rate=usage_ctx.usage_rate,
        field_goal_attempts=fga,
        free_throw_attempts=fta,
        three_point_attempts=fg3a,
        three_point_pct=fg3pct,
        touches=usage_ctx.touches_per_game,
        time_of_possession=usage_ctx.time_of_possession,
        rebound_chances=usage_ctx.rebound_chances,
        potential_assists=usage_ctx.potential_assists,

        # Contextual splits (SOURCE: nba_api primary)
        home_ppg=home_ppg,
        away_ppg=away_ppg,
        is_starter=is_starter,
        minutes_vacuum=inj_ctx.minutes_vacuum,
        usage_vacuum=float(inj_ctx.teammate_usage_vacuum or 1.0),

        # Recent form (SOURCE: nba_api splits — single-element window averages;
        # enriched to per-game arrays by player_feature_builder via game logs)
        last5_points=last5_pts,
        last5_rebounds=last5_reb,
        last5_assists=last5_ast,
        last5_minutes=last5_min,
        last5_threes=last5_fg3,
        last5_blocks=last5_blk,
        last5_steals=last5_stl,
        last5_turnovers=last5_tov,
        last10_points=last10_pts,
        last10_rebounds=last10_reb,
        last10_assists=last10_ast,
        last10_minutes=last10_min,
        last10_threes=last10_fg3,
        last10_blocks=last10_blk,
        last10_steals=last10_stl,
        last10_turnovers=last10_tov,

        data_source=DataSource.BALLDONTLIE,
    )

    return player


def get_players_for_game(
    game_id: str,
    home_team_id: str,
    away_team_id: str,
    game_date: Optional[date] = None,
) -> list[Player]:
    """
    Return Player objects for all players on both rosters for *game_id*.

    Players are the BALLDONTLIE season-average rows for the two team ids.
    """
    if not _season_stats_index:
        refresh(game_date)

    team_ids = {str(home_team_id), str(away_team_id)}
    game_players = [
        r for r in _season_stats_index.values()
        if str(r.get("team_id", "")) in team_ids
        or str(r.get("team_abbr", "")) in team_ids
    ]
    if not game_players:
        logger.info(
            "PlayerContextService: no BALLDONTLIE season rows for teams %s (game %s)",
            team_ids, game_id,
        )

    players: list[Player] = []
    seen: set[str] = set()
    for rec in game_players:
        pid = str(rec.get("player_id", ""))
        if not pid or pid in seen:
            continue
        seen.add(pid)
        tid = str(rec.get("team_id", ""))
        player = get_player(pid, tid, game_date)
        if player and player.injury_status != InjuryStatus.OUT:
            players.append(player)

    return players
