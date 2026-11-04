"""Offline tests for engine/accuracy/variance_fit.py."""

from __future__ import annotations

import random

import pytest

from engine.accuracy import variance_fit as vf


def _row(actual: float, mean: float = 10.0, day: str = "2025-11-05", season: str = "2025-26", role: str = "starter", stat: str = "rebounds"):
    return {
        "stat": stat, "season": season, "game_date": day, "role": role, "actual": actual,
        "forecast": {"minutes": 30.0, "volume": mean / 30.0, "conversion": 1.0},
        "recent_values": [mean - 2, mean, mean + 2] * 3 + [mean],
        "recent_minutes": [30.0] * 10,
    }


def test_fit_recovers_known_dispersion():
    rng = random.Random(3)
    rows = []
    for _ in range(6000):
        mean = rng.uniform(3, 15)
        rows.append(_row(rng.gauss(mean, (2.0 * mean) ** 0.5), mean=mean, season="2024-25"))
    (phi,) = vf.fit_formula(rows, ("mean",))
    assert phi == pytest.approx(2.0, rel=0.08)


def test_coefficients_are_non_negative():
    rows = [_row(10.0, mean=10.0, season="2024-25") for _ in range(50)]
    coef = vf.fit_formula(rows, ("mean", "mean_sq", "recent_var"))
    assert all(c >= 0 for c in coef)
    assert vf.variance(rows[0], ("mean", "mean_sq", "recent_var"), coef) >= 0.05


def test_count_tail_matches_poisson_and_negative_binomial():
    from scipy import stats
    assert vf.over_count(4.0, 4.0, 4.5) == pytest.approx(stats.poisson.sf(4, 4.0))
    nb = vf.over_count(4.0, 8.0, 4.5)
    assert nb == pytest.approx(stats.nbinom.sf(4, 4.0, 0.5))
    assert nb != pytest.approx(vf.over_count(4.0, 4.0, 4.5))


def test_production_threes_is_binomial_on_forecast_attempts():
    from scipy import stats
    row = _row(2, stat="threes")
    row["forecast"] = {"minutes": 30.0, "volume": 0.2, "conversion": 0.36}
    assert vf.production_over(row, 1.5) == pytest.approx(stats.binom.sf(1, 6, 0.36))


def test_split_refuses_2026_targets():
    with pytest.raises(ValueError):
        vf.split([_row(5, day="2026-01-03")])


def test_split_assigns_fit_select_check():
    rows = [_row(5, season="2024-25", day="2025-02-01"), _row(5, day="2025-11-01"), _row(5, day="2025-12-10")]
    fit, select, check = vf.split(rows)
    assert [len(fit), len(select), len(check)] == [1, 1, 1]


def test_correct_spread_scores_better_than_too_wide():
    rng = random.Random(5)
    rows = [_row(round(max(0, rng.gauss(10, 2.5))), mean=10.0) for _ in range(3000)]
    right = vf.score(rows, lambda r, line: vf.over_normal(10.0, 2.5 ** 2, line))
    wide = vf.score(rows, lambda r, line: vf.over_normal(10.0, 6.0 ** 2, line))
    assert right["log_loss"] < wide["log_loss"]
    assert right["ece"] < wide["ece"]
