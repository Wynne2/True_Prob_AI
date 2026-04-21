"""
Low-usage points suppression layer (synthetic McBride / Ellis–style profiles).

Real NBA names are for documentation only; fixtures use generic archetypes.
"""

from __future__ import annotations

import pytest

from domain.entities import Game, Player, StatProjection
from domain.enums import DistributionType, InjuryStatus, PlayerRole, Position, PropSide, PropType
from models.points_low_usage_suppression import (
    PointsScorerBucket,
    apply_low_usage_points_suppression,
    cap_over_probability,
    classify_scorer_bucket,
    floor_risk_fga_multiplier,
    suppression_triggers,
)


def _game(playoff: bool = False) -> Game:
    return Game(
        game_id="g1",
        home_team_id="h",
        home_team_abbr="HOM",
        away_team_id="a",
        away_team_abbr="AWY",
        is_playoff=playoff,
    )


def _projection(mean: float, fga: float, tpa: float = 0.0) -> StatProjection:
    return StatProjection(
        player_id="p1",
        player_name="Test",
        prop_type=PropType.POINTS,
        projected_value=mean,
        distribution_type=DistributionType.NORMAL,
        dist_mean=mean,
        dist_std=3.0,
        expected_field_goal_attempts_proxy=fga,
        expected_three_point_attempts_proxy=tpa,
        expected_minutes=22.0,
    )


class TestTriggersAndBuckets:
    def test_suppression_triggers_all_three(self) -> None:
        assert suppression_triggers(10.5, 0.18, 7.4) is True
        assert suppression_triggers(10.51, 0.18, 7.4) is False
        assert suppression_triggers(8.0, 0.19, 7.0) is False
        assert suppression_triggers(8.0, 0.18, 7.5) is False

    def test_primary_bucket_bypasses_suppression(self) -> None:
        """High-usage + volume → primary; suppression inactive even on low line."""
        p = Player(
            player_id="star",
            name="Star",
            team_id="t",
            team_abbr="TST",
            position=Position.SG,
            usage_rate=0.24,
            minutes_per_game=34.0,
            field_goal_attempts=18.0,
            is_starter=True,
        )
        proj = _projection(26.0, 18.0)
        game = _game()
        b, _ = classify_scorer_bucket(p, proj, game)
        assert b == PointsScorerBucket.PRIMARY
        out = apply_low_usage_points_suppression(p, game, proj, 6.5, b)
        assert out.active is False
        assert out.adjusted_mean == pytest.approx(26.0)

    def test_keon_ellis_archetype_mean_shrinks(self) -> None:
        """
        Synthetic 'Keon Ellis over 4.5' style: bench, low USG, low FGA, low line.
        Before: raw mean would drive high over prob; after: adjusted mean lower.
        """
        p = Player(
            player_id="ellis_like",
            name="EllisLike",
            team_id="t",
            team_abbr="SAC",
            position=Position.SG,
            role=PlayerRole.BENCH,
            usage_rate=0.14,
            minutes_per_game=20.0,
            points_per_game=5.5,
            field_goal_attempts=4.5,
            free_throw_attempts=0.4,
            three_point_attempts=3.0,
            is_starter=False,
            last5_points=[2.0, 8.0, 3.0, 9.0, 1.0],
            last5_minutes=[18.0, 22.0, 16.0, 24.0, 17.0],
        )
        raw_mean = 7.2
        proj = _projection(raw_mean, fga=5.0, tpa=3.2)
        game = _game(playoff=True)
        bucket, _ = classify_scorer_bucket(p, proj, game)
        assert bucket == PointsScorerBucket.LOW_USAGE_VOLATILE
        assert suppression_triggers(4.5, 0.14, 5.0) is True
        out = apply_low_usage_points_suppression(p, game, proj, 4.5, bucket)
        assert out.active is True
        assert out.adjusted_mean < raw_mean - 0.15
        assert out.raw_mean == pytest.approx(raw_mean)

    def test_miles_mcbride_archetype_over_cap(self) -> None:
        """Synthetic 'McBride over 6.5': capped over prob when mean barely above line."""
        from utils.distributions import normal_prob_over

        p = Player(
            player_id="mcbride_like",
            name="McBrideLike",
            team_id="t",
            team_abbr="NYK",
            position=Position.PG,
            role=PlayerRole.BENCH,
            usage_rate=0.17,
            minutes_per_game=22.0,
            field_goal_attempts=6.0,
            free_throw_attempts=0.5,
            is_starter=False,
            last5_points=[5.0, 12.0, 4.0, 7.0, 3.0],
            last5_minutes=[20.0, 24.0, 18.0, 22.0, 21.0],
        )
        raw_mean = 8.0
        proj = _projection(raw_mean, fga=6.2)
        game = _game(playoff=True)
        bucket, _ = classify_scorer_bucket(p, proj, game)
        out = apply_low_usage_points_suppression(p, game, proj, 6.5, bucket)
        assert out.active is True
        std = 3.2
        p_over = normal_prob_over(out.adjusted_mean, std, 6.5)
        capped, unc = cap_over_probability(
            p_over, out.adjusted_mean, 6.5, bucket, PropSide.OVER, True,
        )
        assert unc == pytest.approx(p_over)
        assert capped <= unc + 1e-9
        if bucket == PointsScorerBucket.LOW_USAGE_VOLATILE:
            assert capped <= 0.56

    def test_floor_risk_monotone(self) -> None:
        a = floor_risk_fga_multiplier(7.0)
        b = floor_risk_fga_multiplier(5.0)
        assert a >= b
