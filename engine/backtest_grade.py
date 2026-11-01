"""
Pregame snapshot grading.

Predictions may only be scored with games that had already started by the
grading clock. Integer lines push when the actual stat equals the line.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional


def grade_prop(actual: float, line: float, side: str) -> str:
    """Return win, loss, or push. Half-point lines do not push on integer stats."""
    if actual == line:
        return "push"
    side_l = side.lower()
    if side_l == "over":
        return "win" if actual > line else "loss"
    if side_l == "under":
        return "win" if actual < line else "loss"
    raise ValueError(f"Unsupported side: {side}")


def game_is_pregame(tip_off: datetime, as_of: datetime) -> bool:
    """True when the tip is still in the future relative to the prediction clock."""
    return tip_off > as_of


def brier_score(probability: float, outcome: int) -> float:
    """outcome is 1 for a win, 0 for a loss. Pushes are excluded by the caller."""
    return (probability - outcome) ** 2


def realized_roi(probability_unused: Optional[float], american_odds: int, result: str) -> Optional[float]:
    """
    Unit-stake ROI. Pushes return 0. Losses return -1.
    Wins return the net decimal profit (not including the stake).
    """
    del probability_unused
    if result == "push":
        return 0.0
    if result == "loss":
        return -1.0
    if result != "win":
        return None
    if american_odds >= 100:
        return american_odds / 100.0
    if american_odds < 0:
        return 100.0 / abs(american_odds)
    return None
