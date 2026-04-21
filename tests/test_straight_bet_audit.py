"""Tests for straight-bet favorite-band audit table builder."""

import pytest

from domain.enums import BookName, ConfidenceTier, DistributionType, PropSide, PropType
from domain.entities import PropProbability
from engine.straight_bet_audit import (
    build_favorite_band_audit_table,
    pipeline_drop_averages_for_positive_edge_favorites,
    top_uncapped_minus_fair_gap,
)


def _prop(
    odds: int,
    edge: float,
    audit: dict | None,
) -> PropProbability:
    implied = 0.55
    return PropProbability(
        player_id="p1",
        player_name="A",
        team_abbr="BOS",
        opponent_abbr="MIA",
        game_id="g1",
        prop_type=PropType.POINTS,
        line=22.5,
        side=PropSide.OVER,
        projected_value=23.0,
        true_probability=implied + edge,
        implied_probability=implied,
        edge=edge,
        fair_odds=-110,
        sportsbook_odds=odds,
        best_book=BookName.FANDUEL,
        confidence=ConfidenceTier.MEDIUM,
        distribution_type=DistributionType.NORMAL,
        explanation="",
        all_lines=[],
        favorite_band_audit=audit,
    )


def test_audit_table_marks_leg_band_and_min_edge():
    audit = {
        "raw_implied_probability": 0.6,
        "fair_implied_probability": 0.57,
        "raw_projected_mean": 23.0,
        "adjusted_projected_mean": 23.0,
        "uncapped_true_probability": 0.58,
        "step1_tail_before_probability_shrink": 0.58,
        "after_shrink_probability": 0.57,
        "after_completeness_probability": 0.565,
        "true_probability_before_market_calibration": 0.562,
        "true_probability_after_market_calibration": 0.561,
        "final_true_probability": 0.56,
        "final_edge": 0.02,
        "confidence_tier": "medium",
        "market_calibration_warnings": [],
        "gate_warnings": [],
        "projection_audit_flags": [],
        "points_suppression_flags": [],
        "points_suppression_active": False,
    }
    p = _prop(-250, 0.02, audit)
    rows, summary = build_favorite_band_audit_table([p], -600, -220, min_edge=0.05)
    assert len(rows) == 1
    assert rows[0]["filtered_out"] is True
    assert "min_edge" in rows[0]["filter_reason"]
    assert summary["positive_edge_in_band"] == 1
    assert summary["positive_edge_passing_straight_filters"] == 0

    rows2, summary2 = build_favorite_band_audit_table([p], -600, -220, min_edge=0.01)
    assert rows2[0]["filtered_out"] is False
    assert summary2["positive_edge_passing_straight_filters"] == 1


def test_audit_table_leg_odds_outside_ui_band():
    audit = {
        "raw_implied_probability": 0.7,
        "fair_implied_probability": 0.65,
        "raw_projected_mean": 10.0,
        "adjusted_projected_mean": 10.0,
        "uncapped_true_probability": 0.72,
        "step1_tail_before_probability_shrink": 0.72,
        "after_shrink_probability": 0.71,
        "after_completeness_probability": 0.708,
        "true_probability_before_market_calibration": 0.706,
        "true_probability_after_market_calibration": 0.705,
        "final_true_probability": 0.71,
        "final_edge": 0.05,
        "confidence_tier": "high",
        "market_calibration_warnings": [],
        "gate_warnings": [],
        "projection_audit_flags": [],
        "points_suppression_flags": [],
        "points_suppression_active": False,
    }
    p = _prop(-250, 0.05, audit)
    rows, _ = build_favorite_band_audit_table([p], -219, -200, min_edge=0.01)
    assert rows[0]["filtered_out"] is True
    assert "outside UI band" in rows[0]["filter_reason"]


def _audit_body(
    fair: float,
    unc: float,
    s1: float,
    sh: float,
    co: float,
    pre: float,
    post: float,
    fin: float,
    fe: float,
) -> dict:
    return {
        "fair_implied_probability": fair,
        "uncapped_true_probability": unc,
        "after_shrink_probability": sh,
        "after_completeness_probability": co,
        "step1_tail_before_probability_shrink": s1,
        "true_probability_before_market_calibration": pre,
        "true_probability_after_market_calibration": post,
        "final_true_probability": fin,
        "final_edge": fe,
        "raw_implied_probability": 0.6,
        "raw_projected_mean": 1.0,
        "adjusted_projected_mean": 1.0,
        "tail_after_distribution_and_points_cap": s1,
        "probability_shrinkage_factor": 0.8,
        "confidence_tier": "medium",
        "market_calibration_warnings": [],
        "gate_warnings": [],
        "projection_audit_flags": [],
        "points_suppression_flags": [],
        "points_suppression_active": False,
    }


def test_top_uncapped_minus_fair_orders_by_gap():
    hi = _prop(
        -250,
        0.01,
        _audit_body(0.5, 0.9, 0.85, 0.8, 0.75, 0.72, 0.71, 0.7, 0.05),
    )
    lo = _prop(
        -240,
        0.02,
        _audit_body(0.6, 0.75, 0.7, 0.68, 0.66, 0.65, 0.64, 0.63, 0.04),
    )
    rows = top_uncapped_minus_fair_gap([lo, hi], -600, -220, 0.0)
    assert rows[0]["uncapped_minus_fair_gap"] == pytest.approx(0.4)


def test_pipeline_drop_picks_largest_step():
    p = _prop(
        -250,
        0.05,
        _audit_body(0.5, 0.9, 0.9, 0.5, 0.48, 0.47, 0.46, 0.45, 0.01),
    )
    out = pipeline_drop_averages_for_positive_edge_favorites([p])
    assert out["n_positive_edge_in_band"] == 1
    assert out["largest_among_user_steps_1_to_4"] == "1_shrinkage"
