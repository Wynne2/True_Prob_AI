"""
3-pointers made projection model  –  Binomial distribution.

Volume-first: expected 3PA from season (and per-minute skill) × minutes, then 3PM = 3PA × 3P%.
No separate “form” multiplier — recent shooting is in blended_stat_rate.
"""

from __future__ import annotations

from typing import Optional

from domain.entities import Game, Player, StatProjection, TeamDefense
from domain.enums import DistributionType, PropType
from models.base_model import BaseStatModel
from models.confidence_model import ConfidenceModel
from models.matchup_model import MatchupModel
from models.opportunity import (
    defense_rating_factor,
    pace_label,
    partial_factor,
    resolve_pace,
    widen_std_for_pace_fallback,
)
from models.minutes_model import MinutesModel
from models.usage_model import UsageModel
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
        self._usage = UsageModel()
        self._matchup = MatchupModel()
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

        missing: list[str] = []
        if player.three_point_pct > 0:
            make_rate = clamp(player.three_point_pct, 0.25, 0.48)
            make_source = "three_point_pct"
        elif player.three_point_attempts > 0 and player.threes_per_game > 0:
            make_rate = clamp(player.threes_per_game / player.three_point_attempts, 0.25, 0.48)
            make_source = "makes_over_attempts"
        else:
            make_rate = 0.355
            make_source = "league_prior"
            missing.append("three_point_pct")

        if player.three_point_attempts > 0:
            season_3pa_rate = player.three_point_attempts / mpg
            attempt_source = "season_3pa"
        else:
            season_3pa_rate = max(0.15, (player.threes_per_game / mpg) / max(make_rate, 0.28))
            attempt_source = "derived_from_makes"
            missing.append("three_point_attempts")

        implied_3pa_from_skill = rate_3pm / max(make_rate, 0.28)
        vacuum = float(getattr(player, "minutes_vacuum", 0.0) or 0.0)
        cap_mult = 1.22 if vacuum >= 2.0 else 1.12
        eff_3pa_rate = min(
            max(season_3pa_rate, implied_3pa_from_skill * 0.94),
            season_3pa_rate * cap_mult,
        )

        season_usage = float(player.usage_rate or 0.0)
        if season_usage > 1.0:
            season_usage /= 100.0
        eff_usage = self._usage.project(player, game)
        usage_factor = eff_usage / season_usage if season_usage > 0 else 1.0

        pace, pace_fallback = resolve_pace(defense)
        pace_factor = self._pace_factor(pace)

        pos_factor = 1.0
        def_eff_factor = 1.0
        if defense:
            pos_factor = self._matchup.positional_defense_factor(defense, player, PropType.THREES)
            def_eff_factor = defense_rating_factor(defense.defensive_efficiency)
        # Threes allowed moves attempt volume. If that slot is empty, a small
        # piece of defensive rating moves make probability instead. FPA is not applied.
        if abs(pos_factor - 1.0) > 1e-6:
            volume_matchup = partial_factor(pos_factor, 0.70)
            make_matchup = 1.0
        else:
            volume_matchup = 1.0
            make_matchup = partial_factor(def_eff_factor, 0.25)
        env = pace_factor * usage_factor * volume_matchup

        proj_3pa = eff_3pa_rate * exp_minutes * env
        injury_factor = self._injury_factor(player)
        adjusted_make = clamp(make_rate * make_matchup, 0.20, 0.55)

        projected = proj_3pa * adjusted_make * injury_factor

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

        context = {
            "mechanism": "three_attempts_x_make_rate",
            "projected_minutes": round(exp_minutes, 2),
            "three_point_attempts_per_minute": round(eff_3pa_rate, 4),
            "expected_three_point_attempts": round(proj_3pa, 3),
            "attempt_source": attempt_source,
            "make_rate": round(make_rate, 4),
            "make_source": make_source,
            "matchup_adjusted_make_rate": round(adjusted_make, 4),
            "usage_factor": round(usage_factor, 4),
            "positional_threes_factor": round(pos_factor, 4),
            "volume_matchup": round(volume_matchup, 4),
            "defensive_efficiency_factor": round(def_eff_factor, 4),
            "pace": round(pace, 2),
            "pace_is_fallback": pace_fallback,
            "fpa_applied": False,
            "missing_fields": missing,
            "projected_threes": round(projected, 3),
            "explanation_lines": [
                f"Minutes projected {exp_minutes:.1f} (season {mpg:.1f}).",
                (
                    f"3PA rate {eff_3pa_rate:.3f}/min from {attempt_source}. "
                    f"Expected attempts tonight {proj_3pa:.2f} "
                    f"(pace ×{pace_factor:.3f}, usage ×{usage_factor:.3f}, "
                    f"positional volume ×{volume_matchup:.3f})."
                ),
                (
                    f"Make rate {make_rate:.3f} ({make_source}). "
                    f"Matchup-adjusted make rate {adjusted_make:.3f}."
                ),
                (
                    f"Opponent threes allowed by position factor {pos_factor:.3f}."
                    if abs(pos_factor - 1.0) > 1e-6
                    else "Positional threes allowed missing or league-average. "
                    f"Defensive rating instead moves make rate (factor {def_eff_factor:.3f})."
                ),
                pace_label(pace, pace_fallback),
                "Fantasy points allowed is not applied. It repeats scoring, including threes.",
                ("Missing: " + ", ".join(missing) + ".") if missing else "Attempt volume and make rate were both used.",
            ],
        }
        stat_proj = self._build_projection(
            player, game, is_home, projected,
            minutes_factor, usage_factor, pace_factor,
            volume_matchup, pos_factor,
            1.0, 1.0, injury_factor, 1.0,
            confidence,
            baseline_projection=skill_baseline_3pm,
            expected_minutes=exp_minutes,
            environment_multiplier=env * make_matchup,
            season_rate_per_minute=rate_3pm,
            recent_rate_per_minute=recent_ppm,
            raw_minute_scaled_mean=rate_3pm * exp_minutes,
            expected_three_point_attempts_proxy=expected_3pa_proxy,
            projection_audit_flags=audit_flags,
            model_context=context,
        )
        # The projection guard can sit below attempts × make rate. Shrink the
        # attempt count so the binomial mean matches the guarded projection.
        if adjusted_make > 0 and projected + 1e-9 < proj_3pa * adjusted_make:
            proj_3pa = projected / adjusted_make
        stat_proj.dist_n = max(1, int(round(proj_3pa)))
        stat_proj.dist_p = clamp(adjusted_make, 0.01, 0.60)
        stat_proj.expected_three_point_attempts_proxy = proj_3pa
        stat_proj.model_context["expected_three_point_attempts"] = round(proj_3pa, 3)
        widen_std_for_pace_fallback(stat_proj, pace_fallback)
        return stat_proj
