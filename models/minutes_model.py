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

import logging
from typing import Optional

from statistics import mean

from domain.constants import (
    MINUTES_BLEND_WEIGHTS,
    STAR_MINUTES_VS_SEASON_FLOOR,
    STAR_MPG_SEASON_THRESHOLD,
    TEAMMATE_OUT_MINUTES_BOOST,
)
from domain.entities import Game, Player
from domain.enums import InjuryStatus, PlayerRole
from utils.math_helpers import clamp

logger = logging.getLogger(__name__)


class MinutesModel:
    """Projects expected minutes for a player in a specific game."""

    def project(
        self,
        player: Player,
        game: Game,
        is_home: bool = True,
        minutes_vacuum: float = 0.0,
    ) -> float:
        """
        Return expected minutes for *player* in *game*.

        Args:
            minutes_vacuum: Extra minutes available due to teammate injuries
                            (computed by InjuryRedistributionModel). Additive.

        Returns 0.0 if player is OUT.
        """
        if player.injury_status == InjuryStatus.OUT:
            return 0.0

        base = player.minutes_per_game
        if base <= 0:
            base = self._default_minutes(player.role)

        # Explicit season vs L5 vs L10 blend (configurable weights).
        last5 = player.last5_minutes or []
        last10 = player.last10_minutes or []
        s5 = mean(last5[-5:]) if last5 else base
        s10 = mean(last10[-10:]) if len(last10) >= 3 else s5
        w = MINUTES_BLEND_WEIGHTS
        base = (
            w["season_mpg"] * base
            + w["last5_mpg"] * s5
            + w["last10_mpg"] * s10
        )

        # High-minute healthy starters: L5/L10 noise should not sit far below season MPG.
        if (
            player.injury_status == InjuryStatus.ACTIVE
            and player.role == PlayerRole.STARTER
            and player.minutes_per_game >= STAR_MPG_SEASON_THRESHOLD
        ):
            base = max(base, player.minutes_per_game * STAR_MINUTES_VS_SEASON_FLOOR)

        # Playoff: stars and rotation players typically play slightly more condensed minutes.
        if getattr(game, "is_playoff", False) and player.injury_status == InjuryStatus.ACTIVE:
            if player.role == PlayerRole.STARTER:
                base = min(base * 1.04, self._minutes_cap(player.role))
            elif player.role == PlayerRole.BENCH:
                base = min(base * 1.02, self._minutes_cap(player.role))

        # Starter realistic minimum: a STARTER classified as ACTIVE is expected
        # to play meaningful minutes regardless of a depressed season average
        # (which can occur when a player ramps back from injury mid-season).
        # 28 min is a conservative floor for a genuine playoff starter.
        if (
            player.injury_status == InjuryStatus.ACTIVE
            and player.role == PlayerRole.STARTER
            and 0 < base < 28.0
        ):
            base = 28.0

        # Recent-minutes adjustment: if a player's last 5-10 games show
        # substantially more minutes than the current base, blend toward recent.
        # This captures: players returning from injury, playoff intensity, etc.
        # We only blend UP (not down) to avoid collapsing projections for
        # bench players who had a garbage-time spike in one game.
        recent_min_list = player.last10_minutes or player.last5_minutes
        if recent_min_list and base > 0:
            recent_avg_min = sum(recent_min_list) / len(recent_min_list)
            if recent_avg_min > base * 1.10:   # meaningful increase (>10% above base)
                # Blend: 30% base, 70% recent — weighted heavily toward current role
                blended = 0.30 * base + 0.70 * recent_avg_min
                cap = self._minutes_cap(player.role)
                base = min(blended, cap)

        # Injury dampener
        injury_mult = self._injury_mult(player.injury_status)

        # Back-to-back rest reduction (mild: 6% not catastrophic)
        b2b_mult = 0.94 if (is_home and game.is_back_to_back_home) or (
            not is_home and game.is_back_to_back_away
        ) else 1.0

        # Blowout risk: mild cap only — do not collapse star projections
        blowout_mult = 1.0 - (game.blowout_risk * 0.06)  # max ~6% reduction

        projected = base * injury_mult * b2b_mult * blowout_mult

        # Teammate-absence minutes boost (role-aware, from InjuryRedistributionModel)
        if minutes_vacuum > 0:
            projected = projected + minutes_vacuum
            logger.debug(
                "MinutesModel: %s | +%.1f min from teammate absences → projected=%.1f",
                player.name, minutes_vacuum, projected,
            )

        # Cap by role
        cap = self._minutes_cap(player.role)
        projected = clamp(projected, 0.0, cap)

        # Anti-collapse floor: active starters should not drop below 70% of season avg,
        # but the floor must never exceed their season average (prevents bumping players
        # with low season averages UP beyond their own historical baseline).
        if (
            player.injury_status == InjuryStatus.ACTIVE
            and player.role == PlayerRole.STARTER
            and player.minutes_per_game > 0
        ):
            floor = min(
                max(player.minutes_per_game * 0.70, 20.0),
                player.minutes_per_game,   # ceiling: floor cannot exceed season avg
            )
            if projected < floor:
                logger.warning(
                    "MinutesModel: %s projected %.1f min < floor %.1f min — "
                    "overriding to floor (season avg=%.1f)",
                    player.name, projected, floor, player.minutes_per_game,
                )
                projected = floor

        return projected

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
            PlayerRole.BENCH: 26.0,   # bench guards rarely exceed 26 min with a full squad
            PlayerRole.RESERVE: 20.0,
            PlayerRole.GAME_TIME_DECISION: 38.0,
        }
        return caps.get(role, 30.0)
