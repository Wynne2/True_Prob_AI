"""Live-scan wiring: two providers, both sides of a prop, DvP row shape."""

from data.loaders.nba_api_loader import map_box_score_to_dvp_log
from domain.constants import PROP_ALIAS_MAP
from domain.enums import PropType
from providers.odds_api_provider import PLAYER_PROP_MARKET_BATCHES


def test_documented_prop_markets_include_alternates_and_pra():
    joined = ",".join(PLAYER_PROP_MARKET_BATCHES)
    for key in (
        "player_points",
        "player_points_alternate",
        "player_rebounds_alternate",
        "player_assists_alternate",
        "player_threes_alternate",
        "player_points_rebounds_assists",
        "player_steals",
        "player_blocks",
        "player_turnovers",
        "player_double_double",
        "player_points_rebounds",
    ):
        assert key in joined
        assert PROP_ALIAS_MAP[key] in {
            PropType.POINTS,
            PropType.REBOUNDS,
            PropType.ASSISTS,
            PropType.THREES,
            PropType.PRA,
            PropType.STEALS,
            PropType.BLOCKS,
            PropType.TURNOVERS,
            PropType.DOUBLE_DOUBLE,
            PropType.POINTS_REBOUNDS,
        }


def test_box_score_opponent_is_the_other_team():
    row = {
        "pts": 22,
        "reb": 8,
        "ast": 4,
        "stl": 1,
        "blk": 0,
        "turnover": 3,
        "player": {"id": 15, "first_name": "A", "last_name": "B", "position": "F"},
        "team": {"id": 2, "abbreviation": "BOS"},
        "game": {
            "date": "2026-10-30T00:00:00Z",
            "home_team": {"id": 2},
            "visitor_team": {"id": 9},
        },
    }
    log = map_box_score_to_dvp_log(row)
    assert log["player_id"] == "15"
    assert log["opponent_team_id"] == "9"
    assert log["tov"] == 3
    assert log["position"] == "F"
    assert log["pts"] == 22


def test_team_pace_request_includes_required_season_type(monkeypatch):
    from data.loaders import nba_api_loader as loader

    captured: dict = {}

    class _Client:
        def paginate(self, path, params):
            captured["path"] = path
            captured["params"] = params
            return []

    monkeypatch.setattr(loader, "_client", lambda: _Client())
    monkeypatch.setattr(loader._CACHE, "get", lambda key: None)
    monkeypatch.setattr(loader._CACHE, "set", lambda *args, **kwargs: None)
    loader.fetch_team_pace_batch(season="2026-27", date_str="2026-10-31")
    assert captured["path"] == "nba/v1/team_season_averages/general"
    assert captured["params"]["season"] == 2026
    assert captured["params"]["season_type"] == "regular"
    assert captured["params"]["type"] == "advanced"


def test_registry_does_not_register_other_sports_apis():
    import inspect
    from providers import provider_registry as reg

    source = inspect.getsource(reg._build_providers)
    assert "SportradarProvider" not in source
    assert "SportsDataIOProvider" not in source
    assert "BallDontLieProvider" in source
    assert "OddsAPIProvider" in source
