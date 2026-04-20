"""
Abstract base class for all stat projection models.

Every stat model (points, rebounds, assists, etc.) subclasses BaseStatModel
and implements `project()`.  The base class provides shared helpers for
loading context data and applying common adjustment factors.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from typing import Optional

from domain.constants import (
    ENVIRONMENT_MODIFIER_MAX,
    ENVIRONMENT_MODIFIER_MIN,
    FORM_WINDOW_MEDIUM,
    FORM_WINDOW_SHORT,
    HOME_ADVANTAGE_FACTOR,
    AWAY_PENALTY_FACTOR,
    LEAGUE_AVG_DEF_EFF,
    LEAGUE_AVG_PACE,
    PACE_SENSITIVITY,
    PROJECTION_BLEND_WEIGHTS,
)
from domain.entities import Game, Player, StatProjection, TeamDefense
from domain.enums import DistributionType, InjuryStatus, PropType
from utils.math_helpers import clamp, weighted_average

logger = logging.getLogger(__name__)


class BaseStatModel(ABC):
    """
    Abstract projection model.

    Subclasses must:
    - Set `prop_type` class attribute.
    - Set `distribution_type` class attribute.
    - Implement `_base_stat(player)` → float.
    - Implement `_stat_std(player, projected_mean)` → float.
    - Implement `project(player, game, defense, ...)` → StatProjection.
    """

    prop_type: PropType
    distribution_type: DistributionType

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def project(
        self,
        player: Player,
        game: Game,
        defense: Optional[TeamDefense],
        is_home: bool = True,
    ) -> StatProjection:
        """Return a fully computed StatProjection for *player* in *game*."""
        raise NotImplementedError

    @abstractmethod
    def _base_stat(self, player: Player) -> float:
        """Return the season-average baseline for this stat."""
        raise NotImplementedError

    @abstractmethod
    def _stat_std(self, player: Player, projected_mean: float) -> float:
        """Return the estimated standard deviation for this stat."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Shared adjustment helpers
    # ------------------------------------------------------------------

    def _weighted_baseline(
        self,
        season_avg: float,
        last5: list[float],
        last10: list[float],
        player_name: str = "",
        injury_flag: bool = False,
        is_starter: bool = True,
    ) -> float:
        """
        Return a weighted baseline blending season avg with recent form.

        Role-aware weights: bench players are more minutes-volatile than starters,
        so their baseline anchors more heavily to the season average rather than
        trusting short-window hot streaks that are often caused by elevated minutes
        in a handful of games rather than a genuine skill increase.

          Starter : season=50%, last10=32%, last5=18%  |  spike cap ×1.40
          Bench   : season=65%, last10=25%, last5=10%  |  spike cap ×1.25

        The spike cap prevents a recent hot streak (e.g. 34-min blowout game)
        from lifting the baseline beyond a role-appropriate ceiling.
        """
        if season_avg <= 0:
            return season_avg

        if is_starter:
            w_season, w_last10, w_last5 = 0.50, 0.32, 0.18
            spike_cap_ratio = 1.40
        else:
            w_season, w_last10, w_last5 = 0.65, 0.25, 0.10
            spike_cap_ratio = 1.25

        avgs: list[float] = []
        wts: list[float] = []

        avgs.append(season_avg)
        wts.append(w_season)

        if last10:
            avgs.append(sum(last10) / len(last10))
            wts.append(w_last10)

        if last5:
            avgs.append(sum(last5) / len(last5))
            wts.append(w_last5)

        total_wt = sum(wts)
        blended = sum(a * w for a, w in zip(avgs, wts)) / total_wt

        # Anti-spike cap: hot-streak outliers in recent windows (often minutes-driven)
        # must not push the baseline above a role-appropriate ceiling.
        spike_cap = season_avg * spike_cap_ratio
        if blended > spike_cap:
            logger.debug(
                "WeightedBaseline: %s blended=%.2f exceeds spike_cap=%.2f "
                "(season_avg=%.2f × %.2f) — capping",
                player_name or "?", blended, spike_cap, season_avg, spike_cap_ratio,
            )
            blended = spike_cap

        # Anti-collapse guard: slumps should not collapse an uninjured player's
        # projection below 65% of their season average.
        if not injury_flag and season_avg > 5.0 and blended < season_avg * 0.65:
            logger.warning(
                "WeightedBaseline: %s blended=%.2f < 65%% of season_avg=%.2f "
                "without injury flag — overriding to season_avg × 0.70 (%.2f)",
                player_name or "?", blended, season_avg, season_avg * 0.70,
            )
            blended = season_avg * 0.70

        return blended

    def _per36_efficiency_factor(
        self,
        season_avg: float,
        season_mpg: float,
        recent_values: list[float],
        recent_minutes: list[float],
    ) -> float:
        """
        Per-36-minute efficiency form factor.

        Separates genuine efficiency gains from minutes-driven stat inflation:
          - Player plays more minutes (6.6 APG vs 3.9 avg, but in 28 min vs 19)
            → per-36 rates are similar → factor ≈ 1.0  (no efficiency change)
          - Player is genuinely more efficient per minute
            → factor > 1.0 (real improvement)

        Works on both single-element lists (window averages from split dashboards)
        and full per-game arrays (from enriched game logs).

        factor = recent_per_min / season_per_min, clamped to ±15%.

        Falls back to a raw ±10% ratio when no minutes data is available.
        """
        if season_avg <= 0 or season_mpg <= 0:
            return 1.0

        season_per_min = season_avg / season_mpg

        # Build (stat, minutes) pairs — filter DNP-adjacent minutes (<3 min)
        paired: list[tuple[float, float]] = []
        if recent_values and recent_minutes:
            min_len = min(len(recent_values), len(recent_minutes))
            paired = [
                (recent_values[i], recent_minutes[i])
                for i in range(min_len)
                if recent_minutes[i] >= 3.0
            ]

        if not paired:
            # No usable minutes data — tighter fallback using raw ratio ±10%
            if not recent_values:
                return 1.0
            recent_avg = sum(recent_values) / len(recent_values)
            raw_ratio = recent_avg / season_avg if season_avg > 0 else 1.0
            return clamp(raw_ratio, 0.90, 1.10)

        recent_per_min = sum(v / m for v, m in paired) / len(paired)
        if season_per_min <= 0:
            return 1.0

        factor = recent_per_min / season_per_min
        return clamp(factor, 0.85, 1.15)

    def _recent_form_factor(
        self,
        season_avg: float,
        last5: list[float],
        last10: list[float],
    ) -> float:
        """
        Legacy raw-ratio form factor — kept for models without minutes data.

        Prefer _per36_efficiency_factor whenever player minutes are available.
        Clamped to ±10%.
        """
        if season_avg <= 0:
            return 1.0
        avgs = []
        weights = []
        if last5:
            avgs.append(sum(last5) / len(last5))
            weights.append(0.40)
        if last10:
            avgs.append(sum(last10) / len(last10))
            weights.append(0.30)
        avgs.append(season_avg)
        weights.append(0.30)

        blended = weighted_average(avgs, weights)
        factor = blended / season_avg
        return clamp(factor, 0.90, 1.10)

    @staticmethod
    def _environment_multiplier(pace_factor: float, context_factor: float) -> float:
        """
        Single bounded game-environment modifier: geometric mean of pace ×
        matchup/opponent context, clamped globally (default ±10%).
        """
        g = math.sqrt(max(pace_factor, 1e-6) * max(context_factor, 1e-6))
        return clamp(g, ENVIRONMENT_MODIFIER_MIN, ENVIRONMENT_MODIFIER_MAX)

    def _form_factor_tight(
        self,
        season_avg: float,
        season_mpg: float,
        recent_values: list[float],
        recent_minutes: list[float],
    ) -> float:
        """Per-36 form clamped tighter (±5%) when stacked with baseline blend."""
        raw = self._per36_efficiency_factor(
            season_avg, season_mpg, recent_values, recent_minutes,
        )
        return clamp(raw, 0.95, 1.05)

    @staticmethod
    def _cap_matchup_combined(matchup_factor: float, fpa_factor: float) -> float:
        """
        Cap the combined matchup × FPA product to ±15%.

        Both `matchup_factor` (positional defense / defensive efficiency) and
        `fpa_factor` (fantasy points allowed to position) measure opponent defensive
        softness. Multiplying them independently can reach 1.22 × 1.15 = 1.41x —
        a 41% boost from matchup alone, before any form or minutes adjustment.
        Clamping the product prevents correlated signals from double-stacking.
        """
        return clamp(matchup_factor * fpa_factor, 0.85, 1.15)

    @staticmethod
    def _max_projection(base: float, is_starter: bool) -> float:
        """
        Role-aware ceiling on the final projected stat.

        Starters: no cap applied — their season averages are the reliable anchor,
        and legitimate playoff/hot-streak scenarios can push them 30-40% above
        their regular-season baseline. The matchup combined cap (±15%) and the
        per-36 efficiency cap (±15%) together bound the compounding effect.

        Bench: cap at 1.30× season average. Bench players have volatile roles
        and limited-minute averages; raw factor multiplication can easily push
        them to 1.5–1.8× their realistic output (e.g. Daniss Jenkins at 14.8 PPG
        when he averages 8 PPG). The 1.30× ceiling keeps projections grounded.
        """
        if base <= 0 or is_starter:
            return float("inf")  # starters: uncapped (season avg is reliable)
        return base * 1.30  # bench: hard cap at 30% above season average

    def _pace_factor(
        self,
        game_pace: float,
        prop_type: Optional[PropType] = None,
    ) -> float:
        """
        Pace-adjusted multiplier relative to league average.

        factor = (game_pace / league_avg_pace) ^ sensitivity
        """
        if game_pace <= 0:
            game_pace = LEAGUE_AVG_PACE
        pt = prop_type or self.prop_type
        sensitivity = PACE_SENSITIVITY.get(pt, 0.80)
        ratio = game_pace / LEAGUE_AVG_PACE
        return ratio ** sensitivity

    def _defensive_efficiency_factor(
        self,
        opp_def_eff: float,
        prop_type: Optional[PropType] = None,
    ) -> float:
        """
        Opponent defensive efficiency multiplier.

        A higher opponent def eff (more points allowed) → boost for scorer.
        factor = opp_def_eff / league_avg_def_eff

        Clamped to ±20%.
        """
        if opp_def_eff <= 0:
            opp_def_eff = LEAGUE_AVG_DEF_EFF
        raw = opp_def_eff / LEAGUE_AVG_DEF_EFF
        return clamp(raw, 0.80, 1.20)

    def _home_away_factor(self, is_home: bool, player: Player) -> float:
        """Return a home/away split multiplier."""
        season_avg = self._base_stat(player)
        if season_avg <= 0:
            return HOME_ADVANTAGE_FACTOR if is_home else AWAY_PENALTY_FACTOR
        split = player.home_ppg if is_home else player.away_ppg
        if split > 0:
            return clamp(split / season_avg, 0.85, 1.15)
        return HOME_ADVANTAGE_FACTOR if is_home else AWAY_PENALTY_FACTOR

    def _injury_factor(self, player: Player) -> float:
        """
        Minute/usage factor when player or teammates are injured.

        Active player with key teammate out → small boost.
        Questionable player themselves → reduce expectation.
        """
        if player.injury_status == InjuryStatus.QUESTIONABLE:
            return 0.85  # 15% reduction for uncertainty
        if player.injury_status == InjuryStatus.DOUBTFUL:
            return 0.60
        if player.injury_status == InjuryStatus.OUT:
            return 0.0
        return 1.0

    def _build_projection(
        self,
        player: Player,
        game: Game,
        is_home: bool,
        projected_mean: float,
        minutes_factor: float,
        usage_factor: float,
        pace_factor: float,
        matchup_factor: float,
        defense_factor: float,
        fpa_factor: float,
        recent_form_factor: float,
        injury_factor: float,
        home_away_factor: float,
        confidence,
        *,
        baseline_projection: Optional[float] = None,
        expected_minutes: float = 0.0,
        environment_multiplier: float = 1.0,
        season_rate_per_minute: float = 0.0,
        recent_rate_per_minute: float = 0.0,
        raw_minute_scaled_mean: float = 0.0,
        expected_field_goal_attempts_proxy: float = 0.0,
        expected_three_point_attempts_proxy: float = 0.0,
        projection_audit_flags: Optional[list[str]] = None,
        negbinom_variance_inflation: float = 0.0,
        model_context: Optional[dict] = None,
    ) -> StatProjection:
        """Construct and return a StatProjection dataclass."""
        bp = baseline_projection if baseline_projection is not None else projected_mean
        return StatProjection(
            player_id=player.player_id,
            player_name=player.name,
            prop_type=self.prop_type,
            projected_value=max(0.0, projected_mean),
            distribution_type=self.distribution_type,
            dist_mean=max(0.0, projected_mean),
            dist_std=self._stat_std(player, projected_mean),
            minutes_factor=minutes_factor,
            usage_factor=usage_factor,
            pace_factor=pace_factor,
            matchup_factor=matchup_factor,
            defense_factor=defense_factor,
            fpa_factor=fpa_factor,
            recent_form_factor=recent_form_factor,
            injury_factor=injury_factor,
            home_away_factor=home_away_factor,
            confidence=confidence,
            baseline_projection=max(0.0, bp),
            expected_minutes=expected_minutes,
            environment_multiplier=environment_multiplier,
            season_rate_per_minute=season_rate_per_minute,
            recent_rate_per_minute=recent_rate_per_minute,
            raw_minute_scaled_mean=raw_minute_scaled_mean,
            expected_field_goal_attempts_proxy=expected_field_goal_attempts_proxy,
            expected_three_point_attempts_proxy=expected_three_point_attempts_proxy,
            projection_audit_flags=list(projection_audit_flags or []),
            negbinom_variance_inflation=negbinom_variance_inflation,
            model_context=dict(model_context or {}),
        )
