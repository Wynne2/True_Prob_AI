"""
Statistical distribution helpers.

Thin wrappers around scipy distributions with consistent calling conventions.
Each function takes a projected mean (and shape parameters where needed) and
returns P(stat > line) – the probability the player exceeds the given threshold.

All distributions handle edge cases (zero variance, zero lambda, etc.) safely.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from scipy import stats


# ---------------------------------------------------------------------------
# Normal distribution  (Points, PRA)
# ---------------------------------------------------------------------------

def normal_prob_over(
    mean: float,
    std: float,
    line: float,
    min_std: float = 0.5,
) -> float:
    """
    P(X > line) where X ~ Normal(mean, std).

    Args:
        mean: Projected mean value.
        std: Standard deviation.
        line: The prop threshold (e.g. 24.5 points).
        min_std: Floor on std to avoid degenerate distributions.

    Returns:
        Probability in [0, 1].
    """
    std = max(std, min_std)
    # P(X > line) = 1 - CDF(line) = SF(line)
    return float(stats.norm.sf(line, loc=mean, scale=std))


def normal_prob_under(
    mean: float,
    std: float,
    line: float,
    min_std: float = 0.5,
) -> float:
    """P(X <= line) where X ~ Normal(mean, std)."""
    return 1.0 - normal_prob_over(mean, std, line, min_std)


# ---------------------------------------------------------------------------
# Poisson distribution  (Blocks, Steals)
# ---------------------------------------------------------------------------

def poisson_prob_over(lambda_: float, line: float, min_lambda: float = 0.01) -> float:
    """
    P(X > line) where X ~ Poisson(lambda_).

    For player props line values are typically whole or half-numbers.
    We use P(X >= ceil(line + ε)) to handle half-integer lines.
    """
    lambda_ = max(lambda_, min_lambda)
    threshold = math.floor(line) + 1  # first integer strictly greater than line
    # P(X >= threshold) = 1 - P(X <= threshold - 1) = SF of floor(line)
    return float(stats.poisson.sf(math.floor(line), mu=lambda_))


def poisson_prob_under(lambda_: float, line: float, min_lambda: float = 0.01) -> float:
    """P(X <= line) where X ~ Poisson(lambda_)."""
    return 1.0 - poisson_prob_over(lambda_, line, min_lambda)


# ---------------------------------------------------------------------------
# Negative Binomial distribution  (Rebounds, Assists, Turnovers)
# ---------------------------------------------------------------------------

def _negbinom_params(mean: float, variance: float) -> tuple[float, float]:
    """
    Convert mean and variance to NegBin (r, p) parameterisation used by scipy.

    scipy uses: P(X = k) = C(k+r-1, k) * p^r * (1-p)^k
    where p = r / (r + mean)
    """
    if variance <= mean:
        # Variance must exceed mean for NegBin; fall back to Poisson-like
        variance = mean * 1.05
    r = mean**2 / (variance - mean)
    p = r / (r + mean)
    return r, p


def negbinom_prob_over(
    mean: float,
    variance_inflation: float = 1.3,
    line: float = 0.0,
    min_mean: float = 0.1,
) -> float:
    """
    P(X > line) where X ~ NegativeBinomial(mean, variance).

    Variance is set to mean × variance_inflation (> 1 means overdispersion).

    Args:
        mean: Projected mean.
        variance_inflation: Multiplier on mean to derive variance (>1).
        line: The prop threshold.
        min_mean: Floor on mean.
    """
    mean = max(mean, min_mean)
    variance = mean * variance_inflation
    r, p = _negbinom_params(mean, variance)
    # scipy negbinom: P(X >= k) = sf(k - 1, n, p)
    return float(stats.nbinom.sf(math.floor(line), r, p))


def negbinom_prob_under(
    mean: float,
    variance_inflation: float = 1.3,
    line: float = 0.0,
    min_mean: float = 0.1,
) -> float:
    """P(X <= line) where X ~ NegativeBinomial."""
    return 1.0 - negbinom_prob_over(mean, variance_inflation, line, min_mean)


# ---------------------------------------------------------------------------
# Binomial distribution  (3-pointers made)
# ---------------------------------------------------------------------------

def binomial_prob_over(
    n_attempts: float,
    make_rate: float,
    line: float,
    min_attempts: float = 1.0,
) -> float:
    """
    P(X > line) where X ~ Binomial(n, p).

    Args:
        n_attempts: Expected number of 3-point attempts.
        make_rate: 3-point field goal percentage (0-1 fraction).
        line: The prop threshold (e.g. 2.5 threes).
    """
    n = max(int(round(n_attempts)), int(min_attempts))
    p = max(0.001, min(0.999, make_rate))
    return float(stats.binom.sf(math.floor(line), n, p))


def binomial_prob_under(
    n_attempts: float,
    make_rate: float,
    line: float,
    min_attempts: float = 1.0,
) -> float:
    """P(X <= line) where X ~ Binomial(n, p)."""
    return 1.0 - binomial_prob_over(n_attempts, make_rate, line, min_attempts)


# ---------------------------------------------------------------------------
# Variance estimation from recent game log
# ---------------------------------------------------------------------------

def sample_std(values: list[float], fallback_fraction: float = 0.35) -> float:
    """
    Compute sample standard deviation from a game log.

    Falls back to mean × fallback_fraction if fewer than 3 observations.
    """
    if len(values) >= 3:
        arr = np.array(values, dtype=float)
        std = float(np.std(arr, ddof=1))
        return max(std, 0.1)
    elif values:
        mean = sum(values) / len(values)
        return max(mean * fallback_fraction, 0.1)
    return 1.0


def rolling_mean(values: list[float], window: int) -> float:
    """Return the mean of the last *window* values."""
    if not values:
        return 0.0
    tail = values[-window:]
    return sum(tail) / len(tail)
