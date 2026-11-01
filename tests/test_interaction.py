"""Interaction experiment: training-only coefficients, no invented assist channel."""

from datetime import date
from types import SimpleNamespace

from domain.enums import Position
from engine.backtest.experiment import estimate_february_cost
from engine.backtest.interaction import (
    MODEL_D_STRENGTH,
    adjusted_probability,
    blend_toward,
    fit_ridge,
    interaction_features,
    matchup_gaps,
    select_lambda,
)


def test_model_d_keeps_a_quarter_of_the_positional_gap():
    assert blend_toward(0.40, 0.80, MODEL_D_STRENGTH) == 0.50


def test_zero_interaction_does_not_move_the_probability():
    assert adjusted_probability(0.62, 0.0, 0.0, (0.4, -0.2)) == 0.62


def test_volume_signal_is_recovered_without_an_intercept():
    rows = []
    for index in range(30):
        x = (index - 15) / 10
        rows.append({
            "x_volume": x,
            "x_efficiency": 0.0,
            "outcome": 0,
            "p_a": 0.0,
        })
        rows[-1]["outcome"] = 0.2 * x
        rows[-1]["p_a"] = 0.0
    volume, efficiency = fit_ridge(rows, 0.0)
    assert volume > 0.15
    assert abs(efficiency) < 1e-8


def test_same_day_and_previous_season_do_not_enter_the_matchup():
    logs = [
        _row("2026-01-20", "2025-26", fga=30, pts=40),
        _row("2026-01-02", "2024-25", fga=5, pts=5),
    ]
    gaps = matchup_gaps(logs, date(2026, 1, 20), "9")
    assert gaps["PG"]["sample_games"] == 0


def test_current_season_gap_uses_only_earlier_games():
    logs = [
        _row("2026-01-02", "2025-26", fga=10, pts=12),
        _row("2026-01-04", "2025-26", fga=20, pts=30),
    ]
    gaps = matchup_gaps(logs, date(2026, 1, 10), "9")
    assert gaps["C"]["sample_games"] == 2
    assert gaps["C"]["mapping_confidence"] > gaps["PG"]["mapping_confidence"]


def test_assists_are_not_given_an_invented_channel():
    player = SimpleNamespace(
        position=Position.PG,
        field_goal_attempts=16,
        three_point_attempts=7,
        rebound_chances=8,
        usage_rate=0.28,
        minutes_per_game=34,
    )
    features = interaction_features({"PG": {"fga": 0.1, "ppp": 0.0, "sample_games": 12, "mapping_confidence": 0.55}}, player, "assists")
    assert features["identified"] is False
    assert features["x_volume"] == 0.0


def test_a_perfect_model_a_gets_no_interaction_coefficient():
    rows = []
    for day in range(6):
        for index in range(12):
            outcome = 1 if index % 2 == 0 else 0
            rows.append({
                "game_date": f"2026-01-{day+1:02d}",
                "x_volume": float(index - 6),
                "x_efficiency": float((index % 3) - 1),
                "p_a": outcome,
                "outcome": outcome,
            })
    chosen = select_lambda(rows)
    assert abs(chosen["coefficients"][0]) < 1e-8
    assert abs(chosen["coefficients"][1]) < 1e-8


def test_february_sample_stays_under_the_credit_gate():
    cost = estimate_february_cost(10, 30)
    assert cost["total_credits"] == 10 + 30 * 40
    assert cost["total_credits"] < 1500
    assert cost["march_not_included"] is True


def _row(day: str, season: str, fga: float, pts: float) -> dict:
    return {
        "season": season,
        "game_date": day,
        "opponent_team_id": "9",
        "position": "C",
        "min": 30,
        "pace": 100,
        "fga": fga,
        "pts": pts,
        "fg3a": 1,
        "fg3m": 0,
        "reb": 8,
        "rebound_chances_total": 10,
    }
