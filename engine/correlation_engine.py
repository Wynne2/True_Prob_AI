"""
Correlation Engine.

Scores the correlation risk between any pair of parlay legs.
High correlation means outcomes are not independent, which reduces
the value of a parlay (you are not truly multiplying independent edges).

Rules (from domain/constants.py):
- Same player + same stat group (PRA overlaps) → BLOCKED (1.00)
- Same player + different stats → 0.55
- Same game + same team → 0.40
- Same game + different teams → 0.15
- Different games → 0.05

The engine also applies a diversification bonus when legs span multiple
games, teams, and stat categories.
"""

from __future__ import annotations

import logging
from itertools import combinations

from domain.constants import (
    CORRELATION_BLOCK_THRESHOLD,
    DIFF_GAME_CORR,
    SAME_GAME_DIFF_TEAM_CORR,
    SAME_GAME_SAME_TEAM_CORR,
    SAME_PLAYER_DIFF_STAT_CORR,
    SAME_PLAYER_SAME_STAT_CORR,
)
from domain.entities import PropProbability
from domain.enums import PropType

logger = logging.getLogger(__name__)

# Props that are components of PRA — combining them with PRA is highly correlated
_PRA_COMPONENTS: frozenset[PropType] = frozenset({
    PropType.POINTS,
    PropType.REBOUNDS,
    PropType.ASSISTS,
    PropType.PRA,
})


def pair_correlation(leg_a: PropProbability, leg_b: PropProbability) -> float:
    """
    Return an estimated correlation coefficient ∈ [0, 1] between two legs.

    0 = independent; 1 = perfectly correlated (same outcome).
    """
    # Same player
    if leg_a.player_id == leg_b.player_id:
        # Same prop type
        if leg_a.prop_type == leg_b.prop_type:
            return SAME_PLAYER_SAME_STAT_CORR  # = 1.0

        # PRA component overlap
        if leg_a.prop_type in _PRA_COMPONENTS and leg_b.prop_type in _PRA_COMPONENTS:
            return SAME_PLAYER_SAME_STAT_CORR  # effectively the same

        # Different stats for same player
        return SAME_PLAYER_DIFF_STAT_CORR

    # Different players
    if leg_a.game_id == leg_b.game_id:
        if leg_a.team_abbr == leg_b.team_abbr:
            return SAME_GAME_SAME_TEAM_CORR
        else:
            return SAME_GAME_DIFF_TEAM_CORR

    return DIFF_GAME_CORR


def combo_max_correlation(legs: list[PropProbability]) -> float:
    """Return the maximum pairwise correlation for a set of legs."""
    if len(legs) < 2:
        return 0.0
    return max(pair_correlation(a, b) for a, b in combinations(legs, 2))


def combo_avg_correlation(legs: list[PropProbability]) -> float:
    """Return the average pairwise correlation for a set of legs."""
    if len(legs) < 2:
        return 0.0
    pairs = list(combinations(legs, 2))
    return sum(pair_correlation(a, b) for a, b in pairs) / len(pairs)


def is_blocked(legs: list[PropProbability]) -> bool:
    """
    Return True if any pair of legs is so correlated that the combo
    should be automatically blocked.
    """
    for a, b in combinations(legs, 2):
        if pair_correlation(a, b) >= CORRELATION_BLOCK_THRESHOLD:
            return True
    return False


def diversification_bonus(legs: list[PropProbability]) -> float:
    """
    Return a diversification bonus ∈ [0, 0.20].

    Bonus increases when:
    - Legs span multiple different games.
    - Legs span multiple different teams.
    - Legs cover multiple different stat types.
    """
    games = len({l.game_id for l in legs})
    teams = len({l.team_abbr for l in legs})
    stat_types = len({l.prop_type for l in legs})
    n = len(legs)

    if n == 0:
        return 0.0

    game_div = min(games / n, 1.0) * 0.08
    team_div = min(teams / n, 1.0) * 0.07
    stat_div = min(stat_types / n, 1.0) * 0.05

    return game_div + team_div + stat_div


def correlation_risk_score(legs: list[PropProbability]) -> float:
    """
    Composite correlation risk score for a parlay ∈ [0, 1].

    Higher = more correlated = riskier parlay from an independence standpoint.
    """
    if len(legs) < 2:
        return 0.0
    return min(combo_avg_correlation(legs), 1.0)
