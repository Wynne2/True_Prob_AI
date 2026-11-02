"""
Prediction-market (Kalshi / Polymarket) price helpers.

A binary contract pays $1 if it resolves Yes.  Buying it at ask *p* with a
taker fee of ``rate * p * (1 - p)`` costs ``p + rate * p * (1 - p)`` per $1
payout, so that cost is the side's break-even probability.  Converting it to
American odds lets exchange quotes flow through the same engine as
sportsbook prices.
"""

from __future__ import annotations

from typing import Optional

from odds.normalizer import decimal_to_american

KALSHI_TAKER_FEE_RATE = 0.07

MIN_TRADEABLE_PRICE = 0.01
MAX_TRADEABLE_PRICE = 0.99


def effective_cost(price: float, fee_rate: float = 0.0) -> float:
    """Fee-inclusive cost per $1 payout for buying at *price*."""
    p = float(price)
    return p + max(fee_rate, 0.0) * p * (1.0 - p)


def price_to_american(price: float, fee_rate: float = 0.0) -> Optional[int]:
    """
    Convert a contract ask price (0-1) to fee-inclusive American odds.

    Returns None when the price is outside the tradeable range or the fee
    pushes the cost to $1 or more (no possible profit).
    """
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if not (MIN_TRADEABLE_PRICE <= p <= MAX_TRADEABLE_PRICE):
        return None
    cost = effective_cost(p, fee_rate)
    if cost >= 1.0:
        return None
    return decimal_to_american(1.0 / cost)


def binary_odds_pair(
    yes_ask: Optional[float],
    no_ask: Optional[float],
    fee_rate: float = 0.0,
    max_spread: float = 0.15,
    min_price: float = MIN_TRADEABLE_PRICE,
    max_price: float = MAX_TRADEABLE_PRICE,
) -> Optional[tuple[int, int, float, float]]:
    """
    Price both sides of a binary market.

    *max_spread* is the most the two asks may sum above $1 (the book's
    overround).  Wider books are usually stale or empty and are skipped.
    Asks outside [*min_price*, *max_price*] are treated as untradeable.

    Returns (yes_american, no_american, yes_cost, no_cost) or None when
    either side is untradeable.
    """
    if yes_ask is None or no_ask is None:
        return None
    try:
        y = float(yes_ask)
        n = float(no_ask)
    except (TypeError, ValueError):
        return None
    if not (min_price <= y <= max_price):
        return None
    if not (min_price <= n <= max_price):
        return None
    if (y + n) - 1.0 > max_spread + 1e-9:
        return None
    yes_american = price_to_american(y, fee_rate)
    no_american = price_to_american(n, fee_rate)
    if yes_american is None or no_american is None:
        return None
    return yes_american, no_american, effective_cost(y, fee_rate), effective_cost(n, fee_rate)


def to_float(value: object) -> Optional[float]:
    """Parse API numeric fields that may arrive as strings, numbers, or blanks."""
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
