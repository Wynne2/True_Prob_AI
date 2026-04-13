"""
Points projection model  –  Normal distribution.

Projects: minutes × usage × pace × matchup × defense × fpa × form × injuries
Distribution: Normal(μ, σ)
"""

from __future__ import annotations

from typing import Optional

from domain.entities import Game, Player, StatProjection, TeamDefense
from domain.enums import ConfidenceTier, DistributionType, PropType
from models.base_model import BaseStatModel
from models.confidence_model import ConfidenceModel
from models.fantasy_points_allowed_model import FPAModel
from models.matchup_model import MatchupModel
from models.minutes_model import MinutesModel
from models.usage_model import UsageModel
from models.variance_model import VarianceModel
from utils.math_helpers import clamp


class PointsModel(BaseStatModel):
    """Projects player points using a blended weighted formula."""

    prop_type = PropType.POINTS
    distribution_type = DistributionType.NORMAL

    def __init__(self) -> None:
        self._minutes = MinutesModel()
        self._usage = UsageModel()
        self._matchup = MatchupModel()
        self._fpa = FPAModel()
        self._variance = VarianceModel()
        self._confidence = ConfidenceModel()

    def _base_stat(self, player: Player) -> float:
        return player.points_per_game

    def _stat_std(self, player: Player, projected_mean: float) -> float:
        return self._variance.std(player, PropType.POINTS, projected_mean)

    def project(
        self,
        player: Player,
        game: Game,
        defense: Optional[TeamDefense],
        is_home: bool = True,
    ) -> StatProjection:
        # 1. Expected minutes
        exp_minutes = self._minutes.project(player, game, is_home)
        if player.minutes_per_game > 0:
            minutes_factor = exp_minutes / player.minutes_per_game
        else:
            minutes_factor = 1.0

        # 2. Usage
        eff_usage = self._usage.project(player, game)
        usage_factor = eff_usage / player.usage_rate if player.usage_rate > 0 else 1.0

        # 3. Pace
        pace = defense.pace if defense else 100.0
        pace_factor = self._pace_factor(pace)

        # 4. Defensive efficiency / positional defence
        if defense:
            def_eff_factor = self._defensive_efficiency_factor(defense.defensive_efficiency)
            pos_factor = self._matchup.positional_defense_factor(defense, player, PropType.POINTS)
        else:
            def_eff_factor = 1.0
            pos_factor = 1.0
        matchup_factor = (def_eff_factor + pos_factor) / 2

        # 5. FPA factor
        fpa_factor = self._fpa.factor(defense, player) if defense else 1.0

        # 6. Recent form
        form_factor = self._recent_form_factor(
            player.points_per_game,
            player.last5_points,
            player.last10_points,
        )

        # 7. Injury
        injury_factor = self._injury_factor(player)

        # 8. Home/away split
        home_away_factor = self._home_away_factor(is_home, player)

        # Projection
        base = player.points_per_game or 15.0
        projected = (
            base
            * minutes_factor
            * usage_factor
            * pace_factor
            * matchup_factor
            * fpa_factor
            * form_factor
            * injury_factor
            * home_away_factor
        )
        projected = clamp(projected, 0.0, 65.0)

        # Confidence
        consistency = self._variance.consistency_score(player, PropType.POINTS, projected)
        confidence, _ = self._confidence.score(
            player, PropType.POINTS, projected, consistency,
            edge=0.0, has_defense_data=defense is not None
        )

        return self._build_projection(
            player, game, is_home, projected,
            minutes_factor, usage_factor, pace_factor,
            matchup_factor, def_eff_factor if defense else 1.0,
            fpa_factor, form_factor, injury_factor, home_away_factor,
            confidence,
        )
