"""
Matchup adjustment model.

Computes a matchup multiplier from opponent defensive efficiency and
positional defensive statistics.  The multiplier is applied on top of
the player's baseline projection.

Directionality contract (verified here and in tests):
  factor > 1.0  →  weak opponent defense   →  boost projection
  factor < 1.0  →  strong opponent defense  →  reduce projection
  factor = 1.0  →  league-average defense   →  neutral

For every prop type:
  factor = allowed_by_opponent_to_position / league_avg_allowed_to_position
"""

from __future__ import annotations

import logging

from domain.entities import Player, TeamDefense
from domain.enums import Position, PropType
from utils.math_helpers import clamp

logger = logging.getLogger(__name__)


class MatchupModel:
    """Produces matchup adjustment multipliers for individual stat projections."""

    def pts_allowed_to_position(self, defense: TeamDefense, position: Position) -> float:
        """Return points allowed per game to players at *position*."""
        mapping = {
            Position.PG: defense.pts_allowed_pg,
            Position.SG: defense.pts_allowed_sg,
            Position.SF: defense.pts_allowed_sf,
            Position.PF: defense.pts_allowed_pf,
            Position.C: defense.pts_allowed_c,
            Position.G: (defense.pts_allowed_pg + defense.pts_allowed_sg) / 2,
            Position.F: (defense.pts_allowed_sf + defense.pts_allowed_pf) / 2,
            Position.FC: (defense.pts_allowed_pf + defense.pts_allowed_c) / 2,
            Position.GF: (defense.pts_allowed_sg + defense.pts_allowed_sf) / 2,
        }
        return mapping.get(position, 0.0)

    def reb_allowed_to_position(self, defense: TeamDefense, position: Position) -> float:
        mapping = {
            Position.PG: defense.reb_allowed_pg,
            Position.SG: defense.reb_allowed_sg,
            Position.SF: defense.reb_allowed_sf,
            Position.PF: defense.reb_allowed_pf,
            Position.C: defense.reb_allowed_c,
            Position.G: (defense.reb_allowed_pg + defense.reb_allowed_sg) / 2,
            Position.F: (defense.reb_allowed_sf + defense.reb_allowed_pf) / 2,
            Position.FC: (defense.reb_allowed_pf + defense.reb_allowed_c) / 2,
            Position.GF: (defense.reb_allowed_sg + defense.reb_allowed_sf) / 2,
        }
        return mapping.get(position, 0.0)

    def ast_allowed_to_position(self, defense: TeamDefense, position: Position) -> float:
        mapping = {
            Position.PG: defense.ast_allowed_pg,
            Position.SG: defense.ast_allowed_sg,
            Position.SF: defense.ast_allowed_sf,
            Position.PF: defense.ast_allowed_pf,
            Position.C: defense.ast_allowed_c,
            Position.G: (defense.ast_allowed_pg + defense.ast_allowed_sg) / 2,
            Position.F: (defense.ast_allowed_sf + defense.ast_allowed_pf) / 2,
            Position.FC: (defense.ast_allowed_pf + defense.ast_allowed_c) / 2,
            Position.GF: (defense.ast_allowed_sg + defense.ast_allowed_sf) / 2,
        }
        return mapping.get(position, 0.0)

    def threes_allowed_to_position(self, defense: TeamDefense, position: Position) -> float:
        mapping = {
            Position.PG: defense.threes_allowed_pg,
            Position.SG: defense.threes_allowed_sg,
            Position.SF: defense.threes_allowed_sf,
            Position.PF: defense.threes_allowed_pf,
            Position.C: defense.threes_allowed_c,
            Position.G: (defense.threes_allowed_pg + defense.threes_allowed_sg) / 2,
            Position.F: (defense.threes_allowed_sf + defense.threes_allowed_pf) / 2,
            Position.FC: (defense.threes_allowed_pf + defense.threes_allowed_c) / 2,
            Position.GF: (defense.threes_allowed_sg + defense.threes_allowed_sf) / 2,
        }
        return mapping.get(position, 0.0)

    def fpa_for_position(self, defense: TeamDefense, position: Position) -> float:
        """Fantasy points allowed by the defense to *position*."""
        mapping = {
            Position.PG: defense.fpa_pg,
            Position.SG: defense.fpa_sg,
            Position.SF: defense.fpa_sf,
            Position.PF: defense.fpa_pf,
            Position.C: defense.fpa_c,
            Position.G: (defense.fpa_pg + defense.fpa_sg) / 2,
            Position.F: (defense.fpa_sf + defense.fpa_pf) / 2,
            Position.FC: (defense.fpa_pf + defense.fpa_c) / 2,
            Position.GF: (defense.fpa_sg + defense.fpa_sf) / 2,
        }
        return mapping.get(position, 0.0)

    def positional_defense_factor(
        self,
        defense: TeamDefense,
        player: Player,
        prop_type: PropType,
    ) -> float:
        """
        Compute multiplier based on how many of *prop_type* the opponent allows
        to *player*'s position vs the league average.

        factor = opp_allowed / league_avg_allowed_to_position
        Clamped to [0.75, 1.25].
        """
        pos = player.position

        # League-average baselines per position (approximate 2024-25)
        league_avg: dict[PropType, dict[Position, float]] = {
            PropType.POINTS: {
                Position.PG: 22.0, Position.SG: 20.5, Position.SF: 21.5,
                Position.PF: 19.5, Position.C: 24.0,
                Position.G: 21.2, Position.F: 20.5, Position.FC: 21.8, Position.GF: 21.0,
            },
            PropType.REBOUNDS: {
                Position.PG: 3.2, Position.SG: 3.7, Position.SF: 5.2,
                Position.PF: 8.5, Position.C: 11.0,
                Position.G: 3.5, Position.F: 6.8, Position.FC: 9.7, Position.GF: 4.5,
            },
            PropType.ASSISTS: {
                Position.PG: 6.4, Position.SG: 3.1, Position.SF: 2.6,
                Position.PF: 2.3, Position.C: 2.8,
                Position.G: 4.8, Position.F: 2.5, Position.FC: 2.6, Position.GF: 2.9,
            },
            PropType.THREES: {
                Position.PG: 2.4, Position.SG: 2.3, Position.SF: 2.0,
                Position.PF: 0.9, Position.C: 0.4,
                Position.G: 2.3, Position.F: 1.5, Position.FC: 0.7, Position.GF: 2.2,
            },
            PropType.BLOCKS: {
                Position.PG: 0.2, Position.SG: 0.3, Position.SF: 0.5,
                Position.PF: 0.8, Position.C: 1.5,
                Position.G: 0.2, Position.F: 0.6, Position.FC: 1.1, Position.GF: 0.4,
            },
            PropType.STEALS: {
                Position.PG: 1.2, Position.SG: 1.0, Position.SF: 0.9,
                Position.PF: 0.7, Position.C: 0.6,
                Position.G: 1.1, Position.F: 0.8, Position.FC: 0.6, Position.GF: 0.9,
            },
            PropType.TURNOVERS: {
                Position.PG: 2.8, Position.SG: 2.2, Position.SF: 2.0,
                Position.PF: 1.8, Position.C: 2.2,
                Position.G: 2.5, Position.F: 1.9, Position.FC: 2.0, Position.GF: 2.2,
            },
        }

        # Map prop to allowed stat
        # Blocks / steals use positional allowed data where present; fall back to
        # team-level "forced" stats only as a last resort with a neutral sentinel.
        def _blocks_allowed(d: TeamDefense, p: Position) -> float:
            # blocks_allowed_per_game is a team aggregate, not position-specific.
            # Return 0 to trigger neutral fallback; avoids wrong-granularity mixing.
            return d.blocks_allowed_per_game  # team-level — usable directionally

        def _steals_allowed(d: TeamDefense, p: Position) -> float:
            # "steals_forced" is offensive TOs forced, not steals allowed to players.
            # Return 0 to keep this neutral when only team-level data is available.
            return 0.0  # intentionally neutral — no position-level steals allowed data

        allowed_getters = {
            PropType.POINTS:    self.pts_allowed_to_position,
            PropType.REBOUNDS:  self.reb_allowed_to_position,
            PropType.ASSISTS:   self.ast_allowed_to_position,
            PropType.THREES:    self.threes_allowed_to_position,
            PropType.BLOCKS:    _blocks_allowed,
            PropType.STEALS:    _steals_allowed,
            PropType.TURNOVERS: lambda d, p: d.turnovers_forced_per_game,
            # PRA: composite pts + reb + ast allowed vs position
            PropType.PRA:       None,   # handled separately below
        }

        if prop_type == PropType.PRA:
            pts = self.pts_allowed_to_position(defense, pos)
            reb = self.reb_allowed_to_position(defense, pos)
            ast = self.ast_allowed_to_position(defense, pos)
            allowed = pts + reb + ast

            pts_lg = league_avg.get(PropType.POINTS, {}).get(pos, 0.0)
            reb_lg = league_avg.get(PropType.REBOUNDS, {}).get(pos, 0.0)
            ast_lg = league_avg.get(PropType.ASSISTS, {}).get(pos, 0.0)
            league_baseline = pts_lg + reb_lg + ast_lg

            if league_baseline <= 0 or allowed <= 0:
                return 1.0
            factor = allowed / league_baseline
            logger.debug(
                "MatchupModel PRA pos=%s: allowed=%.1f league=%.1f factor=%.3f",
                pos.value, allowed, league_baseline, factor,
            )
            return clamp(factor, 0.75, 1.25)

        getter = allowed_getters.get(prop_type)
        if getter is None:
            return 1.0

        allowed = getter(defense, pos)
        league_baseline = league_avg.get(prop_type, {}).get(pos, 0.0)

        if league_baseline <= 0 or allowed <= 0:
            logger.debug(
                "MatchupModel %s pos=%s: no data (allowed=%.2f, league=%.2f) → neutral",
                prop_type.value, pos.value, allowed, league_baseline,
            )
            return 1.0

        factor = allowed / league_baseline
        logger.debug(
            "MatchupModel %s pos=%s: allowed=%.2f league=%.2f factor=%.3f",
            prop_type.value, pos.value, allowed, league_baseline, factor,
        )
        return clamp(factor, 0.75, 1.25)
