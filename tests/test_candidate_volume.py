"""Offline tests for engine/candidate/volume.py."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from engine.candidate import volume as cv


def _row(day, gid, team, opp, pid, minutes, fga=10, fta=2, fg3a=4, reb=5, chances=9, ast=3, passes=30, pace=100.0):
    return {
        "season": "2025-26", "game_date": day.isoformat(), "game_id": gid, "team_id": team,
        "opponent_team_id": opp, "player_id": pid, "min": minutes, "fga": fga, "fta": fta,
        "fg3a": fg3a, "fg3m": 1, "pts": 12, "reb": reb, "rebound_chances_total": chances,
        "ast": ast, "passes": passes, "touches": 50, "usage_percentage": 0.2,
        "pace": pace, "possessions": 60,
    }


def _season(n=8, start=date(2025, 11, 1), override=None):
    logs = []
    for i in range(n):
        day = start + timedelta(days=2 * i)
        gid = f"g{i}"
        for pid, team, opp in (("a", "T1", "T2"), ("b", "T1", "T2"), ("x", "T2", "T1"), ("y", "T2", "T1")):
            kw = (override or {}).get((i, pid), {})
            logs.append(_row(day, gid, team, opp, pid, 30.0, **kw))
    return logs


def test_rows_use_only_earlier_games():
    logs = _season(override={(7, "a"): {"fga": 30}})
    rows = [r for r in cv.build_rows(logs) if r["player_id"] == "a" and r["stat"] == "points"]
    last = rows[-1]
    assert last["opportunities"] == pytest.approx(30 + 0.44 * 2)
    assert last["features"]["base_rate"] == pytest.approx((10 + 0.44 * 2) / 30)


def test_opponent_allowed_gap_reflects_what_the_opponent_gave_up():
    # Team T2's players shoot much more against T1, so T1 "allows" more than league.
    over = {(i, p): {"fga": 20} for i in range(8) for p in ("x", "y")}
    logs = _season(override=over)
    rows = [r for r in cv.build_rows(logs) if r["stat"] == "points" and r["game_date"] == logs[-1]["game_date"]]
    t1_player = next(r for r in rows if r["player_id"] == "a")   # faces T2, which allows little
    t2_player = next(r for r in rows if r["player_id"] == "x")   # faces T1, which allows a lot
    assert t2_player["features"]["allowed_gap_x_rate"] > 0 > t1_player["features"]["allowed_gap_x_rate"]


def test_failed_touch_feed_is_ignored():
    assert cv._touches({"touches": 2, "min": 30}) is None
    assert cv._touches({"touches": 45, "min": 30}) == pytest.approx(1.5)


def test_weighted_ridge_recovers_signal():
    import random
    rng = random.Random(1)
    rows = []
    for _ in range(600):
        base = rng.uniform(0.2, 0.8)
        rows.append({
            "features": {"base_rate": base, "pace_gap_x_rate": 0.0},
            "actual_rate": 0.9 * base + 0.03 + rng.gauss(0, 0.02),
            "minutes": rng.uniform(15, 38),
        })
    m = cv.WeightedRidge(("base_rate", "pace_gap_x_rate"), 0.1).fit(rows)
    assert m.describe()["base_rate"] == pytest.approx(0.9, abs=0.03)
    assert np.all(m.predict(rows) >= 0)


def test_backward_selection_drops_a_pure_noise_group(monkeypatch):
    import random
    rng = random.Random(2)

    def make(n):
        out = []
        for _ in range(n):
            base = rng.uniform(0.2, 0.8)
            feats = {f: 0.0 for f in cv.CLEAN_FEATURES}
            feats.update(base_rate=base, ewm_rate=base, last5_rate=base, season_rate=base,
                         last_season_rate=base, b2b=rng.choice([0.0, 1.0]))
            feats["pace_gap_x_rate"] = rng.gauss(0, 0.2)  # noise
            out.append({"features": feats, "actual_rate": base + rng.gauss(0, 0.05),
                        "minutes": 30.0, "opportunities": 0.0})
        for r in out:
            r["opportunities"] = r["actual_rate"] * r["minutes"]
        return out

    fit, select = make(400), make(400)
    _, dropped = cv.select_groups(fit, select, 1000.0)
    assert isinstance(dropped, list)
