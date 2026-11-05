"""Offline tests for engine/candidate/matchups.py."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace as NS

import pytest

from domain.enums import PropSide, PropType
from engine.candidate import matchups as mf


def test_groups_and_fantasy_points():
    assert mf.groups_of("G-F") == {"G", "F"}
    assert mf.groups_of("C") == {"C"}
    assert mf.fantasy_points({"pts": 20, "reb": 10, "ast": 4, "fg3m": 2}) == pytest.approx(20 + 12 + 6 + 1)


def _season(n_teams=12, games=6, leaky_team="T0"):
    """Each team plays each date; T0's opponents score far more (T0 is the worst defense)."""
    logs = []
    teams = [f"T{i}" for i in range(n_teams)]
    for g in range(games):
        day = (date(2026, 10, 21) + timedelta(days=2 * g)).isoformat()
        for i in range(0, n_teams, 2):
            a, b = teams[(i + g) % n_teams], teams[(i + 1 + g) % n_teams]
            gid = f"g{g}-{i}"
            for team, opp in ((a, b), (b, a)):
                boost = 15 if opp == leaky_team else 0
                for pid, pos in (("G", "G"), ("F", "F"), ("C", "C")):
                    logs.append({
                        "season": "2026-27", "game_date": day, "game_id": gid, "team_id": team,
                        "opponent_team_id": opp, "player_id": f"{team}-{pid}", "position": pos,
                        "min": 32, "pts": 18 + boost, "reb": 6, "ast": 4, "fg3m": 2, "fga": 14,
                        "fg3a": 6, "passes": 45, "rebound_chances": 13, "rebound_chances_total": 13,
                        "usage_percentage": 0.24,
                        "defensive_rating": 125 if team == leaky_team else 110, "possessions": 70,
                    })
    return logs


def _state(logs, before):
    state = mf.SeasonState()
    days = sorted({r["game_date"] for r in logs if r["game_date"] < before})
    for d in days:
        state.update([r for r in logs if r["game_date"] == d])
    return state


def test_worst_defense_ranks_first_and_uses_only_earlier_games():
    logs = _season()
    state = _state(logs, "2026-12-01")
    assert state.defense_ranks()["T0"] == 1
    assert state.group_ranks("G")["T0"] == 1
    early = _state(logs, "2026-10-25")  # only 2 games: below MIN_TEAM_GAMES, nobody ranked
    assert early.defense_ranks() == {}


def test_opportunity_thresholds():
    avg = {"minutes": 30, "usage": 0.22, "fga": 13, "fg3a": 6, "passes": 30, "ast": 6, "rebound_chances": 10}
    assert mf.opportunity_ok("points", avg) and mf.opportunity_ok("threes", avg)
    assert mf.opportunity_ok("assists", avg) and not mf.opportunity_ok("rebounds", avg)
    assert not mf.opportunity_ok("points", dict(avg, minutes=20))
    assert mf.opportunity_ok("points", dict(avg, usage=22.0))  # percent form


def test_screen_passes_player_facing_the_leaky_team():
    state = _state(_season(), "2026-12-01")
    s = mf.screen(state, "T1-G", "T0", "points")
    assert s["passes_screen"] and s["defense_rank"] == 1 and s["position_groups"] == "G"


def test_find_for_slate_prices_screened_props():
    logs = _season()
    for r in logs:
        r["team_abbr"] = r["team_id"]
    prop = NS(player_id="T1-G", player_name="Guard One", team_abbr="T1", opponent_abbr="T0",
              prop_type=PropType.POINTS, side=PropSide.OVER, line=24.5, sportsbook_odds=-110,
              implied_probability=110 / 210, candidate_probability=0.60, candidate_projection=26.0,
              best_book_key="fanduel", candidate_outside_range=False)
    rows = mf.find_for_slate([prop], date(2026, 12, 1), season="2026-27", logs=logs)
    assert len(rows) == 1
    r = rows[0]
    assert r["steps_passed"] == 3
    assert r["edge"] == pytest.approx(0.60 - 110 / 210)
    assert r["expected_roi"] == pytest.approx(0.60 * (100 / 110) - 0.40)


def _s(step1=True, step2=True, step3=True):
    return {"step1_bottom10_defense": step1, "step2_bottom10_vs_position": step2, "step3_opportunity": step3,
            "passes_screen": step1 and step2 and step3, "defense_rank": 3, "position_fp_rank": 5}


def test_fade_pick_rules():
    assert mf.tag(_s(), "under", 0.60, 0.52, False)["fade_pick"]
    assert not mf.tag(_s(), "under", 0.54, 0.52, False)["fade_pick"]        # edge under 3%
    assert not mf.tag(_s(), "under", 0.60, 0.52, True)["fade_pick"]         # untested price
    assert not mf.tag(_s(step2=False), "under", 0.60, 0.52, False)["fade_pick"]
    assert not mf.tag(_s(), "over", 0.60, 0.52, False)["fade_pick"]
    assert mf.tag(_s(step3=False), "under", 0.60, 0.52, False)["fade_pick"]  # opportunity not required


def test_screened_over_rule():
    assert mf.tag(_s(), "over", 0.40, 0.52, False)["screened_over"]
    assert not mf.tag(_s(step3=False), "over", 0.40, 0.52, False)["screened_over"]
    assert not mf.tag(_s(), "under", 0.40, 0.52, False)["screened_over"]


def test_find_for_slate_tags_every_screened_prop():
    logs = _season()
    for r in logs:
        r["team_abbr"] = r["team_id"]
    prop = NS(player_id="T1-G", player_name="Guard One", team_abbr="T1", opponent_abbr="T0",
              prop_type=PropType.POINTS, side=PropSide.UNDER, line=24.5, sportsbook_odds=-110,
              implied_probability=110 / 210, candidate_probability=0.60, candidate_projection=22.0,
              best_book_key="fanduel", candidate_outside_range=False)
    mf.find_for_slate([prop], date(2026, 12, 1), season="2026-27", logs=logs, min_steps=3)
    assert prop.matchup_screen["fade_pick"] and not prop.matchup_screen["screened_over"]
