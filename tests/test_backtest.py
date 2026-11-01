"""Backtest rules: no look-ahead, pushes are not losses, prices are not invented."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from domain.entities import OddsLine
from domain.enums import BookName, ConfidenceTier, DataSource, PropSide, PropType
from engine.backtest.asof import build_players_asof, logs_before, names_match
from engine.backtest.expanded import CREDIT_APPROVAL_LIMIT, estimate_expanded_cost, select_cohort
from engine.backtest.line_rule import select_evaluation_line
from engine.backtest.profiles import build_defense_asof, build_player_asof, position_bucket
from engine.backtest.metrics import (
    brier_and_log_loss,
    chronological_split,
    log_loss,
    select_primary_lines,
)
from engine.backtest.odds_history import (
    estimate_sample_cost,
    parse_historical_bookmakers,
    snapshot_is_pregame,
    snapshot_target,
)
from engine.backtest.runner import evaluate_snapshot
from engine.backtest_grade import brier_score, grade_prop
from engine.prediction_ledger import save_pregame_predictions


def _log(day: str, pts: float, player_id: str = "9") -> dict:
    return {
        "player_id": player_id,
        "player_name": "Jayson Tatum",
        "game_date": day,
        "team_id": "2",
        "opponent_team_id": "5",
        "position": "F",
        "min": 32.0,
        "pts": pts,
        "reb": 6,
        "ast": 4,
        "fg3m": 2,
        "fg3a": 7,
        "fga": 16,
    }


def test_logs_on_game_day_are_excluded_from_pregame_inputs():
    logs = [_log("2026-10-28", 10), _log("2026-10-29", 40)]
    kept = logs_before(logs, date(2026, 10, 29))
    assert [row["pts"] for row in kept] == [10]
    player = build_players_asof(logs, date(2026, 10, 29))["9"]
    assert player.points_per_game == 10
    assert player.potential_assists == 0


def test_player_name_matching_ignores_suffix_and_apostrophe():
    assert names_match("Jayson Tatum", "Jayson Tatum Jr.")
    assert names_match("De'Aaron Fox", "DeAaron Fox")
    assert not names_match("Jayson Tatum", "Jaylen Brown")


def test_snapshot_must_be_before_tip():
    tip = datetime(2026, 10, 29, 23, 0, tzinfo=timezone.utc)
    target = snapshot_target(tip)
    assert target == datetime(2026, 10, 29, 21, 0, tzinfo=timezone.utc)
    assert snapshot_is_pregame(target, tip) is True
    assert snapshot_is_pregame(tip, tip) is False


def test_one_sided_historical_price_is_not_invented():
    event = {
        "bookmakers": [{
            "key": "fanduel",
            "markets": [{
                "key": "player_points",
                "outcomes": [
                    {"name": "Over", "description": "Jayson Tatum", "price": -120, "point": 26.5},
                ],
            }],
        }],
    }
    lines, _unsupported = parse_historical_bookmakers(event, "evt")
    assert lines == []


def test_sample_cost_is_events_list_plus_four_markets():
    cost = estimate_sample_cost(1)
    assert cost["events_list_credits"] == 1
    assert cost["event_odds_credits"] == 40
    assert cost["total_credits"] == 41


def test_push_is_not_a_loss_and_is_out_of_brier():
    assert grade_prop(20, 20, "over") == "push"
    assert grade_prop(21, 20.5, "over") == "win"
    rows = [
        {"result": "push", "raw_probability": 0.6, "outcome": None},
        {"result": "win", "raw_probability": 0.6, "outcome": 1},
        {"result": "loss", "raw_probability": 0.6, "outcome": 0},
    ]
    scores = brier_and_log_loss(rows)
    assert scores["n"] == 2
    assert scores["brier"] == (brier_score(0.6, 1) + brier_score(0.6, 0)) / 2
    assert log_loss(0.6, 1) > 0


def test_alternate_lines_are_one_observation():
    rows = [
        {"variant": "A_opportunity", "player_id": "9", "prop_type": "points", "side": "over",
         "line": 20.5, "projected": 18.0},
        {"variant": "A_opportunity", "player_id": "9", "prop_type": "points", "side": "over",
         "line": 28.5, "projected": 18.0},
    ]
    chosen = select_primary_lines(rows)
    assert len(chosen) == 1
    assert chosen[0]["line"] == 20.5


def test_evaluation_line_ignores_any_projection():
    even = OddsLine(
        book=BookName.FANDUEL, player_id="a", player_name="Jayson Tatum",
        prop_type=PropType.POINTS, line=32.5, over_odds=-110, under_odds=-110,
        book_key="fanduel", data_source=DataSource.ODDS_API,
    )
    long = OddsLine(
        book=BookName.DRAFTKINGS, player_id="a", player_name="Jayson Tatum",
        prop_type=PropType.POINTS, line=18.5, over_odds=-400, under_odds=300,
        book_key="draftkings", data_source=DataSource.ODDS_API,
    )
    chosen = select_evaluation_line([long, even])
    assert chosen.line == 32.5


def test_position_buckets_are_explicit():
    assert position_bucket("G") == "PG"
    assert position_bucket("G-F") == "SG"
    assert position_bucket("F-C") == "PF"
    assert position_bucket("C") == "C"
    assert position_bucket("") == ""


def test_previous_season_is_a_prior_and_future_games_are_excluded():
    rows = [
        _profile_row("2024-25", "2025-01-10", pts=10),
        _profile_row("2025-26", "2026-01-02", pts=20),
        _profile_row("2025-26", "2026-01-20", pts=50, passes=12),
    ]
    kept = logs_before(rows, date(2026, 1, 15))
    player, quality = build_player_asof(kept, date(2026, 1, 15))
    assert player is not None
    assert quality["current_games"] == 1
    assert quality["previous_season_games"] == 1
    assert player.points_per_game < 15
    assert player.potential_assists == 0.0
    assert quality["potential_assists_source"] == "unavailable"


def test_previous_season_defense_is_not_this_year_team():
    old = _profile_row("2024-25", "2025-01-10", pts=10)
    old["team_id"] = "2"
    old["possessions"] = 80
    old["pace"] = 110
    old["defensive_rating"] = 108
    defense, quality = build_defense_asof([old], date(2026, 1, 15), "2")
    assert defense.pace_is_fallback is True
    assert quality["model_c_status"] == "incomplete"


def test_thirty_game_estimate_above_the_cap_is_visible():
    cost = estimate_expanded_cost(10, 40)
    assert cost["total_credits"] > CREDIT_APPROVAL_LIMIT
    small = estimate_expanded_cost(8, 30)
    assert small["total_credits"] == 8 + 30 * 40
    assert small["total_credits"] < CREDIT_APPROVAL_LIMIT


def test_cohort_spreads_games_across_dates_and_caps_teams():
    events_by_date = []
    for index, day in enumerate((date(2026, 1, 5), date(2026, 1, 8), date(2026, 1, 12))):
        events_by_date.append((day, [
            {"id": f"{index}-1", "home_team": "A", "away_team": "B", "commence_time": "2026-01-05T00:00:00Z"},
            {"id": f"{index}-2", "home_team": "C", "away_team": "D", "commence_time": "2026-01-05T02:00:00Z"},
        ]))
    chosen = select_cohort(events_by_date, max_games=6)
    assert len(chosen) == 6
    assert len({row["slate_date"] for row in chosen}) == 3


def _profile_row(season: str, day: str, pts: float, passes: float = 0) -> dict:
    return {
        "season": season,
        "player_id": "9",
        "player_name": "Jayson Tatum",
        "position": "F",
        "team_id": "2",
        "team_abbr": "BOS",
        "opponent_team_id": "5",
        "game_id": day,
        "game_date": day,
        "min": 32,
        "pts": pts,
        "reb": 6,
        "ast": 4,
        "fg3m": 2,
        "fga": 16,
        "fg3a": 7,
        "usage_percentage": 0.3,
        "rebound_chances_total": 8,
        "passes": passes,
        "possessions": 40,
        "pace": 101,
        "defensive_rating": 112,
    }


def test_fewer_than_three_dates_blocks_a_retune():
    split = chronological_split(["2026-10-28", "2026-10-29"])
    assert split["can_retune"] is False
    assert split["test"] == ["2026-10-29"]


def test_walk_forward_grade_uses_only_prior_games():
    tip = datetime(2026, 10, 29, 23, 30, tzinfo=timezone.utc)
    event = {
        "id": "evt",
        "home_team": "Boston Celtics",
        "away_team": "Chicago Bulls",
        "commence_time": tip.isoformat().replace("+00:00", "Z"),
    }
    line = OddsLine(
        book=BookName.FANDUEL,
        player_id="jayson_tatum",
        player_name="Jayson Tatum",
        prop_type=PropType.POINTS,
        line=20.5,
        over_odds=-110,
        under_odds=-110,
        game_id="evt",
        book_key="fanduel",
        data_source=DataSource.ODDS_API,
    )
    prior = [_log("2026-10-28", 10)]
    final = [_log("2026-10-29", 22)]
    rows = evaluate_snapshot(
        event=event,
        lines=[line],
        prior_logs=prior + final,
        final_logs=final,
        home_team_id="2",
        away_team_id="5",
        snapshot_at=snapshot_target(tip),
    )
    overs = [row for row in rows if row["side"] == "over" and row["variant"] == "A_opportunity"]
    assert len(overs) == 1
    assert overs[0]["result"] == "win"
    assert overs[0]["actual"] == 22
    assert overs[0]["projected"] < 18
    assert overs[0]["closing_line_value"] is None
    assert overs[0]["data_quality"]["injury_lineup"] == "limited"


def test_ledger_keeps_the_original_price(tmp_path):
    first = _prop(odds=-150, probability=0.62)
    second = _prop(odds=-180, probability=0.70)
    path = save_pregame_predictions(date(2026, 10, 31), [first], directory=str(tmp_path))
    save_pregame_predictions(date(2026, 10, 31), [second], directory=str(tmp_path))
    import json
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["odds"] == -150
    assert rows[0]["raw_model_probability"] == 0.62
    assert rows[0]["calibrated_probability"] == 0.66


def _prop(odds: int, probability: float):
    return SimpleNamespace(
        player_name="Jayson Tatum",
        player_id="9",
        team_abbr="BOS",
        opponent_abbr="CHI",
        game_id="g1",
        prop_type=PropType.POINTS,
        side=PropSide.OVER,
        line=26.5,
        best_book_key="fanduel",
        best_book=BookName.FANDUEL,
        sportsbook_odds=odds,
        implied_probability=0.60,
        true_probability=0.66,
        raw_model_probability=probability,
        calibrated_probability=0.66,
        edge=0.06,
        expected_minutes=36,
        confidence=ConfidenceTier.MEDIUM,
    )
