"""
Points projection model  –  Normal distribution.

Pipeline (multiplicative, no stacked additive form):
  blended per-minute skill rate × expected minutes
  × environment × usage × injury × home/away

Recent “form” lives in ``blended_stat_rate`` (per-minute), not a second form multiplier.
"""

from __future__ import annotations

from typing import Optional

from domain.constants import POINTS_PER_FGA_EFFICIENCY_CEILING
from domain.entities import Game, Player, StatProjection, TeamDefense
from domain.enums import ConfidenceTier, DistributionType, PropType
from models.base_model import BaseStatModel
from models.confidence_model import ConfidenceModel
from models.matchup_model import MatchupModel
from models.minutes_model import MinutesModel
from models.projection_audit import audit_points_projection
from models.projection_baseline import blended_stat_rate
from models.projection_guards import apply_projection_guards
from models.usage_model import UsageModel
from models.opportunity import (
    LEAGUE_POINTS_PER_FGA,
    blend_opportunity,
    defense_rating_factor,
    pace_label,
    partial_factor,
    regressed_rate,
    resolve_pace,
    widen_std_for_pace_fallback,
)
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

        season_usage = float(player.usage_rate or 0.0)
        if season_usage > 1.0:
            season_usage /= 100.0
        eff_usage = self._usage.project(player, game)
        # Season usage is already inside season FGA. The factor moves volume
        # only when tonight's usage differs (teammate vacancy, injury, blowout).
        usage_factor = eff_usage / season_usage if season_usage > 0 else 1.0

        pace, pace_fallback = resolve_pace(defense)
        pace_factor = self._pace_factor(pace)

        if defense:
            def_eff_factor = defense_rating_factor(defense.defensive_efficiency)
            pos_factor = self._matchup.positional_defense_factor(defense, player, PropType.POINTS)
        else:
            def_eff_factor = 1.0
            pos_factor = 1.0
        # Positional points allowed moves shot volume. Defensive rating moves
        # points per shot. Each is partial so the two defensive signals do not stack.
        # Fantasy points allowed is not applied: it is built from the same stats.
        volume_matchup = partial_factor(pos_factor, 0.55)
        eff_matchup = partial_factor(def_eff_factor, 0.45)
        env = pace_factor * volume_matchup * eff_matchup

        missing: list[str] = []
        if player.field_goal_attempts > 0 and mpg > 0:
            fga_per_min = player.field_goal_attempts / mpg
            observed_ppp = (
                player.points_per_game / player.field_goal_attempts
                if player.points_per_game > 0
                else LEAGUE_POINTS_PER_FGA
            )
            points_per_fga = regressed_rate(
                clamp(observed_ppp, 0.70, POINTS_PER_FGA_EFFICIENCY_CEILING),
                LEAGUE_POINTS_PER_FGA,
                observed_weight=0.80,
            )
            expected_fga = fga_per_min * exp_minutes * pace_factor * usage_factor * volume_matchup
            opportunity_mean = expected_fga * points_per_fga * eff_matchup
            rate_prior = rate * exp_minutes * env
            raw_mean = blend_opportunity(opportunity_mean, rate_prior)
            fga_source = "season_fga"
        else:
            missing.append("field_goal_attempts")
            expected_fga = 0.0
            points_per_fga = 0.0
            raw_mean = rate * exp_minutes * env
            fga_source = "missing"

        injury_factor = self._injury_factor(player)
        home_away_factor = self._home_away_factor(is_home, player)
        projected = raw_mean * injury_factor * home_away_factor

        if expected_fga > 0:
            projected = min(projected, expected_fga * POINTS_PER_FGA_EFFICIENCY_CEILING)

        projected = apply_projection_guards(projected, player, PropType.POINTS, exp_minutes)

        skill_baseline_ppg = rate * mpg
        fga_proxy = expected_fga
        audit_flags = audit_points_projection(
            player,
            projected,
            exp_minutes,
            season_ppm,
            recent_ppm,
            env,
            fga_proxy if fga_proxy > 0 else max(3.0, (player.points_per_game / mpg) * exp_minutes / 2.05),
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

        fga_line = (
            f"Expected FGA {expected_fga:.1f} from {player.field_goal_attempts:.1f} season FGA "
            f"× minutes {exp_minutes:.1f}/{mpg:.1f} × pace ×{pace_factor:.3f} "
            f"× usage ×{usage_factor:.3f} × positional volume ×{volume_matchup:.3f}."
            if fga_source == "season_fga"
            else "Field-goal attempts missing. Mean is the minute-scaled scoring rate, not a shot-volume product."
        )
        ppp_line = (
            f"Points per FGA {points_per_fga:.2f} (80% season points/FGA, 20% league {LEAGUE_POINTS_PER_FGA:.2f}), "
            f"then defensive-rating efficiency ×{eff_matchup:.3f}."
            if fga_source == "season_fga"
            else "Points per FGA not used."
        )
        context = {
            "mechanism": "shot_volume" if fga_source == "season_fga" else "rate_prior_fga_missing",
            "points_raw_projected": float(projected),
            "projected_minutes": round(exp_minutes, 2),
            "expected_fga": round(expected_fga, 3),
            "points_per_fga": round(points_per_fga, 3),
            "usage_factor": round(usage_factor, 4),
            "usage_vacuum": round(float(getattr(player, "usage_vacuum", 1.0) or 1.0), 4),
            "pace": round(pace, 2),
            "pace_is_fallback": pace_fallback,
            "pace_factor": round(pace_factor, 4),
            "positional_points_factor": round(pos_factor, 4),
            "volume_matchup": round(volume_matchup, 4),
            "defensive_efficiency_factor": round(def_eff_factor, 4),
            "efficiency_matchup": round(eff_matchup, 4),
            "fpa_applied": False,
            "missing_fields": missing,
            "explanation_lines": [
                f"Minutes projected {exp_minutes:.1f} (season {mpg:.1f}).",
                fga_line,
                ppp_line,
                pace_label(pace, pace_fallback, getattr(defense, "pace_source", "") or ""),
                (
                    f"Positional points allowed factor {pos_factor:.3f} "
                    f"(applied to volume at 55%: ×{volume_matchup:.3f})."
                ),
                (
                    f"Defensive rating factor {def_eff_factor:.3f} "
                    f"(applied to points per shot at 45%: ×{eff_matchup:.3f})."
                ),
                "Fantasy points allowed is not applied. It repeats points, rebounds, and assists.",
                (
                    "Missing: " + ", ".join(missing) + "."
                    if missing
                    else "Shot volume, usage, pace, and defensive rating were all used."
                ),
            ],
        }
        stat = self._build_projection(
            player, game, is_home, projected,
            minutes_factor, usage_factor, pace_factor,
            volume_matchup, def_eff_factor if defense else 1.0,
            1.0, 1.0, injury_factor, home_away_factor,
            confidence,
            baseline_projection=skill_baseline_ppg,
            expected_minutes=exp_minutes,
            environment_multiplier=env,
            season_rate_per_minute=season_ppm,
            recent_rate_per_minute=recent_ppm,
            raw_minute_scaled_mean=raw_mean,
            expected_field_goal_attempts_proxy=fga_proxy,
            projection_audit_flags=audit_flags,
            model_context=context,
        )
        widen_std_for_pace_fallback(stat, pace_fallback)
        return stat
