"""
Expected minutes model.

Projects the number of minutes a player will play, accounting for:
- Season average minutes
- Role (starter vs bench)
- Injury status
- Back-to-back rest factor
- Blowout risk (garbage time reduction)
"""

from __future__ import annotations

from domain.constants import TEAMMATE_OUT_MINUTES_BOOST
from domain.entities import Game, Player
from domain.enums import InjuryStatus, PlayerRole
from utils.math_helpers import clamp


class MinutesModel:
    """Projects expected minutes for a player in a specific game."""

    # Hard caps by role
    STARTER_MIN_CAP: float = 38.0
    BENCH_MIN_CAP: float = 32.0
    RESERVE_MIN_CAP: float = 22.0

    def project(self, player: Player, game: Game, is_home: bool = True) -> float:
        """
        Return expected minutes for *player* in *game*.

        Returns 0.0 if player is OUT.
        """
        if player.injury_status == InjuryStatus.OUT:
            return 0.0

        base = player.minutes_per_game
        if base <= 0:
            # Infer from role
            base = self._default_minutes(player.role)

        # Injury dampener
        injury_mult = self._injury_mult(player.injury_status)

        # Back-to-back rest reduction
        b2b_mult = 0.94 if (is_home and game.is_back_to_back_home) or (
            not is_home and game.is_back_to_back_away
        ) else 1.0

        # Blowout risk: if game is likely a blowout, stars sit in Q4
        blowout_mult = 1.0 - (game.blowout_risk * 0.08)  # max ~8% reduction

        projected = base * injury_mult * b2b_mult * blowout_mult

        # Cap by role
        cap = self._minutes_cap(player.role)
        return clamp(projected, 0.0, cap)

    @staticmethod
    def _default_minutes(role: PlayerRole) -> float:
        defaults = {
            PlayerRole.STARTER: 32.0,
            PlayerRole.BENCH: 22.0,
            PlayerRole.RESERVE: 12.0,
            PlayerRole.GAME_TIME_DECISION: 28.0,
            PlayerRole.INACTIVE: 0.0,
            PlayerRole.OUT: 0.0,
        }
        return defaults.get(role, 22.0)

    @staticmethod
    def _injury_mult(status: InjuryStatus) -> float:
        multipliers = {
            InjuryStatus.ACTIVE: 1.0,
            InjuryStatus.DAY_TO_DAY: 0.95,
            InjuryStatus.QUESTIONABLE: 0.85,
            InjuryStatus.DOUBTFUL: 0.65,
            InjuryStatus.OUT: 0.0,
            InjuryStatus.SUSPENDED: 0.0,
            InjuryStatus.NOT_WITH_TEAM: 0.0,
        }
        return multipliers.get(status, 1.0)

    @staticmethod
    def _minutes_cap(role: PlayerRole) -> float:
        caps = {
            PlayerRole.STARTER: 40.0,
            PlayerRole.BENCH: 33.0,
            PlayerRole.RESERVE: 24.0,
            PlayerRole.GAME_TIME_DECISION: 38.0,
        }
        return caps.get(role, 35.0)
