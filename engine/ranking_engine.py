"""
Ranking Engine.

Sorts and annotates the generated parlays into the final ranked output.
Also identifies the best parlay per risk profile:
- Highest edge parlay
- Safest parlay (lowest correlation risk + highest confidence)
- Best balanced parlay
- Best odds parlay
"""

from __future__ import annotations

import logging
from typing import Optional

from domain.entities import Parlay
from domain.enums import ConfidenceTier, ParlayRiskProfile, SortField

logger = logging.getLogger(__name__)

# Max results – avoid importing constants dynamically
_MAX_RESULTS = 200

_CONFIDENCE_ORDER = {
    ConfidenceTier.HIGH: 3,
    ConfidenceTier.MEDIUM: 2,
    ConfidenceTier.LOW: 1,
    ConfidenceTier.VERY_LOW: 0,
}


def rank_parlays(
    parlays: list[Parlay],
    sort_by: SortField = SortField.EDGE,
    top_n: int = _MAX_RESULTS,
) -> list[Parlay]:
    """
    Sort parlays by *sort_by* field, assign edge_rank, and return top *top_n*.

    Args:
        parlays: Unsorted parlay list.
        sort_by: Primary sort field.
        top_n: Maximum number of parlays to return.

    Returns:
        Sorted, annotated parlays.
    """
    if not parlays:
        return []

    if sort_by == SortField.EDGE:
        sorted_parlays = sorted(parlays, key=lambda p: p.combined_edge, reverse=True)
    elif sort_by == SortField.CONFIDENCE:
        sorted_parlays = sorted(
            parlays,
            key=lambda p: (
                _CONFIDENCE_ORDER.get(p.confidence_tier, 0),
                p.combined_edge,
            ),
            reverse=True,
        )
    elif sort_by == SortField.COMBINED_ODDS:
        sorted_parlays = sorted(parlays, key=lambda p: p.combined_decimal_odds, reverse=True)
    elif sort_by == SortField.CORRELATION_RISK:
        sorted_parlays = sorted(parlays, key=lambda p: p.correlation_risk_score)
    elif sort_by == SortField.BALANCED_SCORE:
        sorted_parlays = sorted(parlays, key=lambda p: p.balanced_score, reverse=True)
    else:
        sorted_parlays = parlays

    # Assign rank
    for i, parlay in enumerate(sorted_parlays, 1):
        parlay.edge_rank = i

    # Tag top parlays by risk profile
    _tag_risk_profiles(sorted_parlays)

    return sorted_parlays[:top_n]


def _tag_risk_profiles(parlays: list[Parlay]) -> None:
    """Identify and tag the best parlay per risk profile."""
    if not parlays:
        return

    # Highest edge
    best_edge = max(parlays, key=lambda p: p.combined_edge)
    best_edge.risk_profile_tags.append(ParlayRiskProfile.HIGHEST_EDGE.value)

    # Safest: lowest correlation risk, then highest confidence
    safest = min(
        parlays,
        key=lambda p: (
            p.correlation_risk_score,
            -_CONFIDENCE_ORDER.get(p.confidence_tier, 0),
        ),
    )
    safest.risk_profile_tags.append(ParlayRiskProfile.SAFEST.value)

    # Best balanced
    best_balanced = max(parlays, key=lambda p: p.balanced_score)
    best_balanced.risk_profile_tags.append(ParlayRiskProfile.BEST_BALANCED.value)

    # Best odds
    best_odds = max(parlays, key=lambda p: p.combined_decimal_odds)
    best_odds.risk_profile_tags.append(ParlayRiskProfile.BEST_ODDS.value)


def get_best_by_profile(
    parlays: list[Parlay],
    profile: ParlayRiskProfile,
) -> Optional[Parlay]:
    """Return the parlay tagged with *profile*, or None."""
    for p in parlays:
        if profile.value in p.risk_profile_tags:
            return p
    return None


def summary_stats(parlays: list[Parlay]) -> dict:
    """Return aggregate statistics for the parlay universe."""
    if not parlays:
        return {}
    edges = [p.combined_edge for p in parlays]
    return {
        "count": len(parlays),
        "avg_edge": sum(edges) / len(edges),
        "max_edge": max(edges),
        "min_edge": min(edges),
        "avg_legs": sum(p.num_legs for p in parlays) / len(parlays),
        "avg_combined_odds": sum(p.combined_decimal_odds for p in parlays) / len(parlays),
    }
