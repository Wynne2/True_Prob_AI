"""Pregame-only slate: exclude in-progress / final games."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from domain.entities import Game
from utils.date_utils import filter_pregame_games, game_is_pregame, parse_iso_datetime


def _g(
    *,
    status: str = "",
    tip: datetime | None = None,
) -> Game:
    return Game(
        game_id="1",
        home_team_id="h",
        home_team_abbr="H",
        away_team_id="a",
        away_team_abbr="A",
        tip_off_time=tip,
        status=status,
    )


def test_final_status_not_pregame():
    g = _g(status="Final")
    assert not game_is_pregame(g)


def test_in_progress_not_pregame():
    g = _g(status="InProgress")
    assert not game_is_pregame(g)


def test_tip_in_past_not_pregame():
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    g = _g(tip=past)
    assert not game_is_pregame(g)


def test_tip_in_future_is_pregame():
    future = datetime.now(timezone.utc) + timedelta(hours=3)
    g = _g(tip=future)
    assert game_is_pregame(g)


def test_filter_keeps_only_pregame():
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    games = [_g(tip=future), _g(tip=past), _g(status="Final")]
    assert len(filter_pregame_games(games)) == 1


def test_naive_tip_is_eastern_local_not_utc():
    """Schedule APIs often send 17:30 without TZ = 5:30 PM ET, not UTC."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        pytest.skip("zoneinfo required")
    et = ZoneInfo("America/New_York")
    tip_naive = datetime(2026, 4, 19, 17, 30)
    g = _g(tip=tip_naive)
    before = datetime(2026, 4, 19, 16, 45, tzinfo=et)
    assert game_is_pregame(g, now=before)
    after = datetime(2026, 4, 19, 18, 0, tzinfo=et)
    assert not game_is_pregame(g, now=after)


def test_parse_iso_z_is_utc_odds_api():
    dt = parse_iso_datetime("2026-04-19T21:30:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert "UTC" in str(dt.tzinfo) or dt.utcoffset() == timedelta(0)


def test_parse_iso_naive_gets_eastern():
    dt = parse_iso_datetime("2026-04-19T17:30:00")
    assert dt is not None
    assert dt.tzinfo is not None
