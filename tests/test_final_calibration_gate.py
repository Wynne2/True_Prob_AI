"""Tests for final pre-output calibration self-check."""

from __future__ import annotations

from domain.entities import Game, Player, StatProjection
from domain.enums import (
    ConfidenceTier,
    DistributionType,
    InjuryStatus,
    PlayerRole,
    Position,
    PropSide,
    PropType,
)
from engine.final_calibration_gate import apply_final_calibration_gate
from models.projection_baseline import season_stat_for_prop


def _minimal_player() -> Player:
    return Player(
        player_id="1",
        name="Test",
        team_id="1",
        team_abbr="BOS",
        position=Position.PG,
        role=PlayerRole.STARTER,
        injury_status=InjuryStatus.ACTIVE,
        minutes_per_game=32.0,
        points_per_game=22.0,
        rebounds_per_game=6.0,
        assists_per_game=5.0,
        is_starter=True,
    )


def test_gate_passes_clean_prop():
    p = _minimal_player()
    proj = StatProjection(
        player_id=p.player_id,
        player_name=p.name,
        prop_type=PropType.POINTS,
        projected_value=22.0,
        distribution_type=DistributionType.NORMAL,
        dist_std=6.0,
        baseline_projection=21.0,
        expected_minutes=32.0,
    )
    g = Game(
        game_id="g1",
        home_team_id="h",
        home_team_abbr="BOS",
        away_team_id="a",
        away_team_abbr="MIA",
        blowout_risk=0.05,
    )
    t, conf, flags = apply_final_calibration_gate(
        p,
        g,
        True,
        PropType.POINTS,
        PropSide.OVER,
        20.5,
        proj,
        0.56,
        0.50,
        0.06,
        ConfidenceTier.MEDIUM,
        0.95,
        -110,
    )
    assert not flags
    assert t == 0.56
    assert conf == ConfidenceTier.MEDIUM


def test_extreme_projection_triggers_flag_and_shrink():
    p = _minimal_player()
    proj = StatProjection(
        player_id=p.player_id,
        player_name=p.name,
        prop_type=PropType.POINTS,
        projected_value=38.0,
        distribution_type=DistributionType.NORMAL,
        dist_std=7.0,
        baseline_projection=22.0,
        expected_minutes=32.0,
    )
    g = Game(
        game_id="g1",
        home_team_id="h",
        home_team_abbr="BOS",
        away_team_id="a",
        away_team_abbr="MIA",
    )
    t, conf, flags = apply_final_calibration_gate(
        p,
        g,
        True,
        PropType.POINTS,
        PropSide.OVER,
        24.5,
        proj,
        0.62,
        0.48,
        0.14,
        ConfidenceTier.HIGH,
        0.9,
        -110,
    )
    assert flags
    assert t < 0.62
    assert conf != ConfidenceTier.HIGH or "fg_" in "".join(flags)


def test_season_stat_helper():
    p = _minimal_player()
    assert season_stat_for_prop(p, PropType.POINTS) == 22.0
