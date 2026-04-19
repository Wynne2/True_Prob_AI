"""
Assists projection model  –  Negative Binomial distribution.
"""

from __future__ import annotations

from typing import Optional

from domain.constants import ELITE_ASSISTS_SEASON_THRESHOLD
from domain.entities import Game, Player, StatProjection, TeamDefense
from domain.enums import DistributionType, InjuryStatus, PropType
from models.base_model import BaseStatModel
from models.confidence_model import ConfidenceModel
from models.fantasy_points_allowed_model import FPAModel
from models.matchup_model import MatchupModel
from models.minutes_model import MinutesModel
from models.projection_baseline import blended_stat_rate
from models.projection_guards import apply_projection_guards
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
        exp_minutes = self._minutes.project(player, game, is_home, minutes_vacuum=player.minutes_vacuum)
        mpg = max(player.minutes_per_game, 1.0)
        minutes_factor = exp_minutes / mpg if mpg > 0 else 1.0

        rate, adetail = blended_stat_rate(player, PropType.ASSISTS, exp_minutes)
        raw_mean = rate * exp_minutes
        season_ppm = float(adetail.get("season_rate_per_minute", 0.0))
        recent_ppm = float(adetail.get("recent_rate_per_minute", 0.0))

        pace = defense.pace if defense else 100.0
        pace_factor = self._pace_factor(pace)

        if defense:
            pos_factor = self._matchup.positional_defense_factor(defense, player, PropType.ASSISTS)
            tov_factor = clamp(1.0 - (defense.turnovers_forced_per_game - 14.0) * 0.01, 0.90, 1.10)
        else:
            pos_factor = 1.0
            tov_factor = 1.0

        # Elite initiators: dampen generic DvP toward neutral only when healthy (listed ACTIVE).
        if (
            player.injury_status == InjuryStatus.ACTIVE
            and player.assists_per_game >= ELITE_ASSISTS_SEASON_THRESHOLD
            and player.is_starter
        ):
            pos_factor = 1.0 + (pos_factor - 1.0) * 0.52

        fpa_factor = self._fpa.factor(defense, player, weight=0.06) if defense else 1.0
        context = clamp(pos_factor * fpa_factor * tov_factor, 0.88, 1.12)
        env = self._environment_multiplier(pace_factor, context)

        injury_factor = self._injury_factor(player)

        base = player.assists_per_game if player.assists_per_game > 0 else (
            2.5 if player.is_starter else 1.0
        )
        projected = raw_mean * env * injury_factor
        projected = apply_projection_guards(projected, player, PropType.ASSISTS, exp_minutes)
        projected = clamp(projected, 0.0, min(20.0, self._max_projection(base, player.is_starter)))

        consistency = self._variance.consistency_score(player, PropType.ASSISTS, projected)
        confidence, _ = self._confidence.score(
            player, PropType.ASSISTS, projected, consistency,
            edge=0.0, has_defense_data=defense is not None
        )

        return self._build_projection(
            player, game, is_home, projected,
            minutes_factor, 1.0, pace_factor,
            context, pos_factor,
            fpa_factor, 1.0, injury_factor, 1.0,
            confidence,
            baseline_projection=rate * mpg,
            expected_minutes=exp_minutes,
            environment_multiplier=env,
            season_rate_per_minute=season_ppm,
            recent_rate_per_minute=recent_ppm,
            raw_minute_scaled_mean=raw_mean,
        )
