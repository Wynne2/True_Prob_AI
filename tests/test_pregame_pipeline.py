"""Fixture-backed pregame workflow tests without target-game results or network."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from config import reload_config
from domain.entities import Game, OddsLine, Player
from domain.enums import BookName, DataSource, InjuryStatus, PlayerRole, Position, PropType
from engine.slate_scanner import SlateScanner
from services.evaluation_store import EvaluationStore


def _player(player_id: str, status: InjuryStatus = InjuryStatus.UNREPORTED) -> Player:
    return Player(
        player_id=player_id,
        name="Pregame Player",
        team_id="home",
        team_abbr="BOS",
        position=Position.PG,
        role=PlayerRole.UNKNOWN,
        injury_status=status,
        minutes_per_game=32.0,
        points_per_game=21.0,
        rebounds_per_game=5.0,
        assists_per_game=6.0,
        last10_points=[20.0] * 10,
        last10_rebounds=[5.0] * 10,
        last10_assists=[6.0] * 10,
        last10_minutes=[32.0] * 10,
        is_starter=None,
        availability_quality="injury_feed_observed",
        data_source=DataSource.BALLDONTLIE,
    )


def _quote(player_id: str) -> OddsLine:
    return OddsLine(
        book=BookName.FANDUEL,
        book_key="fanduel",
        player_id=player_id,
        player_name="Pregame Player",
        prop_type=PropType.POINTS,
        line=20.5,
        over_odds=-110,
        under_odds=-110,
        game_id="game-1",
        fetched_at=datetime.now(timezone.utc),
        data_source=DataSource.BALLDONTLIE,
    )


def test_pregame_workflow_evaluates_historical_player_without_target_stat():
    scanner = SlateScanner()
    game = Game(
        game_id="game-1",
        home_team_id="home",
        home_team_abbr="BOS",
        away_team_id="away",
        away_team_abbr="MIA",
        game_date=date(2026, 4, 10),
        tip_off_time=datetime(2026, 4, 10, 23, tzinfo=timezone.utc),
        data_source=DataSource.BALLDONTLIE,
    )
    player = _player("player-1")

    results = scanner._evaluate_provider_pregame(
        [game],
        {"game-1": [player]},
        [_quote("player-1")],
        [PropType.POINTS],
    )

    assert results
    assert results[0].player_id == "player-1"
    assert results[0].projected_value > 0
    assert scanner.last_exclusions == []


def test_unknown_availability_is_excluded_before_modeling():
    scanner = SlateScanner()
    game = Game("game-1", "home", "BOS", "away", "MIA")
    player = _player("player-1", InjuryStatus.UNKNOWN)

    assert scanner._evaluate_provider_pregame(
        [game], {"game-1": [player]}, [_quote("player-1")], [PropType.POINTS]
    ) == []
    assert scanner.last_exclusions[0]["reason"] == "availability_unknown"


def test_scan_persists_real_player_inputs_and_exclusion_reasons(tmp_path, monkeypatch):
    ledger_path = tmp_path / "forecast.sqlite3"
    monkeypatch.setenv("EVALUATION_DB_PATH", str(ledger_path))
    reload_config()
    game = Game("game-1", "home", "BOS", "away", "MIA")
    scanner = SlateScanner()
    monkeypatch.setattr(scanner, "_pull_slate_and_odds", lambda *_: ([game], [_quote("player-1")]))
    monkeypatch.setattr(
        scanner,
        "_load_players",
        lambda *_: {"game-1": [_player("player-1"), _player("player-2", InjuryStatus.UNKNOWN)]},
    )

    forecasts = scanner.scan(game.game_date, [PropType.POINTS])

    assert forecasts
    with EvaluationStore(ledger_path)._connection() as conn:
        record = conn.execute("SELECT feature_snapshot_json FROM forecasts LIMIT 1").fetchone()
        excluded = conn.execute("SELECT reason FROM exclusions LIMIT 1").fetchone()
    assert json.loads(record[0])["player_id"] == "player-1"
    assert excluded[0] == "availability_unknown"


def test_ledger_failure_is_not_silently_reported_as_success(monkeypatch):
    scanner = SlateScanner()
    game = Game("game-1", "home", "BOS", "away", "MIA")
    monkeypatch.setattr(scanner, "_pull_slate_and_odds", lambda *_: ([game], [_quote("player-1")]))
    monkeypatch.setattr(scanner, "_load_players", lambda *_: {"game-1": [_player("player-1")]})

    def fail(*_args):
        raise OSError("ledger unavailable")

    monkeypatch.setattr(scanner, "_record_forecasts", fail)
    with pytest.raises(OSError, match="ledger unavailable"):
        scanner.scan(game.game_date, [PropType.POINTS])


def test_games_without_quotes_do_not_fetch_full_rosters(monkeypatch):
    scanner = SlateScanner()
    game = Game("game-1", "home", "BOS", "away", "MIA")
    monkeypatch.setattr(
        "data.loaders.load_players_for_game",
        lambda *_args: pytest.fail("No player lookup should be needed without quotes"),
    )

    assert scanner._load_players([game], []) == {"game-1": []}


def test_model_failure_is_not_returned_as_an_empty_slate(monkeypatch):
    from engine.prop_evaluator import _MODELS

    class _BrokenModel:
        def project(self, *_args):
            raise ValueError("invalid inputs")

    monkeypatch.setitem(_MODELS, PropType.POINTS, _BrokenModel())
    scanner = SlateScanner()
    game = Game("game-1", "home", "BOS", "away", "MIA")

    with pytest.raises(RuntimeError, match="Projection failed"):
        scanner._evaluate_provider_pregame(
            [game], {"game-1": [_player("player-1")]}, [_quote("player-1")],
            [PropType.POINTS],
        )
