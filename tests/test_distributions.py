"""
Tests for the statistical distribution helpers.

Covers:
- Normal distribution P(X > line)
- Poisson distribution P(X > line)
- Negative Binomial P(X > line)
- Binomial P(X > line)
- Sample std / rolling mean helpers
"""

import pytest
from utils.distributions import (
    normal_prob_over,
    normal_prob_under,
    poisson_prob_over,
    poisson_prob_under,
    negbinom_prob_over,
    negbinom_prob_under,
    binomial_prob_over,
    binomial_prob_under,
    sample_std,
    rolling_mean,
)


class TestNormal:
    def test_at_mean_50pct(self):
        p = normal_prob_over(25.0, 4.0, 25.0)
        assert abs(p - 0.5) < 0.01

    def test_well_below_line_low_prob(self):
        p = normal_prob_over(10.0, 2.0, 20.0)
        assert p < 0.01

    def test_well_above_line_high_prob(self):
        p = normal_prob_over(30.0, 2.0, 20.0)
        assert p > 0.99

    def test_over_plus_under_equals_one(self):
        over = normal_prob_over(25.0, 5.0, 22.5)
        under = normal_prob_under(25.0, 5.0, 22.5)
        assert abs(over + under - 1.0) < 1e-9

    def test_zero_std_uses_floor(self):
        p = normal_prob_over(25.0, 0.0, 24.5)
        assert 0.0 <= p <= 1.0


class TestPoisson:
    def test_lambda_1_line_0_high_prob(self):
        # With lambda=1, P(X > 0) = 1 - P(X=0) = 1 - e^-1 ≈ 0.632
        p = poisson_prob_over(1.0, 0)
        assert abs(p - 0.632) < 0.01

    def test_lambda_2_line_1(self):
        # P(X > 1) = P(X >= 2) = 1 - P(X=0) - P(X=1)
        # = 1 - e^-2 - 2*e^-2 ≈ 0.594
        p = poisson_prob_over(2.0, 1)
        assert abs(p - 0.594) < 0.01

    def test_over_plus_under_equals_one(self):
        over = poisson_prob_over(1.5, 1.5)
        under = poisson_prob_under(1.5, 1.5)
        assert abs(over + under - 1.0) < 1e-9

    def test_very_high_lambda_vs_low_line(self):
        p = poisson_prob_over(10.0, 1.0)
        assert p > 0.99

    def test_clamps_prob(self):
        p = poisson_prob_over(0.5, 100)
        assert p >= 0.0


class TestNegBinom:
    def test_reasonable_range(self):
        p = negbinom_prob_over(10.0, line=8.0)
        assert 0.5 < p < 0.95

    def test_under_at_mean(self):
        # P(X <= mean) should be near 50% but slightly above due to overdispersion
        p = negbinom_prob_under(10.0, line=10.0)
        assert 0.4 < p < 0.7

    def test_over_plus_under_near_one(self):
        over = negbinom_prob_over(8.0, line=6.5)
        under = negbinom_prob_under(8.0, line=6.5)
        # Not exact due to discrete nature, but should be close
        assert abs(over + under - 1.0) < 1e-6


class TestBinomial:
    def test_curry_threes(self):
        # Curry: ~12 attempts, 39% make rate, line = 4.5
        p = binomial_prob_over(12, 0.39, 4.5)
        # Should be meaningful probability
        assert 0.3 < p < 0.7

    def test_impossible_line(self):
        p = binomial_prob_over(3, 0.35, 3.5)
        # With n=3, max is 3, so P(X > 3.5) should be very low
        assert p < 0.01

    def test_certain_line(self):
        p = binomial_prob_over(10, 0.50, 0)
        # P(X > 0) = 1 - P(X=0) = 1 - 0.5^10 ≈ 0.999
        assert p > 0.99

    def test_over_plus_under_near_one(self):
        over = binomial_prob_over(8, 0.38, 2.5)
        under = binomial_prob_under(8, 0.38, 2.5)
        assert abs(over + under - 1.0) < 1e-6


class TestHelpers:
    def test_sample_std_known(self):
        std = sample_std([10.0, 10.0, 10.0])
        assert std < 0.5  # near zero

    def test_sample_std_varied(self):
        std = sample_std([5.0, 15.0, 10.0])
        assert std > 3.0

    def test_sample_std_fallback_few_values(self):
        std = sample_std([10.0, 12.0])  # < 3 values → fraction of mean
        assert std > 0

    def test_rolling_mean_full_window(self):
        mean = rolling_mean([10, 20, 30, 40, 50], 3)
        assert abs(mean - 40.0) < 0.001

    def test_rolling_mean_empty(self):
        assert rolling_mean([], 5) == 0.0
