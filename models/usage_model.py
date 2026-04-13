"""
Usage rate model.

Adjusts a player's effective usage rate for:
- Injury-driven role expansion (key teammate out)
- Player's own injury status
- Game context (blowout = reduced usage in garbage time)
"""

from __future__ import annotations

from domain.constants import TEAMMATE_OUT_USAGE_BOOST
from domain.entities import Game, Player
from domain.enums import InjuryStatus
from utils.math_helpers import clamp


class UsageModel:
    """Projects effective usage rate for a player in a specific game."""

    MAX_USAGE: float = 0.50
    MIN_USAGE: float = 0.05

    def project(
        self,
        player: Player,
        game: Game,
        missing_star_count: int = 0,
    ) -> float:
        """
        Return effective usage fraction (0-1) for *player*.

        Args:
            player: The player entity.
            game: Today's game.
            missing_star_count: Number of key teammates ruled out.
        """
        if player.injury_status == InjuryStatus.OUT:
            return 0.0

        base_usage = player.usage_rate
        if base_usage <= 0:
            base_usage = 0.20  # league average default

        # Boost for missing teammates
        usage = base_usage * (TEAMMATE_OUT_USAGE_BOOST ** missing_star_count)

        # Player's own injury dampener
        if player.injury_status == InjuryStatus.QUESTIONABLE:
            usage *= 0.90
        elif player.injury_status == InjuryStatus.DOUBTFUL:
            usage *= 0.75

        # Blowout garbage-time reduction
        usage *= 1.0 - (game.blowout_risk * 0.05)

        return clamp(usage, self.MIN_USAGE, self.MAX_USAGE)
