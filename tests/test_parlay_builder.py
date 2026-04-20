"""
Tests for the parlay builder, correlation engine, and ranking engine.

Covers:
- ParlayConstraints filtering
- build_parlays generates valid parlays
- Correlated legs are blocked
- rank_parlays orders by edge
- Risk profile tags are assigned
- Parlay leg count respects max_legs
"""

import pytest
from datetime import date

from domain.entities import PropProbability, ParlayLeg
from domain.enums import (
    BookName, ConfidenceTier, DistributionType, PropSide, PropType
)
from engine.correlation_engine import (
    pair_correlation,
    is_blocked,
    diversification_bonus,
    correlation_risk_score,
)
from engine.parlay_builder import ParlayConstraints, build_parlays, leg_odds_match_constraints
from engine.ranking_engine import rank_parlays, summary_stats
from engine.slate_scanner import SlateScanner


def make_prop(
    player_id: str,
    player_name: str,
    prop_type: PropType,
    team: str = "BOS",
    opp: str = "MIA",
    game_id: str = "g1",
    edge: float = 0.07,
    true_prob: float = 0.57,
    line: float = 24.5,
    side: PropSide = PropSide.OVER,
    odds: int = -110,
    confidence: ConfidenceTier = ConfidenceTier.MEDIUM,
) -> PropProbability:
    """Helper factory for test PropProbability objects."""
    implied = true_prob - edge
    return PropProbability(
        player_id=player_id,
        player_name=player_name,
        team_abbr=team,
        opponent_abbr=opp,
        game_id=game_id,
        prop_type=prop_type,
        line=line,
        side=side,
        projected_value=line + 0.5,
        true_probability=true_prob,
        implied_probability=implied,
        edge=edge,
        fair_odds=-120,
        sportsbook_odds=odds,
        best_book=BookName.FANDUEL,
        confidence=confidence,
        distribution_type=DistributionType.NORMAL,
        explanation="Test prop",
        all_lines=[],
    )


class TestCorrelationEngine:
    def test_same_player_same_stat_blocked(self):
        a = make_prop("p1", "Player A", PropType.POINTS)
        b = make_prop("p1", "Player A", PropType.POINTS)
        assert pair_correlation(a, b) >= 1.0

    def test_same_player_diff_stat(self):
        # THREES is not a PRA component, so POINTS + THREES → diff-stat correlation
        a = make_prop("p1", "Player A", PropType.POINTS)
        b = make_prop("p1", "Player A", PropType.THREES)
        corr = pair_correlation(a, b)
        assert 0.50 <= corr <= 0.60

    def test_pra_plus_points_blocked(self):
        a = make_prop("p1", "Player A", PropType.PRA)
        b = make_prop("p1", "Player A", PropType.POINTS)
        assert pair_correlation(a, b) >= 1.0

    def test_same_game_same_team(self):
        a = make_prop("p1", "Player A", PropType.POINTS, team="BOS", game_id="g1")
        b = make_prop("p2", "Player B", PropType.POINTS, team="BOS", game_id="g1")
        corr = pair_correlation(a, b)
        assert 0.35 <= corr <= 0.45

    def test_diff_game_low_corr(self):
        a = make_prop("p1", "Player A", PropType.POINTS, game_id="g1")
        b = make_prop("p2", "Player B", PropType.POINTS, game_id="g2")
        corr = pair_correlation(a, b)
        assert corr < 0.10

    def test_is_blocked_same_player(self):
        a = make_prop("p1", "Player A", PropType.POINTS)
        b = make_prop("p1", "Player A", PropType.POINTS)
        assert is_blocked([a, b]) is True

    def test_is_not_blocked_diff_players_diff_games(self):
        a = make_prop("p1", "Player A", PropType.POINTS, game_id="g1")
        b = make_prop("p2", "Player B", PropType.REBOUNDS, game_id="g2")
        assert is_blocked([a, b]) is False

    def test_diversification_bonus_all_diff(self):
        legs = [
            make_prop("p1", "A", PropType.POINTS, team="BOS", game_id="g1"),
            make_prop("p2", "B", PropType.REBOUNDS, team="LAL", game_id="g2"),
            make_prop("p3", "C", PropType.ASSISTS, team="OKC", game_id="g3"),
        ]
        bonus = diversification_bonus(legs)
        assert bonus > 0.10  # should get a meaningful bonus

    def test_diversification_bonus_same_team(self):
        legs = [
            make_prop("p1", "A", PropType.POINTS, team="BOS", game_id="g1"),
            make_prop("p2", "B", PropType.POINTS, team="BOS", game_id="g1"),
        ]
        bonus = diversification_bonus(legs)
        # Less than max possible
        assert bonus < 0.20


class TestLegOddsConstraints:
    def test_favorite_band_excludes_positive_odds(self):
        assert leg_odds_match_constraints(-150, -600, -100) is True
        assert leg_odds_match_constraints(+200, -600, -100) is False

    def test_min_max_order_independent_for_negatives(self):
        assert leg_odds_match_constraints(-200, -600, -100) == leg_odds_match_constraints(
            -200, -100, -600
        )

    def test_default_wide_range_allows_typical_prices(self):
        assert leg_odds_match_constraints(-110, -200, 400) is True
        assert leg_odds_match_constraints(+250, -200, 400) is True


class TestParlayBuilder:
    def _get_sample_props(self):
        scanner = SlateScanner()
        return scanner.scan(date.today())

    def test_build_parlays_basic(self):
        props = self._get_sample_props()
        constraints = ParlayConstraints(min_edge=0.03, max_legs=3, min_legs=2)
        parlays = build_parlays(props, constraints)
        assert isinstance(parlays, list)

    def test_min_edge_filter(self):
        props = self._get_sample_props()
        constraints = ParlayConstraints(min_edge=0.30)  # very high – should find few/none
        parlays = build_parlays(props, constraints)
        for p in parlays:
            for leg in p.legs:
                assert leg.edge >= 0.30

    def test_max_legs_respected(self):
        props = self._get_sample_props()
        constraints = ParlayConstraints(min_edge=0.02, max_legs=2, min_legs=2)
        parlays = build_parlays(props, constraints)
        for p in parlays:
            assert p.num_legs <= 2

    def test_parlay_combined_fields(self):
        props = self._get_sample_props()
        constraints = ParlayConstraints(min_edge=0.03, max_legs=3)
        parlays = build_parlays(props, constraints)
        for p in parlays:
            assert p.combined_decimal_odds > 1.0
            assert 0.0 <= p.combined_true_probability <= 1.0
            assert 0.0 <= p.combined_implied_probability <= 1.0
            assert p.num_legs >= 2

    def test_no_blocked_combos(self):
        """All generated parlays should have no auto-blocked correlation pairs."""
        from engine.correlation_engine import CORRELATION_BLOCK_THRESHOLD, pair_correlation
        from itertools import combinations
        props = self._get_sample_props()
        constraints = ParlayConstraints(min_edge=0.03, max_legs=3)
        parlays = build_parlays(props, constraints)
        for parlay in parlays[:50]:  # spot-check first 50
            legs = parlay.legs
            for a, b in combinations(legs, 2):
                a_prop = make_prop(a.player_id, a.player_name, a.prop_type,
                                   team=a.team_abbr, game_id=a.game_id)
                b_prop = make_prop(b.player_id, b.player_name, b.prop_type,
                                   team=b.team_abbr, game_id=b.game_id)
                assert pair_correlation(a_prop, b_prop) < CORRELATION_BLOCK_THRESHOLD, \
                    f"Blocked correlation found: {a.player_name} {a.prop_type} + {b.player_name} {b.prop_type}"


class TestRankingEngine:
    def _get_parlays(self):
        from engine.slate_scanner import SlateScanner
        scanner = SlateScanner()
        props = scanner.scan(date.today())
        constraints = ParlayConstraints(min_edge=0.02, max_legs=3)
        return build_parlays(props, constraints)

    def test_rank_by_edge(self):
        parlays = self._get_parlays()
        if not parlays:
            pytest.skip("No parlays generated")
        ranked = rank_parlays(parlays)
        for i in range(len(ranked) - 1):
            assert ranked[i].combined_edge >= ranked[i + 1].combined_edge

    def test_edge_ranks_assigned(self):
        parlays = self._get_parlays()
        if not parlays:
            pytest.skip("No parlays generated")
        ranked = rank_parlays(parlays)
        for i, p in enumerate(ranked, 1):
            assert p.edge_rank == i

    def test_risk_profile_tags_assigned(self):
        parlays = self._get_parlays()
        if not parlays:
            pytest.skip("No parlays generated")
        ranked = rank_parlays(parlays)
        all_tags = [tag for p in ranked for tag in p.risk_profile_tags]
        assert len(all_tags) > 0

    def test_summary_stats(self):
        parlays = self._get_parlays()
        if not parlays:
            pytest.skip("No parlays generated")
        stats = summary_stats(parlays)
        assert "count" in stats
        assert "avg_edge" in stats
        assert stats["count"] == len(parlays)
