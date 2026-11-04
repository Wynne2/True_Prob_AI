"""Offline tests for engine/candidate/minutes.py."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from engine.candidate import minutes as cm


def _game(day: date, gid: str, team: str, players: dict[str, float], season="2025-26"):
    return [
        {"season": season, "game_date": day.isoformat(), "game_id": gid, "team_id": team,
         "player_id": pid, "min": m}
        for pid, m in players.items()
    ]


def _schedule(n: int, start=date(2025, 11, 1), every=2, lineup=None, overrides=None, season="2025-26"):
    lineup = lineup or {"a": 34.0, "b": 30.0, "c": 20.0}
    logs = []
    for i in range(n):
        players = dict(lineup)
        players.update((overrides or {}).get(i, {}))
        players = {p: m for p, m in players.items() if m is not None}
        logs += _game(start + timedelta(days=every * i), f"g{i}", "T1", players, season)
    return logs


def test_ewm_weights_recent_games_more():
    assert cm.ewm([10, 10, 30], 1.0) > sum([10, 10, 30]) / 3
    assert cm.ewm([20, 20, 20], 3.0) == pytest.approx(20.0)


def test_features_use_only_earlier_games():
    logs = _schedule(8, overrides={7: {"a": 44.0}})
    rows = [r for r in cm.build_rows(logs) if r["player_id"] == "a"]
    last = rows[-1]
    assert last["actual"] == 44.0
    assert last["features"]["last10"] == pytest.approx(34.0)
    assert 44.0 not in last["history"]


def test_absent_rotation_teammate_is_counted():
    logs = _schedule(9, overrides={8: {"b": None}})  # b misses the last game
    last_a = [r for r in cm.build_rows(logs) if r["player_id"] == "a"][-1]
    assert last_a["features"]["absent_minutes"] == pytest.approx(30.0)
    earlier = [r for r in cm.build_rows(logs) if r["player_id"] == "a"][-2]
    assert earlier["features"]["absent_minutes"] == 0.0


def test_just_returned_and_back_to_back():
    logs = _schedule(9, every=1, overrides={7: {"c": None}})
    c_rows = [r for r in cm.build_rows(logs) if r["player_id"] == "c"]
    assert c_rows[-1]["features"]["just_returned"] == 1.0
    assert c_rows[-1]["features"]["b2b"] == 1.0


def test_last_season_minutes_carry_over():
    old = _schedule(12, start=date(2025, 1, 1), lineup={"a": 36.0, "b": 30.0, "c": 20.0}, season="2024-25")
    new = _schedule(7, lineup={"a": 26.0, "b": 30.0, "c": 20.0})
    rows = [r for r in cm.build_rows(old + new) if r["player_id"] == "a" and r["season"] == "2025-26"]
    assert rows[0]["features"]["has_last_season"] == 1.0
    assert rows[0]["features"]["last_season"] == pytest.approx(36.0)
    blended = cm.predict_blend(rows[0], 3.0, 10.0)
    assert 26.0 < blended < 36.0


def test_split_refuses_2026_and_assigns_months():
    rows = [{"game_date": d} for d in ("2025-03-01", "2025-10-20", "2025-11-15", "2025-12-15")]
    fit, select, check = cm.split(rows)
    assert [len(fit), len(select), len(check)] == [2, 1, 1]
    with pytest.raises(ValueError):
        cm.split([{"game_date": "2026-01-02"}])


def test_ridge_recovers_a_linear_signal():
    import random
    rng = random.Random(0)
    rows = []
    for _ in range(500):
        x = rng.uniform(10, 36)
        rows.append({"features": {"ewm3": x, "b2b": 0.0}, "actual": 0.8 * x + 4 + rng.gauss(0, 0.5)})
    model = cm.Ridge(("ewm3", "b2b"), 0.1).fit(rows)
    assert model.describe()["ewm3"] == pytest.approx(0.8, abs=0.03)
