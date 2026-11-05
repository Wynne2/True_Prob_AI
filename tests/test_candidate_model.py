"""Offline tests for engine/candidate/model.py."""

from __future__ import annotations

import json

import numpy as np
import pytest

from engine.candidate import minutes as cm
from engine.candidate import model as m
from engine.candidate import volume as cv


def _minutes_model():
    rows = [{"features": {n: 30.0 for n in cm.CLEAN_FEATURES}, "actual": 30.0} for _ in range(5)]
    for i, r in enumerate(rows):
        r["features"]["ewm3"] = 26.0 + 2 * i
        r["actual"] = 26.0 + 2 * i
    return cm.Ridge(cm.CLEAN_FEATURES, 0.1).fit(rows)


def test_ridge_round_trip_keeps_predictions():
    model = _minutes_model()
    rows = [{"features": {n: 31.0 for n in cm.CLEAN_FEATURES}}]
    restored = m.ridge_from_dict(json.loads(json.dumps(m.ridge_to_dict(model))), cm.Ridge)
    assert restored.predict(rows) == pytest.approx(model.predict(rows))


def _item(stat="rebounds", actual=6.0):
    chain = {
        "stat": stat, "actual": actual, "role": "starter", "game_date": "2025-12-01",
        "forecast": {"minutes": 30.0, "volume": 0.3, "conversion": 0.6},
        "recent_values": [4, 6, 8, 5, 7, 6, 5, 7, 6, 6], "recent_minutes": [30.0] * 10,
    }
    return {
        "stat": stat, "actual": actual, "role": "starter", "game_date": "2025-12-01",
        "player_id": "p", "game_id": "g", "season": "2025-26", "chain": chain,
        "minutes_row": {"features": {n: 30.0 for n in cm.CLEAN_FEATURES}, "history": [30.0] * 10},
        "volume_row": {"features": {"base_rate": 0.3}},
    }


def _params(stat="rebounds"):
    spread = {s: {"terms": ["mean"], "coef": [1.5], "family": "count"} for s in ("points", "rebounds", "assists", "threes")}
    return {
        "minutes": m.ridge_to_dict(_minutes_model()),
        "volume": {s: {"choice": "ratio20"} for s in ("points", "rebounds", "assists", "threes")},
        "spread": spread,
    }


def test_prediction_is_minutes_times_volume_times_conversion():
    pred = m.predict([_item()], _params())[0]
    assert pred["mean"] == pytest.approx(pred["minutes"] * 0.3 * 0.6)
    assert pred["variance"] == pytest.approx(1.5 * pred["mean"])


def test_over_and_push_probabilities_are_consistent():
    pred = {"mean": 5.4, "variance": 8.0, "family": "count"}
    over_45 = m.over_probability(pred, 4.5)
    over_5 = m.over_probability(pred, 5.0)
    push_5 = m.push_probability(pred, 5.0)
    # P(X > 4.5) = P(X >= 5) = P(X > 5) + P(X = 5)
    assert over_45 == pytest.approx(over_5 + push_5, abs=1e-9)
    assert m.push_probability(pred, 4.5) == 0.0
    assert m.push_probability({"mean": 20, "variance": 30, "family": "normal"}, 20.0) == 0.0


def test_spread_fit_is_non_negative_and_uses_candidate_mean():
    items = [_item(actual=a) for a in (2.0, 5.0, 9.0, 4.0, 7.0, 6.0) * 5]
    for stat in ("points", "assists", "threes"):
        items += [_item(stat=stat, actual=a) for a in (3.0, 5.0, 7.0) * 5]
    preds = m.predict(items, _params())
    spread = m.fit_spread(items, preds)
    for stat, spec in spread.items():
        assert all(c >= 0 for c in spec["coef"])
        assert spec["terms"] == list(m.SPREAD_FORMULA[stat][0])


def test_frozen_params_file_is_complete_when_present():
    params = m.load_params()
    if params is None:
        pytest.skip("candidate_v1.json not generated")
    assert params["version"] == m.VERSION
    assert set(params["volume"]) == {"points", "rebounds", "assists", "threes"}
    assert set(params["spread"]) == {"points", "rebounds", "assists", "threes"}
    assert params["holdout"].startswith("2026-27")


def test_calibration_shrinks_toward_half():
    pred = {"mean": 9.0, "variance": 9.0, "family": "count"}
    raw = m.over_probability(pred, 6.5)
    cal = m.over_probability(dict(pred, calibration={"a": 0.0, "b": 0.6}), 6.5)
    assert 0.5 < cal < raw


def test_v2_params_present_and_calibrated():
    params = m.load_params(m.ROOT / "data" / "models" / "candidate_v2.json")
    if params is None:
        pytest.skip("candidate_v2.json not generated")
    assert params["version"] == "candidate-v2"
    assert "abs_spread" in params["settings"]["minutes_features"]
    assert set(params["calibration"]) <= {"points", "rebounds", "assists", "threes"}


def test_v3_params_fix_volume_and_drop_calibration():
    v3 = m.load_params(m.ROOT / "data" / "models" / "candidate_v3.json")
    v1 = m.load_params()
    if v3 is None or v1 is None:
        pytest.skip("frozen params not generated")
    assert v3["version"] == "candidate-v3" and v3["calibration"] == {}
    assert v3["holdout_start"] == "2026-11-05"
    for stat in ("points", "rebounds", "assists", "threes"):
        assert v3["settings"]["volume"][stat]["choice"] == v1["settings"]["volume"][stat]["choice"]
        assert v3["settings"]["volume"][stat]["features"] == v1["settings"]["volume"][stat]["features"]
    assert "abs_spread" in v3["settings"]["minutes_features"]
