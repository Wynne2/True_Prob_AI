"""
Confidence tier model.

Assigns a qualitative confidence tier (HIGH / MEDIUM / LOW / VERY_LOW) to
each prop projection based on:
- Data completeness (how many factors are available)
- Edge magnitude
- Consistency score
- Sample size of recent game logs
"""

from __future__ import annotations

from domain.constants import (
    CONFIDENCE_HIGH_THRESHOLD,
    CONFIDENCE_LOW_THRESHOLD,
    CONFIDENCE_MEDIUM_THRESHOLD,
)
from domain.entities import Player
from domain.enums import ConfidenceTier, PropType
from utils.math_helpers import clamp


class ConfidenceModel:
    """Assigns a ConfidenceTier to a projected prop."""

    def score(
        self,
        player: Player,
        prop_type: PropType,
        projected_mean: float,
        consistency_score: float,
        edge: float,
        has_defense_data: bool = True,
    ) -> tuple[ConfidenceTier, float]:
        """
        Return (ConfidenceTier, raw_confidence_score ∈ [0, 1]).

        The raw score combines:
        - Edge strength (40%)
        - Consistency (30%)
        - Data completeness (20%)
        - Recent form availability (10%)
        """
        edge_score = clamp(abs(edge) / 0.20, 0.0, 1.0) * 0.40

        consistency_component = consistency_score * 0.30

        # Data completeness
        completeness = self._data_completeness(player, prop_type, has_defense_data)
        completeness_component = completeness * 0.20

        # Recent form availability
        form_score = self._form_availability(player, prop_type)
        form_component = form_score * 0.10

        raw = edge_score + consistency_component + completeness_component + form_component
        raw = clamp(raw, 0.0, 1.0)

        if raw >= CONFIDENCE_HIGH_THRESHOLD:
            tier = ConfidenceTier.HIGH
        elif raw >= CONFIDENCE_MEDIUM_THRESHOLD:
            tier = ConfidenceTier.MEDIUM
        elif raw >= CONFIDENCE_LOW_THRESHOLD:
            tier = ConfidenceTier.LOW
        else:
            tier = ConfidenceTier.VERY_LOW

        return tier, raw

    def _data_completeness(
        self,
        player: Player,
        prop_type: PropType,
        has_defense: bool,
    ) -> float:
        """Score how much relevant data is available (0-1)."""
        checks = [
            player.minutes_per_game > 0,
            player.usage_rate > 0,
            has_defense,
            self._base_stat(player, prop_type) > 0,
        ]
        return sum(checks) / len(checks)

    def _form_availability(self, player: Player, prop_type: PropType) -> float:
        """Score recent form data availability (0-1)."""
        short_form = self._recent_form(player, prop_type, 5)
        med_form = self._recent_form(player, prop_type, 10)
        if len(med_form) >= 10:
            return 1.0
        if len(short_form) >= 5:
            return 0.7
        if short_form:
            return 0.3
        return 0.0

    def _base_stat(self, player: Player, prop_type: PropType) -> float:
        mapping = {
            PropType.POINTS: player.points_per_game,
            PropType.REBOUNDS: player.rebounds_per_game,
            PropType.ASSISTS: player.assists_per_game,
            PropType.THREES: player.threes_per_game,
            PropType.PRA: player.points_per_game + player.rebounds_per_game + player.assists_per_game,
            PropType.BLOCKS: player.blocks_per_game,
            PropType.STEALS: player.steals_per_game,
            PropType.TURNOVERS: player.turnovers_per_game,
        }
        return mapping.get(prop_type, 0.0)

    def _recent_form(
        self, player: Player, prop_type: PropType, window: int
    ) -> list[float]:
        if prop_type == PropType.POINTS:
            return (player.last10_points or player.last5_points)[:window]
        if prop_type == PropType.REBOUNDS:
            return (player.last10_rebounds or player.last5_rebounds)[:window]
        if prop_type == PropType.ASSISTS:
            return (player.last10_assists or player.last5_assists)[:window]
        if prop_type == PropType.THREES:
            return player.last5_threes[:window]
        return []
