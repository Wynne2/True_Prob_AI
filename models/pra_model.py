"""
Points + Rebounds + Assists (PRA) projection model  –  Normal distribution.

PRA is modelled as Normal(μ_PRA, σ_PRA) where:
  μ_PRA = μ_pts + μ_reb + μ_ast
  σ_PRA > sqrt(σ²_pts + σ²_reb + σ²_ast)  [variance inflated for covariance]

The covariance inflation reflects the real positive correlation among
a player's stats in a given game (e.g. high-minute games boost all stats).
"""

from __future__ import annotations

import math
from typing import Optional

from domain.entities import Game, Player, StatProjection, TeamDefense
from domain.enums import DistributionType, PropType
from models.assists_model import AssistsModel
from models.base_model import BaseStatModel
from models.confidence_model import ConfidenceModel
from models.points_model import PointsModel
from models.rebounds_model import ReboundsModel
from models.variance_model import VarianceModel
from utils.math_helpers import clamp


# Covariance inflation factor: empirical correlation among pts/reb/ast
# means the combined std is higher than independent sum of std
_COV_INFLATION: float = 1.12


class PRAModel(BaseStatModel):
    prop_type = PropType.PRA
    distribution_type = DistributionType.NORMAL

    def __init__(self) -> None:
        self._pts_model = PointsModel()
        self._reb_model = ReboundsModel()
        self._ast_model = AssistsModel()
        self._variance = VarianceModel()
        self._confidence = ConfidenceModel()

    def _base_stat(self, player: Player) -> float:
        return (
            player.points_per_game
            + player.rebounds_per_game
            + player.assists_per_game
        )

    def _stat_std(self, player: Player, projected_mean: float) -> float:
        # Combined std with covariance inflation
        std_pts = self._variance.std(player, PropType.POINTS, player.points_per_game or projected_mean * 0.6)
        std_reb = self._variance.std(player, PropType.REBOUNDS, player.rebounds_per_game or projected_mean * 0.2)
        std_ast = self._variance.std(player, PropType.ASSISTS, player.assists_per_game or projected_mean * 0.2)
        combined = math.sqrt(std_pts**2 + std_reb**2 + std_ast**2) * _COV_INFLATION
        return max(combined, projected_mean * 0.12)

    def project(
        self,
        player: Player,
        game: Game,
        defense: Optional[TeamDefense],
        is_home: bool = True,
    ) -> StatProjection:
        pts_proj = self._pts_model.project(player, game, defense, is_home)
        reb_proj = self._reb_model.project(player, game, defense, is_home)
        ast_proj = self._ast_model.project(player, game, defense, is_home)

        projected = (
            pts_proj.projected_value
            + reb_proj.projected_value
            + ast_proj.projected_value
        )
        projected = clamp(projected, 0.0, 100.0)

        # Use the most conservative confidence among the three sub-models
        from domain.enums import ConfidenceTier
        tier_order = [ConfidenceTier.HIGH, ConfidenceTier.MEDIUM, ConfidenceTier.LOW, ConfidenceTier.VERY_LOW]
        confidence = max(
            [pts_proj.confidence, reb_proj.confidence, ast_proj.confidence],
            key=lambda t: tier_order.index(t),
        )

        # Combined std with covariance inflation
        std = self._stat_std(player, projected)

        baseline_sum = (
            pts_proj.baseline_projection
            + reb_proj.baseline_projection
            + ast_proj.baseline_projection
        )
        stat_proj = self._build_projection(
            player, game, is_home, projected,
            pts_proj.minutes_factor, pts_proj.usage_factor, pts_proj.pace_factor,
            pts_proj.matchup_factor, pts_proj.defense_factor,
            pts_proj.fpa_factor, pts_proj.recent_form_factor,
            pts_proj.injury_factor, pts_proj.home_away_factor,
            confidence,
            baseline_projection=baseline_sum,
            expected_minutes=pts_proj.expected_minutes,
            environment_multiplier=(
                (
                    pts_proj.environment_multiplier
                    + reb_proj.environment_multiplier
                    + ast_proj.environment_multiplier
                )
                / 3.0
            ),
        )
        stat_proj.dist_std = std
        return stat_proj
