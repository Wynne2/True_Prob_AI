"""
Tests for the odds math layer.

Covers:
- American ↔ Decimal conversions
- Raw implied probability
- Vig removal (simple and Shin)
- Fair odds calculation
- Edge calculation
- Parlay combined odds
"""

import pytest
from odds.normalizer import (
    american_to_decimal,
    decimal_to_american,
    american_to_raw_implied_prob,
    combine_decimal_odds,
)
from odds.implied_probability import (
    remove_vig_simple,
    remove_vig_shin,
    get_fair_implied_probabilities,
    raw_implied_prob_for_side,
)
from odds.fair_odds import (
    true_prob_to_decimal_odds,
    true_prob_to_american_odds,
    calculate_edge,
    expected_value,
    kelly_fraction,
)
from odds.parlay_math import (
    parlay_combined_decimal,
    parlay_combined_american,
    parlay_combined_true_probability,
    parlay_combined_edge,
    parlay_payout,
    parlay_profit,
)


class TestAmericanToDecimal:
    def test_minus_110(self):
        d = american_to_decimal(-110)
        assert abs(d - 1.9091) < 0.001

    def test_plus_150(self):
        d = american_to_decimal(150)
        assert abs(d - 2.5) < 0.001

    def test_minus_200(self):
        d = american_to_decimal(-200)
        assert abs(d - 1.5) < 0.001

    def test_plus_100(self):
        d = american_to_decimal(100)
        assert abs(d - 2.0) < 0.001

    def test_invalid_zero_raises(self):
        with pytest.raises(ValueError):
            american_to_decimal(0)


class TestDecimalToAmerican:
    def test_plus_150(self):
        a = decimal_to_american(2.5)
        assert a == 150

    def test_minus_200(self):
        a = decimal_to_american(1.5)
        assert a == -200

    def test_even_money(self):
        a = decimal_to_american(2.0)
        assert a == 100

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            decimal_to_american(0.5)


class TestRawImpliedProb:
    def test_minus_110_near_52pct(self):
        prob = american_to_raw_implied_prob(-110)
        assert abs(prob - 0.5238) < 0.001

    def test_plus_200_near_33pct(self):
        prob = american_to_raw_implied_prob(200)
        assert abs(prob - 0.333) < 0.001

    def test_raw_implied_prob_for_side_uses_posted_side(self):
        p_over = raw_implied_prob_for_side("over", -200, 170)
        p_under = raw_implied_prob_for_side("under", -200, 170)
        assert p_over == pytest.approx(american_to_raw_implied_prob(-200))
        assert p_under == pytest.approx(american_to_raw_implied_prob(170))


class TestVigRemoval:
    def test_simple_sums_to_one(self):
        over, under = remove_vig_simple(-110, -110)
        assert abs(over + under - 1.0) < 1e-9

    def test_simple_symmetric_market(self):
        over, under = remove_vig_simple(-110, -110)
        assert abs(over - 0.5) < 0.01
        assert abs(under - 0.5) < 0.01

    def test_shin_sums_to_one(self):
        over, under = remove_vig_shin(-110, -110)
        assert abs(over + under - 1.0) < 1e-6

    def test_shin_asymmetric(self):
        over, under = remove_vig_shin(-150, +130)
        # Favourite (over at -150) should have higher prob
        assert over > under

    def test_get_fair_probs_default_shin(self):
        over, under = get_fair_implied_probabilities(-110, -110)
        assert abs(over + under - 1.0) < 1e-6

    def test_get_fair_probs_simple(self):
        over, under = get_fair_implied_probabilities(-110, -110, method="simple")
        assert abs(over + under - 1.0) < 1e-6


class TestFairOdds:
    def test_50pct_even_money(self):
        odds = true_prob_to_american_odds(0.5)
        assert abs(odds - 100) <= 2  # ≈ even money

    def test_66pct_minus_200(self):
        odds = true_prob_to_american_odds(0.667)
        # Should be close to -200
        assert odds < 0
        assert -220 <= odds <= -180

    def test_edge_positive(self):
        edge = calculate_edge(0.60, 0.52)
        assert abs(edge - 0.08) < 0.001

    def test_edge_negative(self):
        edge = calculate_edge(0.48, 0.52)
        assert edge < 0

    def test_ev_positive_bet(self):
        ev = expected_value(0.55, 1.909, stake=100)
        assert ev > 0

    def test_kelly_positive(self):
        k = kelly_fraction(0.55, 1.909)
        assert k > 0

    def test_kelly_no_edge(self):
        k = kelly_fraction(0.476, 1.909)  # fair odds for -110
        assert k <= 0.01  # near zero


class TestParlayMath:
    def test_two_leg_combined_decimal(self):
        combined = parlay_combined_decimal([-110, -110])
        # 1.909 × 1.909 ≈ 3.644
        assert abs(combined - 3.644) < 0.05

    def test_three_leg_combined(self):
        combined = parlay_combined_decimal([-110, 150, -120])
        expected = american_to_decimal(-110) * american_to_decimal(150) * american_to_decimal(-120)
        assert abs(combined - expected) < 0.001

    def test_combined_true_prob(self):
        p = parlay_combined_true_probability([0.6, 0.6, 0.6])
        assert abs(p - 0.216) < 0.001

    def test_combined_edge(self):
        edge = parlay_combined_edge(0.216, 0.18)
        assert abs(edge - 0.036) < 0.001

    def test_payout(self):
        payout = parlay_payout(100, 3.644)
        assert abs(payout - 364.4) < 0.1

    def test_profit(self):
        profit = parlay_profit(100, 3.644)
        assert abs(profit - 264.4) < 0.1

    def test_single_leg_unchanged(self):
        combined = parlay_combined_decimal([-110])
        assert abs(combined - american_to_decimal(-110)) < 0.001
