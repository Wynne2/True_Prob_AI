"""
Shared mathematical utility functions.
"""

from __future__ import annotations

from typing import Sequence


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the range [lo, hi]."""
    return max(lo, min(hi, value))


def clamp_probability(p: float) -> float:
    """Clamp to valid probability range [0, 1]."""
    return clamp(p, 0.0, 1.0)


def weighted_average(values: Sequence[float], weights: Sequence[float]) -> float:
    """
    Compute a weighted average.

    Args:
        values: Sequence of numeric values.
        weights: Corresponding weights (need not sum to 1).

    Returns:
        Weighted mean, or 0.0 if total weight is zero.
    """
    total_weight = sum(weights)
    if total_weight == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights)) / total_weight


def safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Return numerator / denominator or *default* if denominator is zero."""
    if denominator == 0:
        return default
    return numerator / denominator


def percent_change(old: float, new: float) -> float:
    """Return (new - old) / old as a fraction.  Returns 0 if old is 0."""
    return safe_ratio(new - old, old)


def linear_interpolate(lo: float, hi: float, t: float) -> float:
    """Linearly interpolate between *lo* and *hi* at position *t* ∈ [0, 1]."""
    return lo + (hi - lo) * clamp(t, 0.0, 1.0)


def softmax(values: Sequence[float]) -> list[float]:
    """Return softmax-normalised weights for *values*."""
    import math
    if not values:
        return []
    max_v = max(values)
    exps = [math.exp(v - max_v) for v in values]
    total = sum(exps)
    return [e / total for e in exps]


def running_z_score(value: float, mean: float, std: float) -> float:
    """Return (value - mean) / std; returns 0 if std is zero."""
    return safe_ratio(value - mean, std)
