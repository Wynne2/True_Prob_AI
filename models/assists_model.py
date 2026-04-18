"""
Assists projection model  –  Negative Binomial distribution.
"""

from __future__ import annotations

from typing import Optional

from domain.entities import Game, Player, StatProjection, TeamDefense
from domain.enums import DistributionType, PropType
from models.base_model import BaseStatModel
from models.confidence_model import ConfidenceModel
from models.fantasy_points_allowed_model import FPAModel
from models.matchup_model import MatchupModel
from models.minutes_model import MinutesModel
from models.variance_model import VarianceModel
from utils.math_helpers import clamp


class AssistsModel(BaseStatModel):
    prop_type = PropType.ASSISTS
    distribution_type = DistributionType.NEGATIVE_BINOMIAL

    def __init__(self) -> None:
        self._minutes = MinutesModel()
        self._matchup = MatchupModel()
        self._fpa = FPAModel()
        self._variance = VarianceModel()
        self._confidence = ConfidenceModel()

    def _base_stat(self, player: Player) -> float:
        return player.assists_per_game

    def _stat_std(self, player: Player, projected_mean: float) -> float:
        return self._variance.std(player, PropType.ASSISTS, projected_mean)

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

        if defense:
            pos_factor = self._matchup.positional_defense_factor(defense, player, PropType.ASSISTS)
            # Turnovers forced by opponent reduces assist opportunities
            tov_factor = clamp(1.0 - (defense.turnovers_forced_per_game - 14.0) * 0.01, 0.90, 1.10)
        else:
            pos_factor = 1.0
            tov_factor = 1.0

        fpa_factor = self._fpa.factor(defense, player, weight=0.10) if defense else 1.0

        form_factor = self._recent_form_factor(
            player.assists_per_game,
            player.last5_assists,
            player.last10_assists,
        )
        injury_factor = self._injury_factor(player)

        if player.assists_per_game > 0:
            base = player.assists_per_game
        elif player.is_starter:
            base = 2.5   # conservative starter placeholder
        else:
            base = 1.0   # conservative bench placeholder
        projected = (
            base
            * minutes_factor
            * pace_factor
            * pos_factor
            * tov_factor
            * fpa_factor
            * form_factor
            * injury_factor
        )
        projected = clamp(projected, 0.0, 20.0)

        consistency = self._variance.consistency_score(player, PropType.ASSISTS, projected)
        confidence, _ = self._confidence.score(
            player, PropType.ASSISTS, projected, consistency,
            edge=0.0, has_defense_data=defense is not None
        )

        return self._build_projection(
            player, game, is_home, projected,
            minutes_factor, 1.0, pace_factor,
            pos_factor, pos_factor,
            fpa_factor, form_factor, injury_factor, 1.0,
            confidence,
        )
