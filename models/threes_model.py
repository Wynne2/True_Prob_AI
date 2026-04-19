"""
3-pointers made projection model  –  Binomial distribution.

Volume-first: expected 3PA from season (and per-minute skill) × minutes, then 3PM = 3PA × 3P%.
No separate “form” multiplier — recent shooting is in blended_stat_rate.
"""

from __future__ import annotations

from typing import Optional

from domain.constants import THREES_ENV_DAMPEN_WHEN_MINUTES_DOWN
from domain.entities import Game, Player, StatProjection, TeamDefense
from domain.enums import DistributionType, PropType
from models.base_model import BaseStatModel
from models.confidence_model import ConfidenceModel
from models.fantasy_points_allowed_model import FPAModel
from models.matchup_model import MatchupModel
from models.minutes_model import MinutesModel
from models.projection_audit import audit_threes_projection
from models.projection_baseline import blended_stat_rate
from models.projection_guards import apply_projection_guards
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
        exp_minutes = self._minutes.project(player, game, is_home, minutes_vacuum=player.minutes_vacuum)
        mpg = max(player.minutes_per_game, 1.0)
        minutes_factor = exp_minutes / mpg if mpg > 0 else 1.0

        rate_3pm, rate_detail = blended_stat_rate(player, PropType.THREES, exp_minutes)
        season_ppm = rate_3pm  # blended 3pm per minute (skill)
        recent_ppm = float(rate_detail.get("recent_rate_per_minute", rate_3pm))

        make_rate = player.three_point_pct or 0.355
        make_rate = clamp(make_rate, 0.25, 0.48)

        if player.three_point_attempts > 0:
            season_3pa_rate = player.three_point_attempts / mpg
        else:
            season_3pa_rate = max(0.15, (player.threes_per_game / mpg) / max(make_rate, 0.28))

        implied_3pa_from_skill = rate_3pm / max(make_rate, 0.28)
        vacuum = float(getattr(player, "minutes_vacuum", 0.0) or 0.0)
        cap_mult = 1.22 if vacuum >= 2.0 else 1.12
        eff_3pa_rate = min(
            max(season_3pa_rate, implied_3pa_from_skill * 0.94),
            season_3pa_rate * cap_mult,
        )

        pace = defense.pace if defense else 100.0
        pace_factor = self._pace_factor(pace)

        pos_factor = 1.0
        fpa_factor = 1.0
        if defense:
            pos_factor = self._matchup.positional_defense_factor(defense, player, PropType.THREES)
            fpa_factor = self._fpa.factor(defense, player, weight=0.08)
        combined_matchup = self._cap_matchup_combined(pos_factor, fpa_factor)
        env = self._environment_multiplier(pace_factor, combined_matchup)
        if minutes_factor < 1.0 - 1e-6:
            env = 1.0 + (env - 1.0) * THREES_ENV_DAMPEN_WHEN_MINUTES_DOWN

        proj_3pa = eff_3pa_rate * exp_minutes * env
        injury_factor = self._injury_factor(player)

        projected = proj_3pa * make_rate * injury_factor

        projected = apply_projection_guards(projected, player, PropType.THREES, exp_minutes)

        base_3pm = player.threes_per_game if player.threes_per_game > 0 else (
            1.5 if player.is_starter else 0.5
        )
        projected = clamp(projected, 0.0, min(12.0, self._max_projection(base_3pm, player.is_starter)))

        expected_3pa_proxy = proj_3pa
        audit_flags = audit_threes_projection(
            player, projected, exp_minutes, expected_3pa_proxy, season_ppm,
        )

        consistency = self._variance.consistency_score(player, PropType.THREES, projected)
        confidence, _ = self._confidence.score(
            player, PropType.THREES, projected, consistency,
            edge=0.0, has_defense_data=defense is not None
        )

        skill_baseline_3pm = rate_3pm * mpg

        stat_proj = self._build_projection(
            player, game, is_home, projected,
            minutes_factor, 1.0, pace_factor,
            combined_matchup, pos_factor,
            fpa_factor, 1.0, injury_factor, 1.0,
            confidence,
            baseline_projection=skill_baseline_3pm,
            expected_minutes=exp_minutes,
            environment_multiplier=env,
            season_rate_per_minute=rate_3pm,
            recent_rate_per_minute=recent_ppm,
            raw_minute_scaled_mean=rate_3pm * exp_minutes,
            expected_three_point_attempts_proxy=expected_3pa_proxy,
            projection_audit_flags=audit_flags,
        )
        stat_proj.dist_n = max(1, int(round(proj_3pa)))
        stat_proj.dist_p = clamp(projected / max(stat_proj.dist_n, 1), 0.01, 0.60)
        return stat_proj
