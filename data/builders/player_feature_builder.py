"""
Player Feature Builder.

Assembles a FeatureVector for each (player, prop_type) combination from
all service layer outputs.  This is the single assembly point — the
engine consumes FeatureVectors, not raw provider records.

Source assignment per field is documented inline.
"""

from __future__ import annotations

import logging
import statistics
from datetime import date
from typing import Optional

from domain.entities import Player
from domain.feature_vector import FeatureVector
from domain.provider_models import DvPEntry, MatchupContext
from services import (
    dvp_service,
    injury_context_service,
    matchup_context_service,
    splits_service,
    usage_tracking_service,
)

logger = logging.getLogger(__name__)

_LEAGUE_AVG_PACE = 100.3


def build_feature_vector(
    player: Player,
    opponent_team_id: str,
    opponent_team_abbr: str,
    prop_type: str,
    is_home: bool,
    game_id: str = "",
    game_total: float = 0.0,
    point_spread: float = 0.0,
    market_line: float = 0.0,
    over_odds: int = -110,
    under_odds: int = -110,
    game_date: Optional[date] = None,
) -> FeatureVector:
    """
    Build a complete FeatureVector for *player* / *prop_type* combination.

    Gathers:
      - Season averages and role  (from the Player object, populated by PlayerContextService)
      - Usage / tracking          (UsageTrackingService → nba_api primary)
      - Splits                    (SplitsService → nba_api primary)
      - Injury / vacancy          (InjuryContextService → SportsDataIO primary)
      - Matchup context           (MatchupContextService → SportsDataIO + nba_api)
      - DvP factors               (DvPService → derived from nba_api + SportsDataIO)
      - Market data               (passed in from Odds API, not fetched here)
    """
    fv = FeatureVector(
        player_id=player.player_id,
        player_name=player.name,
        team_id=player.team_id,
        team_abbr=player.team_abbr,
        opponent_team_id=opponent_team_id,
        opponent_team_abbr=opponent_team_abbr,
        position=player.position.value,
        game_id=game_id,
        prop_type=prop_type,
        market_line=market_line,
        over_odds=over_odds,
        under_odds=under_odds,
    )

    # ------------------------------------------------------------------
    # BASE PRODUCTION  (SOURCE: SportsDataIO primary via Player entity)
    # ------------------------------------------------------------------
    _fill_base_production(fv, player, prop_type)

    # ------------------------------------------------------------------
    # USAGE / TRACKING  (SOURCE: nba_api primary)
    # ------------------------------------------------------------------
    usage_ctx = usage_tracking_service.get_usage_context(player.player_id, player.team_id)
    fv.usage_rate = usage_ctx.usage_rate or player.usage_rate
    fv.touches_per_game = usage_ctx.touches_per_game or player.touches
    fv.time_of_possession = usage_ctx.time_of_possession or player.time_of_possession
    fv.possessions_per_game = usage_ctx.possessions_per_game
    fv.potential_assists = usage_ctx.potential_assists or player.potential_assists
    fv.rebound_chances = usage_ctx.rebound_chances or player.rebound_chances

    # Team pace (SOURCE: nba_api primary)
    fv.pace_context = usage_ctx.team_pace or _LEAGUE_AVG_PACE

    # ------------------------------------------------------------------
    # INJURY / ROLE  (SOURCE: SportsDataIO primary)
    # ------------------------------------------------------------------
    inj_ctx = injury_context_service.get_injury_context(
        player.player_id, player.team_id, game_date
    )
    fv.player_injury_status = inj_ctx.status
    fv.teammates_out_count = inj_ctx.teammates_out_count
    fv.teammate_usage_vacuum_factor = inj_ctx.teammate_usage_vacuum
    fv.starter_flag = inj_ctx.is_starter

    # Role stability: consistent starter = 1.0, inconsistent or bench = <1.0
    fv.role_stability_factor = 1.0 if inj_ctx.is_starter else 0.90

    # Projected minutes from lineup context
    if inj_ctx.projected_minutes > 0:
        fv.projected_minutes = inj_ctx.projected_minutes
    else:
        fv.projected_minutes = player.minutes_per_game

    # Recalculate per-minute now that we have a projected minute estimate
    if fv.projected_minutes > 0 and fv.season_avg > 0:
        fv.season_per_minute = fv.season_avg / fv.projected_minutes

    # ------------------------------------------------------------------
    # SPLITS  (SOURCE: nba_api primary)
    # ------------------------------------------------------------------
    split_ctx = splits_service.get_split_context(
        player_id=player.player_id,
        prop_type=prop_type,
        opponent_team_id=opponent_team_id,
        is_home=is_home,
    )
    # Enrich with game-log level data for rolling std dev
    split_ctx = splits_service.enrich_split_context_with_logs(
        split_ctx, player.player_id, prop_type
    )

    fv.recent_5_avg = split_ctx.last_5_avg or fv.season_avg
    fv.recent_10_avg = split_ctx.last_10_avg or fv.season_avg
    fv.recent_std_dev = split_ctx.last_10_std_dev

    # Home/away split factor
    if is_home:
        fv.home_away_split_factor = split_ctx.home_split_factor
    else:
        fv.home_away_split_factor = split_ctx.away_split_factor

    fv.opponent_split_factor = split_ctx.vs_opp_factor
    fv.last_n_split_factor = split_ctx.recent_trend_factor

    # ------------------------------------------------------------------
    # MATCHUP CONTEXT  (SOURCE: SportsDataIO + nba_api blended)
    # ------------------------------------------------------------------
    matchup = matchup_context_service.get_matchup_context(
        home_team_id=player.team_id if is_home else opponent_team_id,
        away_team_id=opponent_team_id if is_home else player.team_id,
        defense_team_id=opponent_team_id,
        game_total=game_total,
        point_spread=point_spread,
    )
    fv.opponent_defense_factor = matchup.defense_factor
    fv.opponent_recent_defense_factor = matchup.recent_defense_factor
    fv.opp_pace = matchup.opp_pace
    fv.opp_def_rating = matchup.def_rating
    fv.opp_pts_allowed = matchup.pts_allowed_per_game

    # ------------------------------------------------------------------
    # DvP  (SOURCE: derived internally from nba_api + SportsDataIO)
    # ------------------------------------------------------------------
    dvp_entry = dvp_service.get_dvp(opponent_team_id, player.position.value)
    if dvp_entry is not None:
        fv.dvp_points_factor = dvp_entry.norm_pts
        fv.dvp_rebounds_factor = dvp_entry.norm_reb
        fv.dvp_assists_factor = dvp_entry.norm_ast
        fv.dvp_fantasy_factor = dvp_entry.norm_fantasy
        fv.dvp_pts_allowed = dvp_entry.pts_allowed
        fv.dvp_reb_allowed = dvp_entry.reb_allowed
        fv.dvp_ast_allowed = dvp_entry.ast_allowed
        fv.dvp_fantasy_allowed = dvp_entry.fantasy_allowed

    # ------------------------------------------------------------------
    # DATA COMPLETENESS
    # ------------------------------------------------------------------
    fv.data_completeness, fv.low_confidence_flags = _assess_completeness(fv)

    return fv


def _fill_base_production(fv: FeatureVector, player: Player, prop_type: str) -> None:
    """
    Fill season_avg from the Player entity.

    SOURCE: SportsDataIO primary (season averages loaded via PlayerContextService).
    """
    stat_map = {
        "points": player.points_per_game,
        "rebounds": player.rebounds_per_game,
        "assists": player.assists_per_game,
        "threes": player.threes_per_game,
        "blocks": player.blocks_per_game,
        "steals": player.steals_per_game,
        "turnovers": player.turnovers_per_game,
        "pra": (
            player.points_per_game
            + player.rebounds_per_game
            + player.assists_per_game
        ),
    }
    fv.season_avg = stat_map.get(prop_type, 0.0)
    fv.projected_minutes = player.minutes_per_game
    fv.season_per_minute = (
        fv.season_avg / player.minutes_per_game
        if player.minutes_per_game > 0
        else 0.0
    )


def _assess_completeness(fv: FeatureVector) -> tuple[float, list[str]]:
    """
    Score the data completeness of a FeatureVector (0-1).

    Flags missing or default-value fields that reduce model confidence.
    """
    flags: list[str] = []
    checks = {
        "usage_rate": fv.usage_rate,
        "touches_per_game": fv.touches_per_game,
        "season_avg": fv.season_avg,
        "projected_minutes": fv.projected_minutes,
        "dvp_points_factor": fv.dvp_points_factor != 1.0 or fv.dvp_pts_allowed > 0,
        "pace_context": fv.pace_context > 0,
    }
    filled = 0
    for name, val in checks.items():
        if isinstance(val, bool):
            if val:
                filled += 1
            else:
                flags.append(f"missing_{name}")
        else:
            if val and val != 0.0:
                filled += 1
            else:
                flags.append(f"missing_{name}")

    score = filled / len(checks)
    return score, flags


def build_feature_store(
    players: list[Player],
    opponent_map: dict[str, tuple[str, str]],   # player_id -> (opp_team_id, opp_abbr)
    prop_types: list[str],
    is_home_map: dict[str, bool],
    game_id_map: dict[str, str],
    game_total_map: dict[str, float] = {},
    spread_map: dict[str, float] = {},
    game_date: Optional[date] = None,
) -> dict[tuple[str, str], FeatureVector]:
    """
    Build a FeatureVector for every (player_id, prop_type) combination.

    Returns dict keyed by (player_id, prop_type) for fast prop-eval lookup.
    """
    store: dict[tuple[str, str], FeatureVector] = {}

    for player in players:
        opp_info = opponent_map.get(player.player_id, ("", ""))
        opp_team_id, opp_abbr = opp_info
        is_home = is_home_map.get(player.player_id, True)
        game_id = game_id_map.get(player.player_id, "")
        total = game_total_map.get(player.player_id, 0.0)
        spread = spread_map.get(player.player_id, 0.0)

        for pt in prop_types:
            try:
                fv = build_feature_vector(
                    player=player,
                    opponent_team_id=opp_team_id,
                    opponent_team_abbr=opp_abbr,
                    prop_type=pt,
                    is_home=is_home,
                    game_id=game_id,
                    game_total=total,
                    point_spread=spread,
                    game_date=game_date,
                )
                store[(player.player_id, pt)] = fv
            except Exception as exc:
                logger.warning(
                    "Feature build failed for %s / %s: %s",
                    player.name, pt, exc,
                )

    logger.info(
        "PlayerFeatureBuilder: built %d feature vectors for %d players × %d prop types",
        len(store), len(players), len(prop_types),
    )
    return store
