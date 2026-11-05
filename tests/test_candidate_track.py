"""Offline tests for ledger candidate fields, grading, and the live scorecard."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace as NS

import pytest

from domain.enums import PropSide, PropType
from engine import clv
from engine.candidate import track
from engine.prediction_ledger import save_pregame_predictions


def _prop(pid, name, line, side, odds, implied, cand, prod, market, method="candidate", outside=False):
    return NS(
        player_id=pid, player_name=name, team_abbr="AAA", opponent_abbr="BBB", game_id="g1",
        prop_type=PropType.POINTS, side=side, line=line, best_book_key="fanduel", best_book="fanduel",
        sportsbook_odds=odds, implied_probability=implied, market_probability=market,
        raw_model_probability=cand or prod, calibrated_probability=cand or prod,
        true_probability=cand if method == "candidate" else prod, model_weight=1.0,
        probability_method=method, edge=(cand if method == "candidate" else prod) - implied,
        projected_value=20.0, expected_minutes=32.0, confidence=NS(value="medium"),
        candidate_probability=cand, candidate_projection=21.0,
        production_probability=prod if method == "candidate" else None,
        candidate_outside_range=outside,
    )


def _board(tmp_path):
    day = date(2026, 11, 4)
    props = [
        _prop("1", "Ann", 19.5, PropSide.OVER, 150, 0.40, 0.55, 0.42, 0.41),   # candidate edge +15%
        _prop("2", "Bo", 10.5, PropSide.UNDER, -110, 0.524, 0.54, 0.50, 0.50),  # +1.6%
        _prop("3", "Cy", 30.5, PropSide.OVER, 400, 0.20, 0.35, 0.21, 0.19, method="market_blend", outside=True),
    ]
    save_pregame_predictions(day, props, tmp_path)
    box = [
        {"game_id": "g1", "player_id": "1", "player_name": "Ann", "pts": 24, "min": 33},
        {"game_id": "g1", "player_id": "2", "player_name": "Bo", "pts": 12, "min": 30},
        {"game_id": "g1", "player_id": "3", "player_name": "Cy", "pts": 18, "min": 34},
    ]
    return day, box


def test_ledger_keeps_candidate_fields_and_first_price(tmp_path):
    day, _ = _board(tmp_path)
    rows = json.loads((tmp_path / f"{day}.json").read_text())
    ann = next(r for r in rows if r["player"] == "Ann")
    assert ann["candidate_probability"] == pytest.approx(0.55)
    assert ann["production_probability"] == pytest.approx(0.42)
    # A later scan with a different price does not overwrite the saved row.
    later = _prop("1", "Ann", 19.5, PropSide.OVER, 120, 0.45, 0.60, 0.44, 0.44)
    save_pregame_predictions(day, [later], tmp_path)
    again = next(r for r in json.loads((tmp_path / f"{day}.json").read_text()) if r["player"] == "Ann")
    assert again["odds"] == 150


def test_grade_and_scorecard(tmp_path):
    day, box = _board(tmp_path)
    summary = clv.grade_predictions(day, tmp_path, final_game_ids={"g1"}, box_logs=box)
    assert summary["all"]["decided"] == 3
    card = track.scorecard(tmp_path, since=day)
    assert card["decided_rows"] == 3
    b = card["brier_same_props"]
    assert b["n"] == 3 and b["candidate"] is not None and b["market_novig"] is not None
    big = card["candidate_edge_bets"]["10%+"]
    assert big["n"] == 1 and big["hit_rate"] == 1.0 and big["flat_roi_per_bet"] == pytest.approx(1.5)
    # The +400 longshot was outside the tested range, so it is not counted as a candidate bet.
    assert sum(s["n"] for s in card["candidate_edge_bets"].values()) == 2


def test_grade_pending_skips_today_and_graded_dates(tmp_path, monkeypatch):
    day, box = _board(tmp_path)
    calls = []
    monkeypatch.setattr(clv, "grade_predictions", lambda d, directory: calls.append(d) or {"date": d.isoformat()})
    track.grade_pending(today=day, directory=tmp_path, since=day)
    assert calls == []  # same day: not graded yet
    track.grade_pending(today=date(2026, 11, 5), directory=tmp_path, since=day)
    assert calls == [day]
