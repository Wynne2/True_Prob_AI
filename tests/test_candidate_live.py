"""Offline tests for engine/candidate/live.py and the board's candidate note."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace as NS

import pytest

from app import ui
from domain.enums import PropSide, PropType
from engine.candidate import live
from engine.candidate import minutes as cm
from engine.candidate import model as cmodel


def _logs(n=8, start=date(2026, 10, 20)):
    logs = []
    for i in range(n):
        day = (start + timedelta(days=2 * i)).isoformat()
        for pid, team, abbr, opp in (("a", "1", "AAA", "2"), ("x", "2", "BBB", "1")):
            logs.append({
                "season": "2026-27", "game_date": day, "game_id": f"g{i}", "team_id": team,
                "team_abbr": abbr, "opponent_team_id": opp, "player_id": pid, "min": 32.0,
                "pts": 20, "fga": 15, "fta": 4, "fg3a": 6, "fg3m": 2, "reb": 6,
                "rebound_chances_total": 11, "ast": 4, "passes": 40, "touches": 60,
                "usage_percentage": 0.24, "pace": 100.0, "possessions": 65,
            })
    return logs


def _params():
    rows = [{"features": {n: 30.0 + i for n in cm.CLEAN_FEATURES}, "actual": 30.0 + i} for i in range(5)]
    minutes = cm.Ridge(cm.CLEAN_FEATURES, 0.1).fit(rows)
    stats = ("points", "rebounds", "assists", "threes")
    return {
        "version": "candidate-v1",
        "minutes": cmodel.ridge_to_dict(minutes),
        "volume": {s: {"choice": "ratio20"} for s in stats},
        "spread": {s: {"terms": ["mean"], "coef": [1.5], "family": "count"} for s in stats},
    }


def _prop(pid, team, opp, prop_type=PropType.POINTS, line=19.5, side=PropSide.OVER):
    return NS(player_id=pid, team_abbr=team, opponent_abbr=opp, prop_type=prop_type, line=line, side=side)


def test_attach_scores_known_players_and_skips_unknown(monkeypatch):
    monkeypatch.setattr(live, "season_logs", lambda slate, season="2026-27": _logs())
    monkeypatch.setattr(cmodel, "load_params", lambda path=None: _params())
    over = _prop("a", "AAA", "BBB")
    under = _prop("a", "AAA", "BBB", side=PropSide.UNDER)
    stranger = _prop("zzz", "AAA", "BBB")
    status = live.attach_candidate([over, under, stranger], date(2026, 11, 5), season="2026-27")
    assert status["status"] == "ok" and status["attached"] == 2
    assert 0 < over.candidate_probability < 1
    assert over.candidate_probability + under.candidate_probability == pytest.approx(1.0, abs=1e-6)
    assert over.candidate_projection > 0
    assert getattr(stranger, "candidate_probability", None) is None


def test_attach_without_params_leaves_props_alone(monkeypatch):
    monkeypatch.setattr(cmodel, "load_params", lambda path=None: None)
    prop = _prop("a", "AAA", "BBB")
    assert live.attach_candidate([prop], date(2026, 11, 5))["status"] == "no_params"
    assert getattr(prop, "candidate_probability", None) is None


def test_placeholder_never_supplies_history():
    row = live._placeholder("a", "1", "2", date(2026, 11, 5), "2026-27")
    assert row["game_date"] == "2026-11-05" and row["pts"] == 0 and row["min"] == 1.0


def test_prior_season_label():
    assert live._prior_label("2026-27") == "2025-26"
    assert live._prior_label("2027-28") == "2026-27"


def test_candidate_note_on_card():
    assert ui.candidate_note(NS(candidate_probability=None)) == ""
    note = ui.candidate_note(NS(candidate_probability=0.523, candidate_projection=8.14))
    assert "52.3%" in note and "8.1" in note and "not used for edge" in note
