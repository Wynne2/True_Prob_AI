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
    monkeypatch.setattr(live, "injured_out", lambda: set())
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
    monkeypatch.setattr(live, "injured_out", lambda: set())
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


def test_injured_player_is_not_scored_but_teammates_are(monkeypatch):
    logs = _logs()
    for r in list(logs):
        if r["player_id"] == "a":
            logs.append(dict(r, player_id="b", min=24.0))
    monkeypatch.setattr(live, "season_logs", lambda slate, season="2026-27": logs)
    monkeypatch.setattr(live, "injured_out", lambda: {"b"})
    monkeypatch.setattr(cmodel, "load_params", lambda path=None: _params())
    a, b = _prop("a", "AAA", "BBB"), _prop("b", "AAA", "BBB")
    status = live.attach_candidate([a, b], date(2026, 11, 5), season="2026-27")
    assert a.candidate_probability is not None
    assert getattr(b, "candidate_probability", None) is None
    assert status["injured_out_on_slate"] == 1


def test_recent_rotation_and_spreads():
    rot = live.recent_rotation(_logs(), "2026-27")
    assert rot["1"] == {"a"} and rot["2"] == {"x"}
    teams = {"AAA": "1", "BBB": "2"}
    games = [NS(home_team_abbr="AAA", away_team_abbr="BBB", home_spread=-6.5),
             NS(home_team_abbr="BBB", away_team_abbr="AAA", home_spread=0.0)]
    s = live._spreads(games, teams, date(2026, 11, 5))
    assert s[("1", "2026-11-05")] == -6.5 and s[("2", "2026-11-05")] == 6.5


def _priced(cand, implied=0.5, true=0.45):
    return NS(candidate_probability=cand, candidate_projection=9.0, true_probability=true,
              implied_probability=implied, calibrated_probability=true, raw_model_probability=true,
              edge=true - implied, fair_odds=0, model_weight=0.0, probability_method="market_blend",
              production_probability=None, market_probability=0.47)


def test_candidate_drives_edge_and_keeps_production():
    prop = _priced(0.60)
    fallback = _priced(None)
    status = live.apply_candidate_edges([prop, fallback])
    assert status == {"applied": 1, "fallback": 1, "outside_range": 0, "enabled": True}
    assert prop.true_probability == pytest.approx(0.60)
    assert prop.edge == pytest.approx(0.10)
    assert prop.production_probability == pytest.approx(0.45)
    assert prop.fair_odds < 0 and prop.probability_method == "candidate"
    assert fallback.true_probability == 0.45 and fallback.probability_method == "market_blend"


def test_candidate_edge_is_clamped_like_production():
    from domain.constants import MAX_PROBABILITY_CEILING
    prop = _priced(0.99)
    live.apply_candidate_edges([prop])
    assert prop.true_probability == pytest.approx(MAX_PROBABILITY_CEILING)


def test_candidate_edge_switch_off(monkeypatch):
    from domain import constants
    monkeypatch.setattr(constants, "USE_CANDIDATE_EDGES", False)
    prop = _priced(0.60)
    assert live.apply_candidate_edges([prop])["enabled"] is False
    assert prop.true_probability == 0.45


def test_board_labels_for_candidate_props():
    prop = _priced(0.60)
    live.apply_candidate_edges([prop])
    assert ui.probability_label(prop) == "Candidate"
    assert ui.edge_basis(prop) == "edge"
    note = ui.probability_note(prop)
    assert "Candidate model" in note and "45.0%" in note and "vig included" in note
    assert ui.candidate_note(prop) == ""


def test_spreads_skip_games_already_started():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 5, 1, 0, tzinfo=timezone.utc)
    teams = {"AAA": "1", "BBB": "2", "CCC": "3", "DDD": "4"}
    started = NS(home_team_abbr="AAA", away_team_abbr="BBB", home_spread=-22.5, status="",
                 tip_off_time=datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc))
    later = NS(home_team_abbr="CCC", away_team_abbr="DDD", home_spread=-4.5, status="",
               tip_off_time=datetime(2026, 11, 5, 3, 0, tzinfo=timezone.utc))
    s = live._spreads([started, later], teams, date(2026, 11, 4), now=now)
    assert ("1", "2026-11-04") not in s and s[("3", "2026-11-04")] == -4.5


def test_candidate_edge_skips_untested_prices():
    longshot = _priced(0.40, implied=0.21)
    heavy_fav = _priced(0.90, implied=0.86)
    normal = _priced(0.55, implied=0.50)
    status = live.apply_candidate_edges([longshot, heavy_fav, normal])
    assert status["applied"] == 1 and status["outside_range"] == 2
    assert longshot.probability_method == "market_blend" and longshot.candidate_outside_range
    assert "outside the range" in ui.candidate_note(longshot)
    assert normal.probability_method == "candidate"


def test_board_uses_newest_frozen_version():
    params = live.frozen_params()
    if params is None:
        pytest.skip("no frozen params")
    assert params["version"] == "candidate-v3"
