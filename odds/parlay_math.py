"""
Parlay mathematical utilities.

Handles combined odds, combined probabilities, combined edge, and
payout calculations for multi-leg parlays.
"""

from __future__ import annotations

from odds.normalizer import (
    american_to_decimal,
    combine_decimal_odds,
    decimal_to_american,
)


# ---------------------------------------------------------------------------
# Combined odds
# ---------------------------------------------------------------------------

def parlay_combined_decimal(leg_american_odds: list[int]) -> float:
    """
    Return combined decimal odds for a parlay given each leg's American odds.

    Combined decimal = product of all leg decimal odds.
    """
    if not leg_american_odds:
        return 1.0
    decimals = [american_to_decimal(o) for o in leg_american_odds]
    return combine_decimal_odds(decimals)


def parlay_combined_american(leg_american_odds: list[int]) -> int:
    """
    Return combined American odds for a parlay.
    """
    combined = parlay_combined_decimal(leg_american_odds)
    return decimal_to_american(combined)


# ---------------------------------------------------------------------------
# Combined probability
# ---------------------------------------------------------------------------

def parlay_combined_true_probability(leg_true_probs: list[float]) -> float:
    """
    Return combined true probability for a parlay assuming leg independence.

    For independent legs: P(parlay) = P(leg1) × P(leg2) × ... × P(legN)

    Note: In practice legs may be correlated; the correlation engine applies
    adjustments separately.  This function returns the naive product.
    """
    result = 1.0
    for p in leg_true_probs:
        result *= max(0.0, min(1.0, p))
    return result


def parlay_combined_implied_probability(leg_implied_probs: list[float]) -> float:
    """
    Return combined implied probability for a parlay (product of implied probs).
    """
    return parlay_combined_true_probability(leg_implied_probs)


def parlay_combined_edge(
    combined_true_prob: float,
    combined_implied_prob: float,
) -> float:
    """
    Combined parlay edge = combined_true_prob - combined_implied_prob.

    A positive value means the combined parlay has positive expected value.
    """
    return combined_true_prob - combined_implied_prob


# ---------------------------------------------------------------------------
# Payout
# ---------------------------------------------------------------------------

def parlay_payout(stake: float, combined_decimal_odds: float) -> float:
    """
    Gross total return (stake + profit) for a winning parlay.

    total_return = stake × combined_decimal_odds
    """
    return stake * combined_decimal_odds


def parlay_profit(stake: float, combined_decimal_odds: float) -> float:
    """
    Net profit (excluding stake) for a winning parlay.

    net_profit = total_return - stake = stake × (decimal_odds - 1)
    """
    return stake * (combined_decimal_odds - 1.0)


def parlay_expected_value(
    stake: float,
    combined_decimal_odds: float,
    combined_true_prob: float,
) -> float:
    """
    Expected value of a parlay bet.

    EV = (true_prob × net_profit) - ((1 - true_prob) × stake)
       = stake × (true_prob × decimal_odds - 1)
    """
    return stake * (combined_true_prob * combined_decimal_odds - 1.0)
