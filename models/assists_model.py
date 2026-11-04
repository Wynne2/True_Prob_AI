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
from models.matchup_model import MatchupModel
from models.opportunity import (
    ASSIST_CONVERSION_PRIOR,
    blend_opportunity,
    pace_label,
    partial_factor,
    regressed_rate,
    resolve_pace,
    widen_std_for_pace_fallback,
)
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
        season_ppm = float(adetail.get("season_rate_per_minute", 0.0))
        recent_ppm = float(adetail.get("recent_rate_per_minute", 0.0))

        pace, pace_fallback = resolve_pace(defense)
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

        # Assists allowed moves conversion. Turnovers forced is a small extra.
        # Fantasy points allowed is not applied.
        conv_matchup = clamp(
            partial_factor(pos_factor, 0.60) * partial_factor(tov_factor, 0.50),
            0.88,
            1.12,
        )
        env = pace_factor * conv_matchup
        missing: list[str] = []
        if player.potential_assists > 0 and mpg > 0:
            season_ast = player.assists_per_game if player.assists_per_game > 0 else player.potential_assists * ASSIST_CONVERSION_PRIOR
            observed = clamp(season_ast / player.potential_assists, 0.20, 0.90)
            conversion = regressed_rate(observed, ASSIST_CONVERSION_PRIOR)
            pot_per_min = player.potential_assists / mpg
            expected_potential = pot_per_min * exp_minutes * pace_factor
            matchup_conversion = conversion * conv_matchup
            opportunity_mean = expected_potential * matchup_conversion
            rate_prior = rate * exp_minutes * env
            raw_mean = blend_opportunity(opportunity_mean, rate_prior)
        else:
            missing.append("potential_assists")
            pot_per_min = 0.0
            expected_potential = 0.0
            conversion = 0.0
            matchup_conversion = 0.0
            raw_mean = rate * exp_minutes * env

        injury_factor = self._injury_factor(player)

        base = player.assists_per_game if player.assists_per_game > 0 else (
            2.5 if player.is_starter else 1.0
        )
        projected = raw_mean * injury_factor
        projected = apply_projection_guards(projected, player, PropType.ASSISTS, exp_minutes)
        projected = clamp(projected, 0.0, min(20.0, self._max_projection(base, player.is_starter)))

        consistency = self._variance.consistency_score(player, PropType.ASSISTS, projected)
        confidence, _ = self._confidence.score(
            player, PropType.ASSISTS, projected, consistency,
            edge=0.0, has_defense_data=defense is not None
        )

        if player.potential_assists > 0:
            chance_line = (
                f"Potential assists per minute {pot_per_min:.3f}. "
                f"Expected potential assists tonight {expected_potential:.2f} "
                f"(pace ×{pace_factor:.3f})."
            )
            conv_line = (
                f"Historical conversion {conversion:.3f} "
                f"(75% of season assists/potential, 25% prior {ASSIST_CONVERSION_PRIOR:.2f}). "
                f"Matchup-adjusted conversion {matchup_conversion:.3f}."
            )
        else:
            chance_line = "Potential assists missing. Mean is the minute-scaled assist rate."
            conv_line = "Conversion rate not used."
        context = {
            "mechanism": "potential_assists_x_conversion" if not missing else "rate_prior_potential_assists_missing",
            "projected_minutes": round(exp_minutes, 2),
            "potential_assists_per_minute": round(pot_per_min, 4),
            "expected_potential_assists": round(expected_potential, 3),
            "historical_conversion": round(conversion, 4),
            "matchup_adjusted_conversion": round(matchup_conversion, 4),
            "positional_assists_factor": round(pos_factor, 4),
            "turnovers_forced_factor": round(tov_factor, 4),
            "conversion_matchup": round(conv_matchup, 4),
            "pace": round(pace, 2),
            "pace_is_fallback": pace_fallback,
            "projected_assists": round(projected, 3),
            "fpa_applied": False,
            "missing_fields": missing,
            "explanation_lines": [
                f"Minutes projected {exp_minutes:.1f} (season {mpg:.1f}).",
                chance_line,
                conv_line,
                (
                    f"Opponent assists allowed by position factor {pos_factor:.3f} "
                    f"(60% of that gap applied to conversion)."
                ),
                pace_label(pace, pace_fallback, getattr(defense, "pace_source", "") or ""),
                "Fantasy points allowed is not applied. It repeats assists along with points and rebounds.",
                ("Missing: " + ", ".join(missing) + ".") if missing else "Potential assists and conversion were both used.",
            ],
        }
        stat = self._build_projection(
            player, game, is_home, projected,
            minutes_factor, 1.0, pace_factor,
            conv_matchup, pos_factor,
            1.0, 1.0, injury_factor, 1.0,
            confidence,
            baseline_projection=rate * mpg,
            expected_minutes=exp_minutes,
            environment_multiplier=env,
            season_rate_per_minute=season_ppm,
            recent_rate_per_minute=recent_ppm,
            raw_minute_scaled_mean=raw_mean,
            model_context=context,
        )
        widen_std_for_pace_fallback(stat, pace_fallback)
        return stat
