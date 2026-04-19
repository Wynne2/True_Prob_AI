"""
Points projection model  –  Normal distribution.

Pipeline (multiplicative, no stacked additive form):
  blended per-minute skill rate × expected minutes
  × environment × usage × injury × home/away

Recent “form” lives in ``blended_stat_rate`` (per-minute), not a second form multiplier.
"""

from __future__ import annotations

from typing import Optional

from domain.constants import (
    LOW_USAGE_RATE_THRESHOLD,
    POINTS_ENV_DAMPEN_LOW_USAGE_AND_MINUTES_DOWN,
    POINTS_PER_FGA_EFFICIENCY_CEILING,
)
from domain.entities import Game, Player, StatProjection, TeamDefense
from domain.enums import ConfidenceTier, DistributionType, PropType
from models.base_model import BaseStatModel
from models.confidence_model import ConfidenceModel
from models.fantasy_points_allowed_model import FPAModel
from models.matchup_model import MatchupModel
from models.minutes_model import MinutesModel
from models.projection_audit import audit_points_projection
from models.projection_baseline import blended_stat_rate
from models.projection_guards import apply_projection_guards
from models.usage_model import UsageModel
from models.variance_model import VarianceModel
from utils.math_helpers import clamp


class PointsModel(BaseStatModel):
    """Projects player points using per-minute skill × minutes × bounded multipliers."""

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
        exp_minutes = self._minutes.project(player, game, is_home, minutes_vacuum=player.minutes_vacuum)
        mpg = max(player.minutes_per_game, 1.0)
        minutes_factor = exp_minutes / mpg if mpg > 0 else 1.0

        rate, bdetail = blended_stat_rate(player, PropType.POINTS, exp_minutes)
        season_ppm = float(bdetail.get("season_rate_per_minute", 0.0))
        recent_ppm = float(bdetail.get("recent_rate_per_minute", 0.0))
        raw_mean = rate * exp_minutes

        eff_usage = self._usage.project(player, game)
        usage_factor = eff_usage / player.usage_rate if player.usage_rate > 0 else 1.0

        pace = defense.pace if defense else 100.0
        pace_factor = self._pace_factor(pace)

        if defense:
            def_eff_factor = self._defensive_efficiency_factor(defense.defensive_efficiency)
            pos_factor = self._matchup.positional_defense_factor(defense, player, PropType.POINTS)
        else:
            def_eff_factor = 1.0
            pos_factor = 1.0
        matchup_factor = (def_eff_factor + pos_factor) / 2

        fpa_factor = self._fpa.factor(defense, player) if defense else 1.0
        combined_matchup = self._cap_matchup_combined(matchup_factor, fpa_factor)
        env = self._environment_multiplier(pace_factor, combined_matchup)

        usg = float(player.usage_rate or 0.0)
        if usg > 1.0:
            usg /= 100.0
        if (
            minutes_factor < 1.0 - 1e-6
            and usg > 0
            and usg < LOW_USAGE_RATE_THRESHOLD
        ):
            env = 1.0 + (env - 1.0) * POINTS_ENV_DAMPEN_LOW_USAGE_AND_MINUTES_DOWN

        injury_factor = self._injury_factor(player)
        home_away_factor = self._home_away_factor(is_home, player)

        projected = raw_mean * env * usage_factor * injury_factor * home_away_factor

        if player.field_goal_attempts > 0:
            fga_proxy = player.field_goal_attempts / mpg * exp_minutes
        else:
            fga_proxy = max(3.0, (player.points_per_game / mpg) * exp_minutes / 2.05)

        projected = min(projected, fga_proxy * POINTS_PER_FGA_EFFICIENCY_CEILING)

        projected = apply_projection_guards(projected, player, PropType.POINTS, exp_minutes)

        skill_baseline_ppg = rate * mpg
        audit_flags = audit_points_projection(
            player,
            projected,
            exp_minutes,
            season_ppm,
            recent_ppm,
            env,
            fga_proxy,
        )

        base = player.points_per_game if player.points_per_game > 0 else (
            8.0 if player.is_starter else 4.0
        )
        projected = clamp(projected, 0.0, self._max_projection(base, player.is_starter))

        consistency = self._variance.consistency_score(player, PropType.POINTS, projected)
        confidence, _ = self._confidence.score(
            player, PropType.POINTS, projected, consistency,
            edge=0.0, has_defense_data=defense is not None
        )

        return self._build_projection(
            player, game, is_home, projected,
            minutes_factor, usage_factor, pace_factor,
            combined_matchup, def_eff_factor if defense else 1.0,
            fpa_factor, 1.0, injury_factor, home_away_factor,
            confidence,
            baseline_projection=skill_baseline_ppg,
            expected_minutes=exp_minutes,
            environment_multiplier=env,
            season_rate_per_minute=season_ppm,
            recent_rate_per_minute=recent_ppm,
            raw_minute_scaled_mean=raw_mean,
            expected_field_goal_attempts_proxy=fga_proxy,
            projection_audit_flags=audit_flags,
        )
