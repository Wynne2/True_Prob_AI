"""
Implied probability calculation with vig (overround) removal.

Two methods are supported:
1. Simple multiplicative normalisation (most common)
2. Shin (1993) method – more theoretically robust for asymmetric markets

The Shin method is the default because it better accounts for inside
information and is widely used in quantitative sports modelling.
"""

from __future__ import annotations

import math

from domain.constants import SHIN_Z_DEFAULT
from odds.normalizer import american_to_decimal, american_to_raw_implied_prob


# ---------------------------------------------------------------------------
# Raw overround calculation
# ---------------------------------------------------------------------------

def calculate_overround(over_odds: int, under_odds: int) -> float:
    """
    Return the total implied probability for an over/under market.

    Values > 1.0 indicate vig; e.g. 1.048 = 4.8% overround.
    """
    return (
        american_to_raw_implied_prob(over_odds)
        + american_to_raw_implied_prob(under_odds)
    )


# ---------------------------------------------------------------------------
# Simple multiplicative vig removal
# ---------------------------------------------------------------------------

def remove_vig_simple(over_odds: int, under_odds: int) -> tuple[float, float]:
    """
    Remove the bookmaker's margin by proportional normalisation.

    Returns (fair_over_prob, fair_under_prob) that sum to exactly 1.0.
    """
    raw_over = american_to_raw_implied_prob(over_odds)
    raw_under = american_to_raw_implied_prob(under_odds)
    total = raw_over + raw_under
    if total <= 0:
        return 0.5, 0.5
    return raw_over / total, raw_under / total


# ---------------------------------------------------------------------------
# Shin (1993) vig removal
# ---------------------------------------------------------------------------

def _shin_z(raw_over: float, raw_under: float, z: float) -> tuple[float, float]:
    """
    One step of the Shin probability inversion for a two-outcome market.

    The parameter *z* represents the insider-trading fraction (≈ overround).
    This gives a more accurate true probability when books shade one side.

    Reference: Shin (1993) "Measuring the Incidence of Insider Trading in a
    Market for State-Contingent Claims".
    """
    # Overround ε = raw_over + raw_under - 1
    eps = raw_over + raw_under - 1.0
    if eps <= 0:
        # No overround: raw probs are already fair
        return raw_over, raw_under

    # Shin's formula: fair_p_i = (sqrt(z^2 + 4*(1-z)*q_i^2/S) - z) / (2*(1-z))
    # where q_i is the raw implied prob, S = sum of q_i
    s = raw_over + raw_under
    z_eff = min(max(z, 0.001), 0.20)  # clamp to sane range

    def shin_p(q: float) -> float:
        discriminant = z_eff**2 + 4.0 * (1.0 - z_eff) * (q**2) / s
        return (math.sqrt(discriminant) - z_eff) / (2.0 * (1.0 - z_eff))

    p_over = shin_p(raw_over)
    p_under = shin_p(raw_under)

    # Renormalise to ensure sum = 1.0 (small floating-point correction)
    total = p_over + p_under
    return p_over / total, p_under / total


def remove_vig_shin(
    over_odds: int,
    under_odds: int,
    z: float = SHIN_Z_DEFAULT,
) -> tuple[float, float]:
    """
    Remove vig using the Shin method.

    Returns (fair_over_prob, fair_under_prob).

    Args:
        over_odds: American odds for the over.
        under_odds: American odds for the under.
        z: Shin parameter (estimated market overround fraction).
           Defaults to SHIN_Z_DEFAULT from constants.
    """
    raw_over = american_to_raw_implied_prob(over_odds)
    raw_under = american_to_raw_implied_prob(under_odds)
    return _shin_z(raw_over, raw_under, z)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def get_fair_implied_probabilities(
    over_odds: int,
    under_odds: int,
    method: str = "shin",
    shin_z: float = SHIN_Z_DEFAULT,
) -> tuple[float, float]:
    """
    Return vig-removed (fair) probabilities for an over/under market.

    Args:
        over_odds: American odds for the over side.
        under_odds: American odds for the under side.
        method: 'shin' (default) or 'simple'.
        shin_z: Shin parameter (only used when method='shin').

    Returns:
        (fair_over_prob, fair_under_prob) both in [0, 1], summing to 1.0.
    """
    if method == "shin":
        return remove_vig_shin(over_odds, under_odds, shin_z)
    return remove_vig_simple(over_odds, under_odds)


def implied_prob_for_side(
    side: str,
    over_odds: int,
    under_odds: int,
    method: str = "shin",
    shin_z: float = SHIN_Z_DEFAULT,
) -> float:
    """
    Return fair implied probability for just one side ('over' or 'under').
    """
    fair_over, fair_under = get_fair_implied_probabilities(
        over_odds, under_odds, method, shin_z
    )
    return fair_over if side.lower() == "over" else fair_under


def raw_implied_prob_for_side(side: str, over_odds: int, under_odds: int) -> float:
    """
    Return the sportsbook's raw implied probability (vig included) for one side.

    This is the inverse of the posted American odds for that outcome only,
    without normalising the two-way market to sum to 1.
    """
    american = over_odds if side.lower() == "over" else under_odds
    return american_to_raw_implied_prob(american)
