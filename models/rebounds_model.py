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
    rebound_negbinom_inflation,
    rebound_pace_multiplier,
    rebound_positional_matchup_only,
    role_stability_factor,
    teammate_competition_factor,
    minutes_volatility_ratio,
)
from models.opportunity import (
    REBOUND_SECURE_PRIOR,
    blend_opportunity,
    pace_label,
    partial_factor,
    regressed_rate,
    resolve_pace,
    widen_std_for_pace_fallback,
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

        pace, pace_fallback = resolve_pace(defense)
        pace_factor = self._pace_factor(pace)
        pace_comp = rebound_pace_multiplier(pace)

        if defense:
            pos_only = rebound_positional_matchup_only(self._matchup, defense, player)
        else:
            pos_only = 1.0
        # Positional boards move the secure rate. Pace moves the chance count.
        # FPA is not applied.
        secure_matchup = partial_factor(pos_only, 0.55)
        env = pace_comp * secure_matchup
        missing: list[str] = []
        if player.rebound_chances > 0 and mpg > 0:
            season_reb = player.rebounds_per_game if player.rebounds_per_game > 0 else player.rebound_chances * REBOUND_SECURE_PRIOR
            observed = clamp(season_reb / player.rebound_chances, 0.15, 0.95)
            secure = regressed_rate(observed, REBOUND_SECURE_PRIOR)
            chances_per_min = player.rebound_chances / mpg
            expected_chances = chances_per_min * exp_minutes * pace_comp
            matchup_secure = secure * secure_matchup
            opportunity_mean = expected_chances * matchup_secure
            rate_prior = blend_rpm * exp_minutes * env
            raw_mean = blend_opportunity(opportunity_mean, rate_prior)
        else:
            missing.append("rebound_chances")
            chances_per_min = 0.0
            expected_chances = 0.0
            secure = 0.0
            matchup_secure = 0.0
            raw_mean = blend_rpm * exp_minutes * env

        fpa_factor = 1.0

        injury_factor = self._injury_factor(player)
        home_away_factor = 1.0
        role_s = role_stability_factor(player, exp_minutes, mpg)
        team_c = teammate_competition_factor(player)

        projected = raw_mean * injury_factor * role_s * team_c
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

        if player.rebound_chances > 0:
            chance_line = (
                f"Rebound chances per minute {chances_per_min:.3f}. "
                f"Expected chances tonight {expected_chances:.2f} (pace ×{pace_comp:.3f})."
            )
            secure_line = (
                f"Secure rate {secure:.3f} "
                f"(75% of season rebounds/chances, 25% prior {REBOUND_SECURE_PRIOR:.2f}). "
                f"Matchup-adjusted secure rate {matchup_secure:.3f}."
            )
        else:
            chance_line = "Rebound chances missing. Mean is the minute-scaled rebound rate."
            secure_line = "Secure rate not used."
        model_context = {
            "mechanism": "rebound_chances_x_secure_rate" if not missing else "rate_prior_rebound_chances_missing",
            "projected_minutes": round(exp_minutes, 2),
            "season_rebounds_per_minute": round(season_rpm, 4),
            "recent_rebounds_per_minute": round(recent_rpm, 4),
            "blended_rebounds_per_minute": round(blend_rpm, 4),
            "rebound_chances_per_minute": round(chances_per_min, 4),
            "expected_rebound_chances": round(expected_chances, 3),
            "secure_rate": round(secure, 4),
            "matchup_adjusted_secure_rate": round(matchup_secure, 4),
            "rebound_environment_factor": round(env, 4),
            "role_stability_factor": round(role_s, 4),
            "teammate_competition_factor": round(team_c, 4),
            "positional_rebound_matchup_factor": round(pos_only, 4),
            "pace_component_rebounds": round(pace_comp, 4),
            "pace": round(pace, 2),
            "pace_is_fallback": pace_fallback,
            "minutes_volatility_cv": round(minute_cv, 4),
            "negbinom_variance_inflation": round(negbinom_vi, 3),
            "fpa_factor_applied": 0.0,
            "fpa_applied": False,
            "missing_fields": missing,
            "projected_rebounds": round(projected, 3),
            "notes": "Rebounds are chances times secure rate. Pace scales chances. Positional boards scale the secure rate. FPA is omitted.",
            "explanation_lines": [
                f"Minutes projected {exp_minutes:.1f} (season {mpg:.1f}).",
                chance_line,
                secure_line,
                (
                    f"Opponent rebounds allowed by position factor {pos_only:.3f} "
                    f"(55% of that gap applied to the secure rate)."
                ),
                pace_label(pace, pace_fallback, getattr(defense, "pace_source", "") or ""),
                f"Role stability ×{role_s:.2f}. Teammate competition ×{team_c:.2f}.",
                "Fantasy points allowed is not applied.",
                ("Missing: " + ", ".join(missing) + ".") if missing else "Rebound chances and secure rate were both used.",
            ],
        }

        stat = self._build_projection(
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
        widen_std_for_pace_fallback(stat, pace_fallback)
        return stat
