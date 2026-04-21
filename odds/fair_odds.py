"""
Fair odds calculation.

Converts a true probability into American and decimal fair odds (i.e. the
odds at which you would break even in the long run with zero vig).
"""

from __future__ import annotations

from odds.normalizer import clamp_american, decimal_to_american


def true_prob_to_decimal_odds(true_prob: float) -> float:
    """
    Convert a true probability to decimal fair odds.

    fair_decimal = 1 / true_prob

    Args:
        true_prob: True probability in (0, 1].

    Returns:
        Decimal odds >= 1.0.
    """
    if true_prob <= 0:
        raise ValueError(f"True probability must be > 0, got {true_prob}")
    if true_prob >= 1:
        return 1.0
    return 1.0 / true_prob


def true_prob_to_american_odds(true_prob: float) -> int:
    """
    Convert a true probability to American fair odds.

    Args:
        true_prob: True probability in (0, 1].

    Returns:
        American odds integer (e.g. -110, +150).
    """
    decimal = true_prob_to_decimal_odds(true_prob)
    return clamp_american(decimal_to_american(decimal))


def calculate_edge(true_prob: float, implied_prob: float) -> float:
    """
    Calculate the betting edge.

    edge = true_prob - implied_prob

    Positive edge means the model believes the bet has positive expected value.

    Args:
        true_prob: Model-derived true probability.
        implied_prob: Sportsbook implied probability for the priced side
            (this codebase uses raw / vig-included implied from American odds).

    Returns:
        Edge as a signed fraction (e.g. 0.07 = 7% edge).
    """
    return true_prob - implied_prob


def expected_value(true_prob: float, decimal_odds: float, stake: float = 1.0) -> float:
    """
    Calculate expected value for a bet.

    EV = (true_prob × (decimal_odds - 1) × stake) - ((1 - true_prob) × stake)
       = stake × (true_prob × decimal_odds - 1)

    Args:
        true_prob: True probability of winning.
        decimal_odds: Decimal odds offered.
        stake: Amount wagered (default 1.0 unit).

    Returns:
        Expected profit per bet (can be negative).
    """
    return stake * (true_prob * decimal_odds - 1.0)


def kelly_fraction(true_prob: float, decimal_odds: float) -> float:
    """
    Full Kelly criterion optimal bet fraction.

    Kelly = (b × p - q) / b
    where b = decimal_odds - 1, p = true_prob, q = 1 - true_prob.

    Returns a fraction in [0, 1].  Negative means don't bet.

    This is the full Kelly; practitioners typically use half or quarter Kelly
    for risk management.
    """
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    p = true_prob
    q = 1.0 - p
    kelly = (b * p - q) / b
    return max(0.0, kelly)
