"""
Rebounds projection model  –  Negative Binomial distribution.

Pipeline (minutes-first, conservative):
  blended rebound/min (70% season / 30% recent rate, recent clamped)
    × projected minutes
    × rebound environment (positional boards allowed + mild pace **only** — no FPA)
    × injury × role stability × teammate competition

FPA is **not** applied for rebounds (avoids double-counting with positional DvP).
"""

from __future__ import annotations

from typing import Optional

from domain.entities import Game, Player, StatProjection, TeamDefense
from domain.enums import DistributionType, PropType
from models.base_model import BaseStatModel
from models.confidence_model import ConfidenceModel
from models.matchup_model import MatchupModel
from models.minutes_model import MinutesModel
from models.projection_guards import apply_projection_guards
from models.rebounds_support import (
    blended_rebound_rates,
    rebound_environment_multiplier,
    rebound_negbinom_inflation,
    rebound_pace_multiplier,
    role_stability_factor,
    teammate_competition_factor,
    minutes_volatility_ratio,
)
from models.variance_model import VarianceModel
from utils.math_helpers import clamp


class ReboundsModel(BaseStatModel):
    prop_type = PropType.REBOUNDS
    distribution_type = DistributionType.NEGATIVE_BINOMIAL

    def __init__(self) -> None:
        self._minutes = MinutesModel()
        self._matchup = MatchupModel()
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

        blend_rpm, season_rpm, recent_rpm, _bd = blended_rebound_rates(player, exp_minutes)
        raw_mean = blend_rpm * exp_minutes

        pace = defense.pace if defense else 100.0
        pace_factor = self._pace_factor(pace)

        if defense:
            env = rebound_environment_multiplier(self._matchup, defense, player, pace)
            pos_only = self._matchup.positional_defense_factor(defense, player, PropType.REBOUNDS)
        else:
            env = 1.0
            pos_only = 1.0

        # No FPA for rebounds — single defensive signal is positional rebound allowance + mild pace in *env*.
        fpa_factor = 1.0

        injury_factor = self._injury_factor(player)
        home_away_factor = 1.0
        role_s = role_stability_factor(player, exp_minutes, mpg)
        team_c = teammate_competition_factor(player)

        projected = raw_mean * env * injury_factor * role_s * team_c
        projected = apply_projection_guards(projected, player, PropType.REBOUNDS, exp_minutes)

        base = player.rebounds_per_game if player.rebounds_per_game > 0 else (
            3.0 if player.is_starter else 2.0
        )
        projected = clamp(projected, 0.0, min(25.0, self._max_projection(base, player.is_starter)))

        minute_cv = minutes_volatility_ratio(player)
        negbinom_vi = rebound_negbinom_inflation(minute_cv, exp_minutes, mpg)

        consistency = self._variance.consistency_score(player, PropType.REBOUNDS, projected)
        consistency_adj = consistency * (0.90 if role_s < 0.90 else 1.0) * (0.93 if team_c < 0.96 else 1.0)
        confidence, _ = self._confidence.score(
            player, PropType.REBOUNDS, projected, consistency_adj,
            edge=0.0, has_defense_data=defense is not None
        )

        pace_comp = rebound_pace_multiplier(pace) if defense else 1.0
        model_context = {
            "projected_minutes": round(exp_minutes, 2),
            "season_rebounds_per_minute": round(season_rpm, 4),
            "recent_rebounds_per_minute": round(recent_rpm, 4),
            "blended_rebounds_per_minute": round(blend_rpm, 4),
            "rebound_environment_factor": round(env, 4),
            "role_stability_factor": round(role_s, 4),
            "teammate_competition_factor": round(team_c, 4),
            "positional_rebound_matchup_factor": round(pos_only, 4),
            "pace_component_rebounds": round(pace_comp, 4),
            "minutes_volatility_cv": round(minute_cv, 4),
            "negbinom_variance_inflation": round(negbinom_vi, 3),
            "fpa_factor_applied": 0.0,
            "notes": "Rebounds use positional reb allowed + mild pace in env; FPA omitted to avoid DvP double count.",
        }

        return self._build_projection(
            player, game, is_home, projected,
            minutes_factor, 1.0, pace_factor,
            pos_only, pos_only,
            fpa_factor, 1.0, injury_factor, home_away_factor,
            confidence,
            baseline_projection=blend_rpm * mpg,
            expected_minutes=exp_minutes,
            environment_multiplier=env,
            season_rate_per_minute=season_rpm,
            recent_rate_per_minute=recent_rpm,
            raw_minute_scaled_mean=raw_mean,
            negbinom_variance_inflation=negbinom_vi,
            model_context=model_context,
        )
