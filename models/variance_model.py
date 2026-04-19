"""
Variance and consistency model.

Estimates the standard deviation of a player's stat line, incorporating:
- Historical game-to-game variance from recent game logs
- Variance inflation constants per prop type
- Consistency scoring (inverse of coefficient of variation)
"""

from __future__ import annotations

from domain.constants import (
    LOW_LINE_THRESHOLD,
    LOW_LINE_VARIANCE_BOOST,
    STD_ABSOLUTE_MIN,
    STD_MIN_FRACTION,
    VARIANCE_INFLATION,
)
from domain.entities import Player
from domain.enums import PropType
from utils.distributions import sample_std
from utils.math_helpers import clamp, safe_ratio


class VarianceModel:
    """Estimates standard deviation and consistency score for a player/stat."""

    def std(
        self,
        player: Player,
        prop_type: PropType,
        projected_mean: float,
        use_recent_form: bool = True,
        prop_line: float | None = None,
    ) -> float:
        """
        Return the estimated standard deviation for *prop_type* for *player*.

        Methodology:
        1. Compute raw std from recent game logs (if available).
        2. Fall back to fraction of mean if insufficient data.
        3. Apply variance inflation factor for the prop type.
        """
        raw_std = self._raw_std(player, prop_type, projected_mean, use_recent_form)
        inflation = VARIANCE_INFLATION.get(prop_type, 1.20)
        inflated = raw_std * inflation

        line_boost = 1.0
        if prop_line is not None and prop_line <= LOW_LINE_THRESHOLD:
            line_boost = max(line_boost, LOW_LINE_VARIANCE_BOOST)
        if prop_type in (PropType.THREES, PropType.BLOCKS, PropType.STEALS):
            if projected_mean < 2.0 or (prop_line is not None and prop_line <= 1.5):
                line_boost = max(line_boost, 1.18)
        inflated *= line_boost
        # Minimum floor: larger of (fraction × mean) and absolute minimum.
        # These wider floors prevent overconfident distributions when the
        # projected mean is far from the prop line.
        min_frac = STD_MIN_FRACTION.get(prop_type, 0.30)
        min_abs  = STD_ABSOLUTE_MIN.get(prop_type, 1.0)
        floor = max(projected_mean * min_frac, min_abs)
        return max(inflated, floor)

    def _raw_std(
        self,
        player: Player,
        prop_type: PropType,
        projected_mean: float,
        use_recent: bool,
    ) -> float:
        """Extract raw std from available game log data."""
        log = self._game_log(player, prop_type)
        if use_recent and len(log) >= 3:
            return sample_std(log)
        # Default: 35% of projected mean (approximate NBA prop volatility)
        return projected_mean * 0.35

    def _game_log(self, player: Player, prop_type: PropType) -> list[float]:
        """Return the most relevant recent game log for *prop_type*."""
        if prop_type == PropType.POINTS:
            return player.last10_points or player.last5_points
        elif prop_type == PropType.REBOUNDS:
            return player.last10_rebounds or player.last5_rebounds
        elif prop_type == PropType.ASSISTS:
            return player.last10_assists or player.last5_assists
        elif prop_type == PropType.THREES:
            return player.last5_threes
        elif prop_type == PropType.PRA:
            # Build PRA from components if available
            pts = player.last10_points or player.last5_points
            reb = player.last10_rebounds or player.last5_rebounds
            ast = player.last10_assists or player.last5_assists
            if pts and reb and ast and len(pts) == len(reb) == len(ast):
                return [p + r + a for p, r, a in zip(pts, reb, ast)]
        return []

    def consistency_score(
        self,
        player: Player,
        prop_type: PropType,
        projected_mean: float,
    ) -> float:
        """
        Return a consistency score in [0, 1].

        score = 1 - clamp(CV, 0, 1)
        where CV = std / mean (coefficient of variation).

        1.0 = perfectly consistent; 0.0 = extremely volatile.
        """
        if projected_mean <= 0:
            return 0.5
        std = self._raw_std(player, prop_type, projected_mean, True)
        cv = safe_ratio(std, projected_mean)
        return clamp(1.0 - cv, 0.0, 1.0)
