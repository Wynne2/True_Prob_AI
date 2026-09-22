"""Fixture-backed pregame workflow tests without target-game results or network."""

from __future__ import annotations

from datetime import date, datetime, timezone

from domain.entities import Game, OddsLine, Player
from domain.enums import BookName, DataSource, InjuryStatus, PlayerRole, Position, PropType
from engine.slate_scanner import SlateScanner


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
