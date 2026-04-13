"""
Blocks projection model  –  Poisson distribution.

Blocks are rare, discrete, and well-modelled by Poisson.
Key factors: opponent paint frequency, player's role at rim.
"""

from __future__ import annotations

from typing import Optional

from domain.entities import Game, Player, StatProjection, TeamDefense
from domain.enums import DistributionType, PropType
from models.base_model import BaseStatModel
from models.confidence_model import ConfidenceModel
from models.fantasy_points_allowed_model import FPAModel
from models.minutes_model import MinutesModel
from models.variance_model import VarianceModel
from utils.math_helpers import clamp


class BlocksModel(BaseStatModel):
    prop_type = PropType.BLOCKS
    distribution_type = DistributionType.POISSON

    def __init__(self) -> None:
        self._minutes = MinutesModel()
        self._fpa = FPAModel()
        self._variance = VarianceModel()
        self._confidence = ConfidenceModel()

    def _base_stat(self, player: Player) -> float:
        return player.blocks_per_game

    def _stat_std(self, player: Player, projected_mean: float) -> float:
        return self._variance.std(player, PropType.BLOCKS, projected_mean)

    def project(
        self,
        player: Player,
        game: Game,
        defense: Optional[TeamDefense],
        is_home: bool = True,
    ) -> StatProjection:
        exp_minutes = self._minutes.project(player, game, is_home)
        minutes_factor = (exp_minutes / player.minutes_per_game
                          if player.minutes_per_game > 0 else 1.0)

        pace = defense.pace if defense else 100.0
        pace_factor = self._pace_factor(pace)

        # Opponent paint frequency (blocks are rim-protection events)
        if defense:
            paint_factor = clamp(defense.paint_pts_allowed / 48.0, 0.80, 1.20)
        else:
            paint_factor = 1.0

        fpa_factor = self._fpa.factor(defense, player, weight=0.08) if defense else 1.0
        injury_factor = self._injury_factor(player)

        base = player.blocks_per_game or 0.5
        projected = (
            base
            * minutes_factor
            * pace_factor
            * paint_factor
            * fpa_factor
            * injury_factor
        )
        projected = clamp(projected, 0.0, 8.0)

        consistency = self._variance.consistency_score(player, PropType.BLOCKS, projected)
        confidence, _ = self._confidence.score(
            player, PropType.BLOCKS, projected, consistency,
            edge=0.0, has_defense_data=defense is not None
        )

        stat_proj = self._build_projection(
            player, game, is_home, projected,
            minutes_factor, 1.0, pace_factor,
            paint_factor, paint_factor,
            fpa_factor, 1.0, injury_factor, 1.0,
            confidence,
        )
        stat_proj.dist_lambda = projected
        return stat_proj
