"""
Turnovers projection model  –  Negative Binomial distribution.

Turnovers are overdispersed (some games a player has 0, others 6+).
NegBin captures this better than Poisson.
"""

from __future__ import annotations

from typing import Optional

from domain.entities import Game, Player, StatProjection, TeamDefense
from domain.enums import DistributionType, PropType
from models.base_model import BaseStatModel
from models.confidence_model import ConfidenceModel
from models.fantasy_points_allowed_model import FPAModel
from models.minutes_model import MinutesModel
from models.usage_model import UsageModel
from models.variance_model import VarianceModel
from utils.math_helpers import clamp


class TurnoversModel(BaseStatModel):
    prop_type = PropType.TURNOVERS
    distribution_type = DistributionType.NEGATIVE_BINOMIAL

    def __init__(self) -> None:
        self._minutes = MinutesModel()
        self._usage = UsageModel()
        self._fpa = FPAModel()
        self._variance = VarianceModel()
        self._confidence = ConfidenceModel()

    def _base_stat(self, player: Player) -> float:
        return player.turnovers_per_game

    def _stat_std(self, player: Player, projected_mean: float) -> float:
        return self._variance.std(player, PropType.TURNOVERS, projected_mean)

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

        eff_usage = self._usage.project(player, game)
        usage_factor = eff_usage / player.usage_rate if player.usage_rate > 0 else 1.0

        pace = defense.pace if defense else 100.0
        pace_factor = self._pace_factor(pace)

        # Defensive pressure: teams that force turnovers boost opposing TOV
        if defense:
            pressure_factor = clamp(defense.steals_forced_per_game / 7.5, 0.85, 1.15)
        else:
            pressure_factor = 1.0

        injury_factor = self._injury_factor(player)

        base = player.turnovers_per_game or 2.0
        projected = (
            base
            * minutes_factor
            * usage_factor
            * pace_factor
            * pressure_factor
            * injury_factor
        )
        projected = clamp(projected, 0.0, 8.0)

        consistency = self._variance.consistency_score(player, PropType.TURNOVERS, projected)
        confidence, _ = self._confidence.score(
            player, PropType.TURNOVERS, projected, consistency,
            edge=0.0, has_defense_data=defense is not None
        )

        return self._build_projection(
            player, game, is_home, projected,
            minutes_factor, usage_factor, pace_factor,
            pressure_factor, pressure_factor,
            1.0, 1.0, injury_factor, 1.0,
            confidence,
        )
