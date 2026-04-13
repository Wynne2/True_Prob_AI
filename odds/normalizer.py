"""
Odds format conversion utilities.

Supports American ↔ Decimal ↔ Fractional conversion with clean
validation and edge-case handling.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# American → Decimal
# ---------------------------------------------------------------------------

def american_to_decimal(american: int) -> float:
    """
    Convert American (moneyline) odds to decimal odds.

    Examples:
        -110 → 1.9091
        +150 → 2.5
        -200 → 1.5
    """
    if american >= 100:
        return (american / 100.0) + 1.0
    elif american <= -100:
        return (100.0 / abs(american)) + 1.0
    else:
        raise ValueError(f"Invalid American odds: {american} (must be >= 100 or <= -100)")


def decimal_to_american(decimal: float) -> int:
    """
    Convert decimal odds to American (moneyline) odds.

    Examples:
        1.9091 → -110
        2.5    → +150
        1.5    → -200
    """
    if decimal <= 1.0:
        raise ValueError(f"Decimal odds must be > 1.0, got {decimal}")
    if decimal >= 2.0:
        return int(round((decimal - 1.0) * 100))
    else:
        return int(round(-100.0 / (decimal - 1.0)))


# ---------------------------------------------------------------------------
# Fractional
# ---------------------------------------------------------------------------

def fractional_to_decimal(numerator: int, denominator: int) -> float:
    """Convert fractional odds (e.g. 10/11) to decimal."""
    if denominator == 0:
        raise ValueError("Fractional odds denominator cannot be zero")
    return (numerator / denominator) + 1.0


def fractional_to_american(numerator: int, denominator: int) -> int:
    """Convert fractional odds to American odds."""
    return decimal_to_american(fractional_to_decimal(numerator, denominator))


# ---------------------------------------------------------------------------
# Probability ↔ Decimal
# ---------------------------------------------------------------------------

def decimal_to_raw_implied_prob(decimal: float) -> float:
    """
    Convert decimal odds to raw (vig-inclusive) implied probability.

    P = 1 / decimal
    """
    if decimal <= 0:
        raise ValueError(f"Decimal odds must be positive, got {decimal}")
    return 1.0 / decimal


def american_to_raw_implied_prob(american: int) -> float:
    """Convert American odds to raw implied probability (vig included)."""
    return decimal_to_raw_implied_prob(american_to_decimal(american))


# ---------------------------------------------------------------------------
# Parlay combined odds
# ---------------------------------------------------------------------------

def combine_decimal_odds(odds_list: list[float]) -> float:
    """Multiply a list of decimal odds to get the combined parlay odds."""
    result = 1.0
    for o in odds_list:
        result *= o
    return result


def combine_american_odds(american_list: list[int]) -> int:
    """Convert a list of American odds to combined parlay American odds."""
    decimal_list = [american_to_decimal(a) for a in american_list]
    combined = combine_decimal_odds(decimal_list)
    return decimal_to_american(combined)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def is_valid_american(odds: int) -> bool:
    """Return True if *odds* is a valid American odds integer."""
    return odds >= 100 or odds <= -100


def clamp_american(odds: int) -> int:
    """Clamp odds to a minimum magnitude of 100 (prevent 0 / invalid)."""
    if -99 <= odds <= 99:
        return -100 if odds <= 0 else 100
    return odds
