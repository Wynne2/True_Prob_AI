from copy import deepcopy

import pytest

from domain.entities import Game, Player
from domain.enums import InjuryStatus, PlayerRole, Position
from domain.feature_vector import FeatureVector, GameObservation
from engine.market_calibration import calibrate_true_probability
from engine.prop_evaluator import _hydrate_player_from_feature_vector
from models.minutes_model import MinutesModel


def _player() -> Player:
    return Player(
        player_id="player-1",
        name="Player One",
        team_id="team-1",
        team_abbr="BOS",
        position=Position.PG,
        role=PlayerRole.STARTER,
        injury_status=InjuryStatus.ACTIVE,
        minutes_per_game=34.0,
        points_per_game=22.0,
        last10_points=[10.0, 11.0, 12.0],
        last10_minutes=[30.0, 30.0, 30.0],
    )


def test_hydration_does_not_mutate_source_player_and_preserves_observations():
    player = _player()
    original = deepcopy(player)
    observations = tuple(
        GameObservation(f"g-{index}", "", 32.0, points=float(value))
        for index, value in enumerate(range(10, 20))
    )
    fv = FeatureVector(
        player_id=player.player_id,
        prop_type="points",
        season_avg=22.0,
        projected_minutes=28.0,
        recent_5_avg=99.0,
        recent_10_avg=99.0,
        recent_observations=observations,
    )

    hydrated = _hydrate_player_from_feature_vector(player, fv)

    assert player == original
    assert hydrated is not player
    assert hydrated.last10_points == list(range(10, 20))
    assert hydrated.last10_minutes == [32.0] * 10
    assert hydrated.expected_minutes_override == 28.0


def test_game_specific_minutes_change_projection_input():
    game = Game(
        game_id="game-1",
        home_team_id="team-1",
        home_team_abbr="BOS",
        away_team_id="team-2",
        away_team_abbr="MIA",
    )
    low = _player()
    high = _player()
    low.expected_minutes_override = 12.0
    high.expected_minutes_override = 40.0

    minutes = MinutesModel()
    assert minutes.project(low, game) < minutes.project(high, game)


def test_market_calibration_is_monotonic_at_previous_threshold_boundary():
    low, _ = calibrate_true_probability(0.569, 0.45, 122)
    high, _ = calibrate_true_probability(0.571, 0.45, 122)
    assert high > low
