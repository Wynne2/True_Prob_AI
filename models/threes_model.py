"""
3-pointers made projection model  –  Binomial distribution.

Models 3PM as Binomial(n_attempts, make_rate) where:
  n_attempts = projected 3PA (attempts × minutes factor × matchup)
  make_rate  = player's season 3P% adjusted for opponent 3P% allowed
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


class ThreesModel(BaseStatModel):
    prop_type = PropType.THREES
    distribution_type = DistributionType.BINOMIAL

    def __init__(self) -> None:
        self._minutes = MinutesModel()
        self._matchup = MatchupModel()
        self._fpa = FPAModel()
        self._variance = VarianceModel()
        self._confidence = ConfidenceModel()

    def _base_stat(self, player: Player) -> float:
        return player.threes_per_game

    def _stat_std(self, player: Player, projected_mean: float) -> float:
        return self._variance.std(player, PropType.THREES, projected_mean)

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

        # Projected 3PA
        if player.three_point_attempts > 0:
            base_3pa = player.three_point_attempts
        elif player.is_starter:
            base_3pa = 2.0   # conservative starter placeholder
        else:
            base_3pa = 1.0   # conservative bench placeholder
        proj_3pa = base_3pa * minutes_factor

        # Positional defence adjustment on attempts
        if defense:
            pos_factor = self._matchup.positional_defense_factor(defense, player, PropType.THREES)
            proj_3pa *= pos_factor

        # 3P% adjustment based on opponent permissiveness (rough proxy)
        make_rate = player.three_point_pct or 0.355
        if defense:
            # Approx: if opp allows lots of 3s, shot quality may be slightly lower
            # but we keep make_rate stable (not adjusted) as opponent scheme varies
            pass

        fpa_factor = self._fpa.factor(defense, player, weight=0.08) if defense else 1.0

        form_factor = self._recent_form_factor(
            player.threes_per_game,
            player.last5_threes,
            player.last5_threes,  # use same for both short/medium windows
        )
        injury_factor = self._injury_factor(player)

        # Projected 3PM = 3PA × make_rate × adjustments
        projected = (
            proj_3pa
            * make_rate
            * fpa_factor
            * form_factor
            * injury_factor
        )
        projected = clamp(projected, 0.0, 12.0)

        consistency = self._variance.consistency_score(player, PropType.THREES, projected)
        confidence, _ = self._confidence.score(
            player, PropType.THREES, projected, consistency,
            edge=0.0, has_defense_data=defense is not None
        )

        # Store binomial parameters in the projection
        stat_proj = self._build_projection(
            player, game, is_home, projected,
            minutes_factor, 1.0, 1.0,
            pos_factor if defense else 1.0, 1.0,
            fpa_factor, form_factor, injury_factor, 1.0,
            confidence,
        )
        # Override distribution params for binomial
        stat_proj.dist_n = max(1, int(round(proj_3pa)))
        stat_proj.dist_p = make_rate
        return stat_proj
