"""Walk-forward market correction: no look-ahead, symmetric, and off unless it clearly helps."""

import json

import pytest

from engine import market_slope as ms


def _rows(dates, b_true):
    """Synthetic lines whose real hit rate follows slope b_true applied to the market price."""
    out = []
    for d in dates:
        for i in range(400):
            p = 0.05 + 0.9 * i / 399
            q = ms.apply_slope(p, b_true)
            # deterministic outcomes with the right frequency per price
            out.append({"date": d, "p": p, "y": 1.0 if (i * 7919 % 1000) / 1000 < q else 0.0, "prop": "points"})
    return out


def test_apply_slope_is_symmetric_and_identity_at_one():
    assert ms.apply_slope(0.3, 1.0) == pytest.approx(0.3)
    assert ms.apply_slope(0.3, 1.2) + ms.apply_slope(0.7, 1.2) == pytest.approx(1.0)


def test_walk_forward_never_trains_on_the_scored_date():
    rows = _rows([f"2026-01-{d:02d}" for d in range(1, 8)], 1.0)
    wf = ms.walk_forward(rows)
    assert wf["test_dates"] == 7 - ms.MIN_TRAIN_DATES
    for f in wf["folds"]:
        earlier = [r for r in rows if r["date"] < f["date"]]
        assert f["train_rows"] == len(earlier)


def test_fit_recovers_a_real_bias():
    rows = _rows([f"2026-01-{d:02d}" for d in range(1, 6)], 1.25)
    assert 1.1 <= ms.fit_slope(rows) <= 1.4


def test_slope_stays_off_unless_it_passes(tmp_path):
    path = tmp_path / "s.json"
    assert ms.load_slope(path) == 1.0                       # no file
    path.write_text(json.dumps({"in_use": False, "slope_in_use": 1.2}))
    assert ms.load_slope(path) == 1.0
    path.write_text(json.dumps({"in_use": True, "slope_in_use": 1.2}))
    assert ms.load_slope(path) == 1.2


def test_saved_result_matches_the_data():
    """The committed file must be what the committed code produces on the committed data."""
    saved = json.loads(ms.PARAMS_PATH.read_text(encoding="utf-8"))
    fresh = ms.run(save=False)
    if fresh["dates"] != saved["dates"]:
        pytest.skip("new graded dates since the last fit; rerun: python -m engine.market_slope")
    assert fresh["walk_forward"]["brier_market"] == saved["walk_forward"]["brier_market"]
    assert fresh["in_use"] == saved["in_use"]
