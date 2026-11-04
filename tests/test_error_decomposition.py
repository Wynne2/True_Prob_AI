"""Offline tests for engine/accuracy/decompose.py."""

from __future__ import annotations

import pytest

from engine.accuracy import decompose as dec


def _row(day: str, pid: str = "1", minutes: float = 30, fga: int = 15, fta: int = 4, pts: int = 20, season: str = "2025-26"):
    return {
        "season": season, "game_date": day, "game_id": f"g{day}{pid}", "player_id": pid,
        "min": minutes, "fga": fga, "fta": fta, "pts": pts, "fg3a": 6, "fg3m": 2,
        "reb": 5, "rebound_chances_total": 9, "ast": 4, "passes": 40,
    }


def _days(n: int, start: int = 1):
    return [f"2025-11-{d:02d}" for d in range(start, start + n)]


def test_shapley_shares_sum_to_one_and_split_symmetrically():
    rows = [{
        "actual": 20.0,
        "forecast": {"minutes": 30.0, "volume": 0.5, "conversion": 1.2},
        "observed": {"minutes": 30.0, "volume": 0.5, "conversion": 20.0 / 15.0},
    }]
    errors = dec._squared_errors(rows)
    mse = {k: float(v.mean()) for k, v in errors.items()}
    shares = dec.shapley_shares(mse)
    assert sum(shares.values()) == pytest.approx(1.0)
    # Only conversion was wrong, so it carries all the error.
    assert shares["conversion"] == pytest.approx(1.0)
    assert shares["minutes"] == pytest.approx(0.0)


def test_swapping_every_link_reproduces_the_actual_stat():
    logs = [_row(d) for d in _days(8)]
    logs[-1].update({"min": 22, "fga": 9, "fta": 1, "pts": 11})
    rows = dec.build_rows(logs)
    points = [r for r in rows if r["stat"] == "points"]
    assert points
    for r in points:
        assert dec.chain_prediction(r, frozenset(dec.LINKS)) == pytest.approx(r["actual"])


def test_forecast_uses_only_earlier_dates():
    logs = [_row(d) for d in _days(6)]
    # A big game on the target date must not leak into its own forecast.
    logs[-1].update({"min": 44, "fga": 30, "pts": 50})
    rows = [r for r in dec.build_rows(logs) if r["stat"] == "points"]
    target = rows[-1]
    assert target["game_date"] == logs[-1]["game_date"]
    assert target["forecast"]["minutes"] == pytest.approx(30.0)
    assert target["forecast"]["volume"] == pytest.approx((15 + 0.44 * 4) / 30)


def test_needs_history_and_projected_minutes():
    few = [_row(d) for d in _days(dec.MIN_HISTORY)]
    assert not dec.build_rows(few)  # last game has only MIN_HISTORY-1 prior games
    low = [_row(d, minutes=8) for d in _days(10)]
    assert not dec.build_rows(low)


def test_history_resets_each_season():
    old = [_row(f"2025-03-{d:02d}", season="2024-25") for d in range(1, 10)]
    new = [_row(d) for d in _days(3)]
    rows = dec.build_rows(old + new)
    assert all(r["season"] == "2024-25" for r in rows)


def test_targets_stop_before_2026_and_report_refuses_leaks():
    logs = [_row(d) for d in _days(6)] + [_row("2026-01-05")]
    rows = dec.build_rows(logs)
    assert all(r["game_date"] < "2026-01-01" for r in rows)
    leaked = [dict(rows[0], game_date="2026-01-05")]
    with pytest.raises(ValueError):
        dec.build_report(leaked)


def test_impossible_tracking_counts_are_missing():
    assert dec.opportunities({"passes": 1, "ast": 3, "min": 36}, "assists") is None
    assert dec.opportunities({"passes": 2, "ast": 0, "min": 36}, "assists") is None
    assert dec.opportunities({"passes": 30, "ast": 4, "min": 30}, "assists") == 30
    assert dec.opportunities({"rebound_chances_total": 3, "reb": 5}, "rebounds") is None
    assert dec.opportunities({"rebound_chances_total": 9, "reb": 5}, "rebounds") == 9


def test_spread_check_on_a_correct_sd_covers_near_nominal():
    import random
    rng = random.Random(1)
    rows = []
    for i in range(4000):
        actual = rng.gauss(20.0, 5.0)
        rows.append({
            "stat": "points", "actual": actual, "recent_values": [20.0] * 10,
            "forecast": {"minutes": 20.0, "volume": 1.0, "conversion": 1.0},
        })
    s = dec.spread_check(rows, lambda row, mean: 5.0)
    assert s["z_sd"] == pytest.approx(1.0, abs=0.05)
    assert s["coverage_50"] == pytest.approx(0.50, abs=0.03)
    assert s["coverage_80"] == pytest.approx(0.80, abs=0.03)
