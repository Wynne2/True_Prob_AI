"""BALLDONTLIE client, field mapping, opportunity model, and grading."""

from datetime import datetime, timezone

import pytest

from data.loaders.balldontlie_client import BallDontLieAuthError, BallDontLieClient
from domain.constants import balldontlie_season_start_year
from domain.entities import Game, Player
from domain.enums import PlayerRole, Position
from engine.backtest_grade import brier_score, game_is_pregame, grade_prop, realized_roi
from models.assists_model import AssistsModel


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, headers, params, timeout):
        self.calls.append({"url": url, "headers": headers, "params": list(params)})
        cursor = dict(params).get("cursor")
        key = 0 if cursor is None else int(cursor)
        return _Resp(self.pages[key])


def test_authorization_header_is_raw_key_not_logged(monkeypatch):
    session = _Session({
        0: {"data": [{"id": 1}], "meta": {"next_cursor": None}},
    })
    client = BallDontLieClient(api_key="secret-key", requests_per_minute=10_000, session=session)
    rows = client.paginate("v1/players", {"per_page": 100})
    assert rows == [{"id": 1}]
    assert session.calls[0]["headers"]["Authorization"] == "secret-key"
    assert "Bearer" not in session.calls[0]["headers"]["Authorization"]


def test_cursor_pagination_and_dedup():
    session = _Session({
        0: {"data": [{"id": 1}, {"id": 2}], "meta": {"next_cursor": 1}},
        1: {"data": [{"id": 2}, {"id": 3}], "meta": {"next_cursor": None}},
    })
    client = BallDontLieClient(api_key="k", requests_per_minute=10_000, session=session)
    rows = client.paginate("v1/teams")
    assert [row["id"] for row in rows] == [1, 2, 3]


def test_auth_failure_raises_without_embedding_key():
    session = _Session({})
    session.get = lambda url, headers, params, timeout: _Resp({}, status=401)
    client = BallDontLieClient(api_key="secret-key", requests_per_minute=10_000, session=session)
    with pytest.raises(BallDontLieAuthError) as exc:
        client.get("v1/players")
    assert "secret-key" not in str(exc.value)


def test_2026_27_season_is_start_year_2026():
    assert balldontlie_season_start_year("2026-27") == 2026
    assert balldontlie_season_start_year(2026) == 2026


def test_tracking_missing_field_is_not_zero(monkeypatch):
    from data.loaders import nba_api_loader as loader

    class _Mem:
        def get(self, key):
            return None

        def set(self, *args, **kwargs):
            return None

    class _FakeClient:
        def paginate(self, path, params):
            assert params["type"] == "passing"
            return [{
                "player": {
                    "id": 9,
                    "first_name": "Ty",
                    "last_name": "Test",
                    "team": {"id": 2, "abbreviation": "BOS"},
                },
                "stats": {"ast": 0},
            }]

    monkeypatch.setattr(loader, "_CACHE", _Mem())
    monkeypatch.setattr(loader, "_client", lambda: _FakeClient())
    rows = loader.fetch_today_player_tracking_batch(
        season="2026-27", pt_measure_type="Passing", date_str="test-day",
    )
    assert rows[0]["ast"] == 0.0
    assert rows[0]["potential_ast"] is None
    assert "potential_ast" in rows[0]["missing_fields"]


def test_potential_assists_change_the_assists_projection():
    game = Game(
        game_id="g",
        home_team_id="1",
        home_team_abbr="DEN",
        away_team_id="2",
        away_team_abbr="MIN",
    )
    base = Player(
        player_id="1",
        name="Passer",
        team_id="1",
        team_abbr="DEN",
        position=Position.PG,
        role=PlayerRole.STARTER,
        is_starter=True,
        minutes_per_game=32,
        assists_per_game=8,
        potential_assists=0,
    )
    low = Player(**{**base.__dict__})
    high = Player(**{**base.__dict__, "potential_assists": 60.0})
    model = AssistsModel()
    low_mean = model.project(low, game, None, True).projected_value
    high_mean = model.project(high, game, None, True).projected_value
    assert high_mean > low_mean


def test_integer_push_and_lookahead():
    assert grade_prop(20, 20, "over") == "push"
    assert grade_prop(21, 20.5, "over") == "win"
    assert grade_prop(20, 20.5, "over") == "loss"
    tip = datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc)
    earlier = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
    later = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    assert game_is_pregame(tip, earlier) is True
    assert game_is_pregame(tip, later) is False
    assert brier_score(0.8, 1) == pytest.approx(0.04)
    assert realized_roi(None, -200, "win") == pytest.approx(0.5)
    assert realized_roi(None, -200, "push") == 0.0


def test_player_logs_reuse_season_pull_across_window_sizes(monkeypatch):
    from data.loaders import nba_api_loader as loader

    class _Mem(dict):
        def get(self, key):
            return dict.get(self, key)

        def set(self, key, value, *args, **kwargs):
            self[key] = value

    calls = []

    class _FakeClient:
        def paginate(self, path, params):
            calls.append(params["player_ids"])
            return [
                {"game": {"id": i, "date": f"2026-10-{10 + i:02d}"}, "pts": 10 + i, "min": "30:00"}
                for i in range(15)
            ]

    monkeypatch.setattr(loader, "_CACHE", _Mem())
    monkeypatch.setattr(loader, "_client", lambda: _FakeClient())
    ten = loader.fetch_recent_player_logs_batch(["7"], last_n=10)["7"]
    twenty = loader.fetch_recent_player_logs_batch(["7"], last_n=20)["7"]
    assert len(calls) == 1
    assert len(ten) == 10 and len(twenty) == 15
    assert ten[0]["game_date"] == "2026-10-24"