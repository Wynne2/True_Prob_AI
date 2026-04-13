"""
Fantasy Points Allowed (FPA) model.

FPA is the number of DraftKings fantasy points the opponent allows per
game to players at the target position.  High FPA → favourable matchup.

The FPA factor is integrated as an additional multiplier across all stat
models.  This captures the overall defensive generosity of the opponent
in a single composite signal.
"""

from __future__ import annotations

from domain.constants import FPA_LEAGUE_AVG
from domain.entities import Player, TeamDefense
from domain.enums import Position
from utils.math_helpers import clamp


class FPAModel:
    """Computes the fantasy points allowed multiplier for a matchup."""

    def factor(
        self,
        defense: TeamDefense,
        player: Player,
        weight: float = 0.15,
    ) -> float:
        """
        Return an FPA-based multiplier.

        factor = 1.0 + weight × (fpa_z_score)

        where fpa_z_score measures how much better/worse than league average
        the opponent is at allowing FPA to the player's position.

        Args:
            defense: Opponent's defensive profile.
            player: The player being projected.
            weight: Sensitivity (0-1); how much FPA shifts the projection.

        Returns:
            Multiplier in [0.85, 1.15].
        """
        pos = player.position
        opp_fpa = self._fpa_for_position(defense, pos)
        league_avg = FPA_LEAGUE_AVG.get(pos, 33.0)

        if league_avg <= 0 or opp_fpa <= 0:
            return 1.0

        # Z-score relative to league average (rough approximation: std ≈ 4.5)
        fpa_std = 4.5
        z = (opp_fpa - league_avg) / fpa_std

        factor = 1.0 + weight * z
        return clamp(factor, 0.85, 1.15)

    def _fpa_for_position(self, defense: TeamDefense, position: Position) -> float:
        """Extract the raw FPA value for the given position."""
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
