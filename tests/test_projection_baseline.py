"""Unit tests for baseline blend and parlay calibration monotonicity."""

from __future__ import annotations

from domain.constants import (
    BASELINE_BLEND_ELITE_ASSISTS,
    BASELINE_BLEND_ELITE_POINTS,
    BASELINE_BLEND_ELITE_REBOUNDS,
    BASELINE_BLEND_WEIGHTS,
    LOW_USAGE_POINTS_MAX_ABOVE_MINUTE_ANCHOR,
    THREES_MAX_ABOVE_MINUTE_SCALED_ANCHOR,
)
from domain.entities import Player
from domain.enums import InjuryStatus, PlayerRole, Position, PropType
from models.projection_baseline import blended_baseline, blended_stat_rate
from models.projection_guards import apply_projection_guards
from odds.parlay_math import (
    parlay_combined_true_probability,
    parlay_combined_true_probability_calibrated,
)


def test_baseline_blend_weights_sum_to_one():
    assert abs(sum(BASELINE_BLEND_WEIGHTS.values()) - 1.0) < 1e-9
    assert abs(sum(BASELINE_BLEND_ELITE_ASSISTS.values()) - 1.0) < 1e-9
    assert abs(sum(BASELINE_BLEND_ELITE_POINTS.values()) - 1.0) < 1e-9
    assert abs(sum(BASELINE_BLEND_ELITE_REBOUNDS.values()) - 1.0) < 1e-9


def test_blended_baseline_is_non_negative():
    p = Player(
        player_id="1",
        name="Test",
        team_id="1",
        team_abbr="BOS",
        position=Position.PG,
        role=PlayerRole.STARTER,
        minutes_per_game=30.0,
        points_per_game=20.0,
        rebounds_per_game=5.0,
        assists_per_game=5.0,
    )
    b, detail = blended_baseline(p, PropType.POINTS, 32.0)
    assert b >= 0
    assert "season_anchor" in detail


def test_questionable_elite_pg_uses_default_blend_not_elite_weights():
    """Non-ACTIVE listings should not get the elite season-heavy baseline blend."""
    base_kw = dict(
        player_id="q",
        name="Q",
        team_id="1",
        team_abbr="DET",
        position=Position.PG,
        role=PlayerRole.STARTER,
        minutes_per_game=34.0,
        points_per_game=24.0,
        rebounds_per_game=6.0,
        assists_per_game=9.9,
    )
    p_act = Player(**base_kw, injury_status=InjuryStatus.ACTIVE)
    p_q = Player(**base_kw, injury_status=InjuryStatus.QUESTIONABLE)
    b_act, d_act = blended_baseline(p_act, PropType.ASSISTS, 31.0)
    b_q, d_q = blended_baseline(p_q, PropType.ASSISTS, 31.0)
    assert d_act["weights"]["season"] > d_q["weights"]["season"]
    assert d_q["weights"] == BASELINE_BLEND_WEIGHTS
    assert not d_q["full_season_anchor"]
    assert d_act["full_season_anchor"]
    assert b_q <= b_act


def test_bench_player_gets_season_anchors_not_just_starters():
    """Soft/hard floors apply to every role (starter vs bench uses config ratios)."""
    bench = Player(
        player_id="b",
        name="Bench",
        team_id="1",
        team_abbr="BOS",
        position=Position.SG,
        role=PlayerRole.BENCH,
        is_starter=False,
        minutes_per_game=22.0,
        points_per_game=11.0,
        last10_points=[3.0] * 10,
        last5_points=[3.0] * 5,
    )
    b, d = blended_baseline(bench, PropType.POINTS, 20.0)
    assert d["full_season_anchor"] is True
    assert b >= 11.0 * 0.75


def test_baseline_independent_of_expected_minutes_pass_through():
    """Baseline skill estimate must not depend on tonight's expected minutes."""
    p = Player(
        player_id="m",
        name="M",
        team_id="1",
        team_abbr="DET",
        position=Position.PG,
        role=PlayerRole.STARTER,
        minutes_per_game=34.0,
        assists_per_game=9.0,
    )
    b_low, d_low = blended_baseline(p, PropType.ASSISTS, 22.0)
    b_high, d_high = blended_baseline(p, PropType.ASSISTS, 36.0)
    assert abs(b_low - b_high) < 1e-6
    assert d_low["expected_minutes_pass_through"] == 22.0
    assert d_high["expected_minutes_pass_through"] == 36.0


def test_elite_assists_baseline_stays_near_season_when_minutes_snapshot_low():
    """Slump floors + season-heavy blend keep APG baseline near season."""
    p = Player(
        player_id="2",
        name="Elite PG",
        team_id="1",
        team_abbr="DET",
        position=Position.PG,
        role=PlayerRole.STARTER,
        minutes_per_game=34.0,
        points_per_game=22.0,
        rebounds_per_game=5.0,
        assists_per_game=9.9,
        last10_assists=[4.0] * 10,
        last5_assists=[4.0] * 5,
    )
    b, detail = blended_baseline(p, PropType.ASSISTS, 28.0)
    assert detail["weights"]["season"] >= 0.6
    assert b >= 9.9 * 0.88


def test_rate_blend_does_not_treat_high_raw_totals_as_efficiency_without_minutes():
    """If minute lists are missing, recent curve falls back to season per-minute rate."""
    p = Player(
        player_id="x",
        name="X",
        team_id="1",
        team_abbr="BOS",
        position=Position.SG,
        role=PlayerRole.BENCH,
        is_starter=False,
        minutes_per_game=24.0,
        points_per_game=10.0,
        last10_points=[20.0, 20.0, 20.0],
        last5_points=[20.0],
    )
    r, d = blended_stat_rate(p, PropType.POINTS, 24.0)
    # Without paired minutes, recent_rate = season_rate → no fake +100% spike
    assert abs(d["recent_rate_per_minute"] - d["season_rate_per_minute"]) < 1e-6
    assert r * 24.0 < 14.0


def test_regression_tristan_da_silva_style_points_clamped_vs_scaled_season():
    """~9.9 PPG, 23 exp min, 26 season mpg, low USG — guard must not allow ~12.9 mean."""
    p = Player(
        player_id="tds",
        name="Low-usage wing",
        team_id="1",
        team_abbr="ORL",
        position=Position.SF,
        role=PlayerRole.BENCH,
        is_starter=False,
        minutes_per_game=26.0,
        points_per_game=9.9,
        usage_rate=0.14,
    )
    exp = 23.0
    ceiling = 9.9 * (exp / 26.0) * LOW_USAGE_POINTS_MAX_ABOVE_MINUTE_ANCHOR
    out = apply_projection_guards(12.9, p, PropType.POINTS, exp)
    assert out <= ceiling + 1e-6
    assert out < 11.5


def test_regression_barnes_like_points_not_star_inflated_at_21_min():
    """Role forward ~9–10 PPG at ~21 exp min should not project like a high-usage wing."""
    from unittest.mock import MagicMock

    from domain.entities import Game, TeamDefense
    from models.points_model import PointsModel

    p = Player(
        player_id="hb",
        name="H Barnes",
        team_id="1",
        team_abbr="TOR",
        position=Position.PF,
        role=PlayerRole.STARTER,
        is_starter=True,
        minutes_per_game=24.5,
        points_per_game=9.5,
        field_goal_attempts=8.0,
        usage_rate=0.16,
    )
    game = Game(
        game_id="g1",
        home_team_id="T2",
        home_team_abbr="OPP",
        away_team_id="1",
        away_team_abbr="TOR",
    )
    defense = TeamDefense(
        team_id="T2",
        team_abbr="OPP",
        pace=100.0,
        defensive_efficiency=113.0,
        pts_allowed_pf=19.5,
        pts_allowed_pg=22.0,
        pts_allowed_sg=20.5,
        pts_allowed_sf=21.5,
        pts_allowed_c=24.0,
    )
    model = PointsModel()
    model._minutes.project = MagicMock(return_value=21.0)
    out = model.project(p, game, defense, is_home=False)
    assert 6.0 < out.projected_value < 12.0


def test_regression_barnes_like_threes_stays_below_inflated_mean_at_21_min():
    """~1.8 3PM / ~26 mpg with ~21 exp min — should not land near 2.3 makes without 3PA surge."""
    from unittest.mock import MagicMock

    from domain.entities import Game, TeamDefense
    from models.threes_model import ThreesModel

    p = Player(
        player_id="hb",
        name="H Barnes",
        team_id="1",
        team_abbr="TOR",
        position=Position.PF,
        role=PlayerRole.STARTER,
        is_starter=True,
        minutes_per_game=25.8,
        threes_per_game=1.8,
        three_point_attempts=5.0,
        three_point_pct=0.36,
        usage_rate=0.16,
    )
    game = Game(
        game_id="g1",
        home_team_id="T2",
        home_team_abbr="OPP",
        away_team_id="1",
        away_team_abbr="TOR",
    )
    defense = TeamDefense(
        team_id="T2",
        team_abbr="OPP",
        pace=100.0,
        defensive_efficiency=113.0,
        threes_allowed_pf=2.4,
        threes_allowed_pg=3.0,
        threes_allowed_sg=2.8,
        threes_allowed_sf=2.6,
        threes_allowed_c=1.9,
    )
    model = ThreesModel()
    model._minutes.project = MagicMock(return_value=21.0)
    out = model.project(p, game, defense, is_home=False)
    assert out.projected_value < 2.22


def test_threes_guard_caps_when_expected_minutes_below_season():
    """Low projected minutes: made-threes mean cannot sit far above minute-scaled season rate."""
    p = Player(
        player_id="t",
        name="Barnes-like",
        team_id="1",
        team_abbr="TOR",
        position=Position.PF,
        role=PlayerRole.STARTER,
        minutes_per_game=25.8,
        threes_per_game=1.8,
        is_starter=True,
    )
    exp = 21.0
    ceiling = 1.8 * (exp / 25.8) * THREES_MAX_ABOVE_MINUTE_SCALED_ANCHOR
    out = apply_projection_guards(2.5, p, PropType.THREES, exp)
    assert out <= ceiling + 1e-9
    assert out < 2.2


def test_correlation_penalty_reduces_vs_naive():
    probs = [0.55, 0.52, 0.50]
    naive = parlay_combined_true_probability(probs)
    adj = parlay_combined_true_probability_calibrated(
        probs,
        legs=[],
        avg_pairwise_correlation=0.35,
        combined_american_odds=300,
    )
    assert adj <= naive + 1e-9
