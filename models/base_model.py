"""
Abstract base class for all stat projection models.

Every stat model (points, rebounds, assists, etc.) subclasses BaseStatModel
and implements `project()`.  The base class provides shared helpers for
loading context data and applying common adjustment factors.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

from domain.constants import (
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

    def _recent_form_factor(
        self,
        season_avg: float,
        last5: list[float],
        last10: list[float],
    ) -> float:
        """
        Blend recent form into a multiplicative adjustment factor.

        Weight: 50% last5, 30% last10, 20% season average.
        Factor = blended_recent / season_avg.
        """
        if season_avg <= 0:
            return 1.0
        avgs = []
        weights = []
        if last5:
            avgs.append(sum(last5) / len(last5))
            weights.append(0.50)
        if last10:
            avgs.append(sum(last10) / len(last10))
            weights.append(0.30)
        avgs.append(season_avg)
        weights.append(0.20)

        blended = weighted_average(avgs, weights)
        factor = blended / season_avg
        # Clamp to ±30% adjustment to prevent outliers dominating
        return clamp(factor, 0.70, 1.30)

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
    ) -> StatProjection:
        """Construct and return a StatProjection dataclass."""
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
        )
