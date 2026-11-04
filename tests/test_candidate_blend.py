"""Offline tests for engine/candidate/blend.py."""

import numpy as np
import pytest

from engine.candidate import blend as cb


def test_blend_endpoints():
    model, market = np.array([0.7]), np.array([0.5])
    assert cb._blend(model, market, 0.0, 1.0)[0] == pytest.approx(0.5)
    assert cb._blend(model, market, 1.0, 99.0)[0] == pytest.approx(0.7)
    assert 0.5 < cb._blend(model, market, 0.5, 99.0)[0] < 0.7


def test_candidate_probability_conditions_on_no_push():
    pred = {"mean": 5.0, "variance": 7.0, "family": "count"}
    p = cb.candidate_probability(pred, 5.0)
    from engine.candidate import model as m
    assert p > m.over_probability(pred, 5.0)
    assert cb.candidate_probability(pred, 4.5) == pytest.approx(m.over_probability(pred, 4.5))


def test_fit_gives_zero_weight_when_model_is_noise():
    import random
    rng = random.Random(4)
    rows = []
    for d in range(10):
        for _ in range(60):
            market = rng.uniform(0.3, 0.7)
            y = 1.0 if rng.random() < market else 0.0
            rows.append({"date": f"2026-01-{d+1:02d}", "prop": "points", "market": market,
                         "model": rng.uniform(0.05, 0.95), "old_model": market, "y": y})
    fitted = cb.fit(rows)
    assert fitted["weights"]["points"]["weight"] == 0.0
    assert fitted["weights"]["threes"]["weight"] == 0.0  # no rows -> market only
