"""
Assemble TeamDefense for projection models from:

  - SportsDataIO `TeamStatsAllowedByPosition` — per-position pts/reb/ast/3PM/FPA allowed
  - SportsDataIO `TeamSeasonStats` — team steals/blocks, opponent turnovers, pace proxy
  - nba_api `LeagueDashTeamStats` Advanced — pace and defensive rating (per 100)

This is the single path `SportsDataIOProvider.get_team_defense` uses so MatchupModel /
FPAModel receive real positional data instead of repeating team-level opp_pts for every slot.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from domain.constants import LEAGUE_AVG_DEF_EFF, LEAGUE_AVG_PACE, NBA_SEASON, SDIO_SEASON
from domain.entities import TeamDefense
from domain.enums import DataSource
from data.loaders.nba_api_loader import fetch_team_pace_batch
from data.loaders.sportsdataio_loader import (
    fetch_team_season_stats,
    fetch_team_stats_allowed_by_position,
    index_by_team_id,
)

logger = logging.getLogger(__name__)

# Normalize SDIO position labels to PG/SG/SF/PF/C keys
_POS_KEYS = ("PG", "SG", "SF", "PF", "C")


def _norm_pos(label: str) -> Optional[str]:
    if not label:
        return None
    u = str(label).upper().strip()
    if u in _POS_KEYS:
        return u
    for k in _POS_KEYS:
        if u.startswith(k):
            return k
    return None


def _row_by_position(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        pk = _norm_pos(str(r.get("opponent_position", "")))
        if pk:
            out[pk] = r
    return out


def build_team_defense(team_id: str) -> Optional[TeamDefense]:
    """
    Return a fully populated TeamDefense for *team_id*, or None if no SDIO data.

    Falls back to flat team-level opponent points for any position bucket missing
    from the per-position feed (injury / API sparsity).
    """
    season = SDIO_SEASON
    team_map = {r["team_id"]: r for r in fetch_team_season_stats(season)}
    raw_team = team_map.get(team_id)
    if not raw_team:
        return None

    abbr = str(raw_team.get("team", "") or "")
    opp_pts = float(raw_team.get("opp_pts", 0) or 0)
    opp_reb = float(raw_team.get("opp_reb", 0) or 0)
    opp_ast = float(raw_team.get("opp_ast", 0) or 0)
    opp_fg3m = float(raw_team.get("opp_fg3m", 0) or 0)
    team_stl = float(raw_team.get("stl", 0) or 0)
    team_blk = float(raw_team.get("blk", 0) or 0)
    opp_tov = float(raw_team.get("opp_tov", 0) or 0)
    if opp_tov < 5.0:
        opp_tov = 14.0
    poss_pg = float(raw_team.get("poss", 0) or 0)

    all_pos_rows = fetch_team_stats_allowed_by_position(season)
    by_team = index_by_team_id(all_pos_rows)
    rows = by_team.get(team_id, [])
    by_pos = _row_by_position(rows)

    def _pts(pk: str) -> float:
        r = by_pos.get(pk)
        if r and (r.get("pts_allowed") or 0) > 0:
            return float(r["pts_allowed"])
        return opp_pts

    def _reb(pk: str) -> float:
        r = by_pos.get(pk)
        if r and (r.get("reb_allowed") or 0) > 0:
            return float(r["reb_allowed"])
        return opp_reb

    def _ast(pk: str) -> float:
        r = by_pos.get(pk)
        if r and (r.get("ast_allowed") or 0) > 0:
            return float(r["ast_allowed"])
        return opp_ast

    def _threes(pk: str) -> float:
        r = by_pos.get(pk)
        if r and (r.get("fg3m_allowed") or 0) >= 0:
            return float(r["fg3m_allowed"])
        return opp_fg3m

    def _fpa(pk: str) -> float:
        r = by_pos.get(pk)
        if r and (r.get("fantasy_allowed") or 0) > 0:
            return float(r["fantasy_allowed"])
        return 0.0

    # nba_api pace + def rating (team_id must match between feeds)
    pace = poss_pg if poss_pg > 90 else LEAGUE_AVG_PACE
    def_rating = LEAGUE_AVG_DEF_EFF
    try:
        nba_teams = fetch_team_pace_batch(season=NBA_SEASON, date_str=date.today().isoformat())
        nba_by_id = {str(r.get("team_id", "")): r for r in nba_teams}
        nr = nba_by_id.get(str(team_id))
        if nr:
            p = float(nr.get("pace", 0) or 0)
            dr = float(nr.get("def_rating", 0) or 0)
            if p > 90:
                pace = p
            if dr > 90:
                def_rating = dr
    except Exception as exc:
        logger.debug("team_defense_builder: nba_api pace merge skipped: %s", exc)

    # Points in paint proxy: ~44% of opponent points (tunable); unblocks blocks_model
    paint_pts = max(opp_pts * 0.44, 38.0) if opp_pts > 0 else 44.0
    perimeter_pts = max(opp_pts * 0.32, 26.0) if opp_pts > 0 else 30.0

    td = TeamDefense(
        team_id=team_id,
        team_abbr=abbr,
        data_source=DataSource.SPORTSDATAIO,
        pts_allowed_pg=_pts("PG"),
        pts_allowed_sg=_pts("SG"),
        pts_allowed_sf=_pts("SF"),
        pts_allowed_pf=_pts("PF"),
        pts_allowed_c=_pts("C"),
        reb_allowed_pg=_reb("PG"),
        reb_allowed_sg=_reb("SG"),
        reb_allowed_sf=_reb("SF"),
        reb_allowed_pf=_reb("PF"),
        reb_allowed_c=_reb("C"),
        ast_allowed_pg=_ast("PG"),
        ast_allowed_sg=_ast("SG"),
        ast_allowed_sf=_ast("SF"),
        ast_allowed_pf=_ast("PF"),
        ast_allowed_c=_ast("C"),
        threes_allowed_pg=_threes("PG"),
        threes_allowed_sg=_threes("SG"),
        threes_allowed_sf=_threes("SF"),
        threes_allowed_pf=_threes("PF"),
        threes_allowed_c=_threes("C"),
        fpa_pg=_fpa("PG"),
        fpa_sg=_fpa("SG"),
        fpa_sf=_fpa("SF"),
        fpa_pf=_fpa("PF"),
        fpa_c=_fpa("C"),
        blocks_allowed_per_game=team_blk,
        steals_forced_per_game=team_stl,
        turnovers_forced_per_game=opp_tov,
        defensive_efficiency=def_rating,
        pace=pace,
        paint_pts_allowed=paint_pts,
        perimeter_pts_allowed=perimeter_pts,
        fast_break_pts_allowed=0.0,
        second_chance_pts_allowed=0.0,
    )
    return td
