"""Accuracy experiment: pre-2026 fit, no second blowout, March not used to choose weights."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from engine.accuracy.dataset import build_player_games
from engine.accuracy.fit import FEATURES, TRAIN_END, calibration_slope, fit_stat, prune
from engine.backtest.advanced_history import HISTORY_WINDOWS
from models.points_model import PointsModel


def _row(day: str, actual: float, features: list[float]) -> dict:
    return {
        "game_date": day,
        "baseline": 10.0,
        "actual": actual,
        "x": features,
        "role": "starter",
    }


def test_fit_rejects_january():
    with pytest.raises(ValueError):
        fit_stat([_row("2026-01-05", 10.0, [0, 0, 0, 0])])


def test_december_keeps_a_real_pace_signal_and_drops_the_other_rating():
    rows = []
    for offset in range(30):
        day = (date(2025, 11, 1) + timedelta(days=offset)).isoformat()
        rows.append(_row(day, 13.0, [1.0, 0.0, 0.0, 0.0]))
    for offset in range(12):
        day = (date(2025, 12, 1) + timedelta(days=offset)).isoformat()
        rows.append(_row(day, 13.0, [1.0, 0.0, 0.0, 0.0]))
    fit = fit_stat(rows)
    assert fit["coefficients"][FEATURES.index("pace")] != 0
    assert fit["coefficients"][FEATURES.index("defensive_rating")] == 0
    assert str(TRAIN_END) == "2026-01-01"


def test_prune_removes_a_feature_that_adds_december_error():
    rows = [_row("2025-12-02", 11.0, [1.0, 1.0, 0.0, 0.0]) for _ in range(12)]
    kept = prune(rows, [1.0, 0.5, 0.0, 0.0])
    assert kept[0] == 1.0
    assert kept[1] == 0.0


def test_overconfident_probabilities_are_shrunk_without_a_market_price():
    probabilities = [0.9] * 100
    outcomes = [1] * 50 + [0] * 50
    assert calibration_slope(probabilities, outcomes) == 0.5


def test_same_game_is_not_inside_its_own_baseline():
    logs = []
    for index, points in enumerate((10, 20, 30, 100)):
        day = (date(2025, 11, 1) + timedelta(days=index * 2)).isoformat()
        logs.append({
            "season": "2025-26",
            "game_date": day,
            "game_id": day,
            "player_id": "7",
            "player_name": "Tester",
            "team_id": "1",
            "opponent_team_id": "2",
            "position": "G",
            "min": 30,
            "pts": points,
            "reb": 4,
            "ast": 3,
            "fg3m": 1,
            "fga": 10,
            "fg3a": 4,
            "fta": 2,
            "pace": 100,
            "possessions": 20,
            "defensive_rating": 112,
            "usage_percentage": 0.22,
        })
    rows = build_player_games(logs, date(2025, 11, 1), date(2025, 11, 8))
    last = [row for row in rows if row["stat"] == "points" and row["game_date"] == "2025-11-07"]
    assert last
    assert last[0]["actual"] == 100
    assert 15 < last[0]["baseline"] < 25
    assert last[0]["role"] == "starter"


def test_march_is_not_in_the_default_history_and_blowout_is_not_a_new_feature():
    assert HISTORY_WINDOWS[-1][1] == date(2026, 2, 28)
    assert "blowout" not in FEATURES
    source = Path("models/points_model.py").read_text(encoding="utf-8")
    assert "partial_factor(pos_factor, 0.55)" in source
    minutes = Path("models/minutes_model.py").read_text(encoding="utf-8")
    assert "blowout_risk * 0.06" in minutes
    assert PointsModel is not None
