"""
Tests for the bankroll / payout calculator.

Covers:
- apply_stake returns correct payout fields
- Kelly fraction suggestion is in valid range
- Batch stake application
- Parlay payout matches manual calculation
"""

import pytest

from domain.entities import Parlay, ParlayLeg
from domain.enums import (
    BookName, ConfidenceTier, PropSide, PropType
)
from engine.bankroll_engine import (
    apply_stake,
    apply_stake_to_all,
    suggested_kelly_stake,
    payout_summary,
)
from engine.slate_scanner import SlateScanner
from engine.parlay_builder import ParlayConstraints, build_parlays
from engine.ranking_engine import rank_parlays
from odds.normalizer import american_to_decimal
from datetime import date


def make_parlay(
    combined_american_odds: int = 350,
    combined_true_prob: float = 0.22,
    combined_implied_prob: float = 0.18,
    n_legs: int = 3,
) -> Parlay:
    """Helper: create a minimal Parlay for payout tests."""
    decimal = american_to_decimal(combined_american_odds)
    combined_edge = combined_true_prob - combined_implied_prob

    leg = ParlayLeg(
        player_id="p1", player_name="Test Player",
        team_abbr="BOS", opponent_abbr="MIA", game_id="g1",
        prop_type=PropType.POINTS, line=24.5, side=PropSide.OVER,
        sportsbook=BookName.FANDUEL, sportsbook_odds=-110,
        projected_value=25.0, true_probability=0.55,
        implied_probability=0.48, edge=0.07, fair_odds=-122,
        confidence=ConfidenceTier.MEDIUM,
    )

    return Parlay(
        parlay_id="test_parlay",
        legs=[leg] * n_legs,
        combined_american_odds=combined_american_odds,
        combined_decimal_odds=decimal,
        combined_implied_probability=combined_implied_prob,
        combined_true_probability=combined_true_prob,
        combined_edge=combined_edge,
        confidence_tier=ConfidenceTier.MEDIUM,
        correlation_risk_score=0.05,
    )


class TestApplyStake:
    def test_stake_fields_populated(self):
        parlay = make_parlay(350)
        apply_stake(parlay, 100.0)
        assert parlay.stake == 100.0
        assert parlay.total_return > 0
        assert parlay.net_profit > 0

    def test_payout_correct(self):
        parlay = make_parlay(350)  # +350 = decimal 4.5
        apply_stake(parlay, 100.0)
        assert abs(parlay.total_return - 450.0) < 0.01
        assert abs(parlay.net_profit - 350.0) < 0.01

    def test_negative_odds_payout(self):
        parlay = make_parlay(-110)  # about decimal 1.909
        apply_stake(parlay, 110.0)
        assert abs(parlay.total_return - 110 * american_to_decimal(-110)) < 0.01

    def test_stake_zero(self):
        parlay = make_parlay(350)
        apply_stake(parlay, 0.0)
        assert parlay.total_return == 0.0
        assert parlay.net_profit == 0.0

    def test_large_stake(self):
        parlay = make_parlay(500)  # +500 = decimal 6.0
        apply_stake(parlay, 1000.0)
        assert abs(parlay.total_return - 6000.0) < 0.01
        assert abs(parlay.net_profit - 5000.0) < 0.01


class TestApplyStakeToAll:
    def test_all_parlays_updated(self):
        parlays = [make_parlay(300 + i * 10) for i in range(5)]
        result = apply_stake_to_all(parlays, 50.0)
        for p in result:
            assert p.stake == 50.0
            assert p.total_return > 0

    def test_returns_same_list(self):
        parlays = [make_parlay()]
        result = apply_stake_to_all(parlays, 100.0)
        assert len(result) == 1


class TestKelly:
    def test_kelly_with_edge(self):
        parlay = make_parlay(350, combined_true_prob=0.25, combined_implied_prob=0.18)
        stake = suggested_kelly_stake(parlay, bankroll=1000.0, kelly_fraction_multiplier=0.25)
        assert stake >= 0

    def test_kelly_no_edge(self):
        # Edge is 0 → Kelly should be ~0
        parlay = make_parlay(350, combined_true_prob=0.18, combined_implied_prob=0.18)
        stake = suggested_kelly_stake(parlay, bankroll=1000.0)
        assert stake <= 5.0  # near zero

    def test_kelly_within_bankroll(self):
        parlay = make_parlay(300, combined_true_prob=0.30)
        stake = suggested_kelly_stake(parlay, bankroll=1000.0)
        assert stake <= 1000.0


class TestPayoutSummary:
    def test_summary_keys(self):
        parlay = make_parlay(350)
        apply_stake(parlay, 100.0)
        summary = payout_summary(parlay)
        required_keys = [
            "parlay_id", "num_legs", "combined_odds", "combined_edge",
            "true_prob", "stake", "total_return", "net_profit",
        ]
        for k in required_keys:
            assert k in summary

    def test_summary_formats(self):
        parlay = make_parlay(350)
        apply_stake(parlay, 100.0)
        summary = payout_summary(parlay)
        assert summary["stake"].startswith("$")
        assert summary["total_return"].startswith("$")
        assert "%" in summary["combined_edge"]


class TestIntegration:
    """End-to-end: scan → parlays → stake."""

    def test_full_pipeline(self):
        scanner = SlateScanner()
        props = scanner.scan(date.today())
        assert len(props) > 0

        constraints = ParlayConstraints(min_edge=0.03, max_legs=3)
        parlays = build_parlays(props, constraints)

        if not parlays:
            pytest.skip("No parlays generated with current sample data")

        ranked = rank_parlays(parlays, top_n=10)
        apply_stake_to_all(ranked, 100.0)

        for p in ranked:
            assert p.stake == 100.0
            assert p.total_return >= 0
            assert p.net_profit >= -100.0  # can't lose more than stake
