"""
Bankroll / Payout Calculator.

Computes stake, total return, and net profit for any set of parlays
given a user-supplied stake amount.

Supports:
- Single-parlay payout
- Batch payout for a list of parlays
- Kelly fraction suggestion per parlay
"""

from __future__ import annotations

from domain.entities import Parlay
from odds.fair_odds import kelly_fraction
from odds.parlay_math import parlay_payout, parlay_profit


def apply_stake(parlay: Parlay, stake: float) -> Parlay:
    """
    Attach stake-dependent payout fields to *parlay* in-place and return it.

    Args:
        parlay: The parlay to calculate payouts for.
        stake: Dollar amount wagered.

    Returns:
        The same parlay with stake, total_return, and net_profit populated.
    """
    parlay.stake = stake
    parlay.total_return = parlay_payout(stake, parlay.combined_decimal_odds)
    parlay.net_profit = parlay_profit(stake, parlay.combined_decimal_odds)
    return parlay


def apply_stake_to_all(parlays: list[Parlay], stake: float) -> list[Parlay]:
    """Apply *stake* payout to every parlay in the list."""
    return [apply_stake(p, stake) for p in parlays]


def suggested_kelly_stake(
    parlay: Parlay,
    bankroll: float,
    kelly_fraction_multiplier: float = 0.25,  # quarter Kelly by default
) -> float:
    """
    Return the suggested stake size based on the (fractional) Kelly criterion.

    Args:
        parlay: The parlay to compute Kelly for.
        bankroll: Total available bankroll.
        kelly_fraction_multiplier: Fraction of full Kelly to use (default 0.25).

    Returns:
        Suggested stake in dollars.
    """
    full_kelly = kelly_fraction(
        parlay.combined_true_probability,
        parlay.combined_decimal_odds,
    )
    fractional = full_kelly * kelly_fraction_multiplier
    return round(bankroll * fractional, 2)


def payout_summary(parlay: Parlay) -> dict:
    """Return a dict of payout fields for display."""
    return {
        "parlay_id": parlay.parlay_id,
        "num_legs": parlay.num_legs,
        "combined_odds": f"+{parlay.combined_american_odds}" if parlay.combined_american_odds > 0
                         else str(parlay.combined_american_odds),
        "combined_decimal": f"{parlay.combined_decimal_odds:.2f}",
        "combined_edge": f"{parlay.combined_edge * 100:.1f}%",
        "true_prob": f"{parlay.combined_true_probability * 100:.1f}%",
        "implied_prob": f"{parlay.combined_implied_probability * 100:.1f}%",
        "stake": f"${parlay.stake:.2f}",
        "total_return": f"${parlay.total_return:.2f}",
        "net_profit": f"${parlay.net_profit:.2f}",
        "confidence": parlay.confidence_tier.value,
        "correlation_risk": f"{parlay.correlation_risk_score:.2f}",
    }
