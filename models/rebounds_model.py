"""
Rebounds projection model  –  Negative Binomial distribution.

Rebounds are overdispersed relative to Poisson; NegBin fits empirical
NBA rebound distributions better.
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
from models.projection_baseline import blended_stat_rate
from models.projection_guards import apply_projection_guards
from models.variance_model import VarianceModel
from utils.math_helpers import clamp


class ReboundsModel(BaseStatModel):
    prop_type = PropType.REBOUNDS
    distribution_type = DistributionType.NEGATIVE_BINOMIAL

    def __init__(self) -> None:
        self._minutes = MinutesModel()
        self._matchup = MatchupModel()
        self._fpa = FPAModel()
        self._variance = VarianceModel()
        self._confidence = ConfidenceModel()

    def _base_stat(self, player: Player) -> float:
        return player.rebounds_per_game

    def _stat_std(self, player: Player, projected_mean: float) -> float:
        return self._variance.std(player, PropType.REBOUNDS, projected_mean)

    def project(
        self,
        player: Player,
        game: Game,
        defense: Optional[TeamDefense],
        is_home: bool = True,
    ) -> StatProjection:
        exp_minutes = self._minutes.project(player, game, is_home, minutes_vacuum=player.minutes_vacuum)
        mpg = max(player.minutes_per_game, 1.0)
        minutes_factor = exp_minutes / mpg if mpg > 0 else 1.0

        rate, rdetail = blended_stat_rate(player, PropType.REBOUNDS, exp_minutes)
        raw_mean = rate * exp_minutes
        season_ppm = float(rdetail.get("season_rate_per_minute", 0.0))
        recent_ppm = float(rdetail.get("recent_rate_per_minute", 0.0))

        pace = defense.pace if defense else 100.0
        pace_factor = self._pace_factor(pace)

        if defense:
            pos_factor = self._matchup.positional_defense_factor(defense, player, PropType.REBOUNDS)
        else:
            pos_factor = 1.0

        fpa_factor = self._fpa.factor(defense, player, weight=0.10) if defense else 1.0
        combined_matchup = self._cap_matchup_combined(pos_factor, fpa_factor)
        env = self._environment_multiplier(pace_factor, combined_matchup)

        injury_factor = self._injury_factor(player)
        home_away_factor = 1.0

        projected = raw_mean * env * injury_factor
        projected = apply_projection_guards(projected, player, PropType.REBOUNDS, exp_minutes)

        base = player.rebounds_per_game if player.rebounds_per_game > 0 else (
            3.0 if player.is_starter else 2.0
        )
        projected = clamp(projected, 0.0, min(25.0, self._max_projection(base, player.is_starter)))

        consistency = self._variance.consistency_score(player, PropType.REBOUNDS, projected)
        confidence, _ = self._confidence.score(
            player, PropType.REBOUNDS, projected, consistency,
            edge=0.0, has_defense_data=defense is not None
        )

        return self._build_projection(
            player, game, is_home, projected,
            minutes_factor, 1.0, pace_factor,
            combined_matchup, pos_factor,
            fpa_factor, 1.0, injury_factor, home_away_factor,
            confidence,
            baseline_projection=rate * mpg,
            expected_minutes=exp_minutes,
            environment_multiplier=env,
            season_rate_per_minute=season_ppm,
            recent_rate_per_minute=recent_ppm,
            raw_minute_scaled_mean=raw_mean,
        )
