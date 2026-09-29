"""Captured-shape contracts for the BALLDONTLIE-only adapter."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from config import get_bdl_config
from domain.entities import Game
from domain.enums import BookName, DataSource, InjuryStatus, PropType
from providers.balldontlie_client import BallDontLieRequestError, ProviderPayload
from providers.balldontlie_provider import BallDontLieProvider
from providers.provider_registry import ProviderRegistry


def _game() -> Game:
    return Game(
        game_id="100",
        home_team_id="1",
        home_team_abbr="BOS",
        away_team_id="2",
        away_team_abbr="MIA",
        game_date=date(2026, 4, 10),
        tip_off_time=datetime(2026, 4, 10, 23, 0, tzinfo=timezone.utc),
        data_source=DataSource.BALLDONTLIE,
    )


def _provider() -> BallDontLieProvider:
    provider = BallDontLieProvider("test-key")
    provider._games = {"100": _game()}
    return provider


def test_nested_pra_market_is_normalized_with_provider_times():
    provider = _provider()
    fetched_at = datetime(2026, 4, 10, 18, tzinfo=timezone.utc)
    rows = provider._map_prop_row(
        {
            "id": "quote-1",
            "type": "player_points",
            "updated_at": "2026-04-10T17:59:00Z",
            "player": {"id": 22, "first_name": "Jay", "last_name": "Player"},
            "market": {"type": "over_under"},
            "odds": [{
                "vendor": "FanDuel",
                "over": {"line": 24.5, "price": -110},
                "under": {"line": 24.5, "price": -110},
            }],
        },
        _game(),
        fetched_at,
    )

    assert len(rows) == 1
    quote = rows[0]
    assert quote.prop_type == PropType.POINTS
    assert quote.book == BookName.FANDUEL
    assert quote.book_key == "fanduel"
    assert quote.source_updated_at == datetime(2026, 4, 10, 17, 59, tzinfo=timezone.utc)
    assert quote.fetched_at == fetched_at
    assert quote.provider_quote_id == "quote-1"


def test_unknown_real_book_is_other_not_sample():
    rows = _provider()._map_prop_row(
        {
            "type": "player_assists",
            "player": {"id": 22, "first_name": "Jay", "last_name": "Player"},
            "market": {"type": "over_under"},
            "odds": [{
                "vendor": "NewBook",
                "over": {"line": 6.5, "price": 105},
                "under": {"line": 6.5, "price": -125},
            }],
        },
        _game(),
        datetime.now(timezone.utc),
    )
    assert rows[0].book == BookName.OTHER
    assert rows[0].book_key == "newbook"


def test_unsupported_and_incomplete_markets_are_rejected():
    provider = _provider()
    assert provider._map_prop_row(
        {"type": "player_turnovers", "player": {"id": 1}, "odds": []},
        _game(),
        datetime.now(timezone.utc),
    ) == []
    assert provider._map_prop_row(
        {
            "type": "player_rebounds",
            "player": {"id": 1},
            "market": {"type": "over_under"},
            "odds": [{"vendor": "fanduel", "over": {"line": 8.5, "price": -110}}],
        },
        _game(),
        datetime.now(timezone.utc),
    ) == []


def test_injury_feed_failure_blocks_participant_resolution(monkeypatch):
    provider = _provider()
    provider._active_players = lambda: {
        "22": {
            "id": 22,
            "first_name": "Jay",
            "last_name": "Player",
            "position": "PG",
            "team": {"id": 1, "abbreviation": "BOS"},
        }
    }
    provider.get_player_recent_form = lambda *_args, **_kwargs: [
        {"game_date": "2026-04-08T00:00:00Z", "pts": 20, "reb": 4, "ast": 5, "min": "32:00"},
        {"game_date": "2026-04-07T00:00:00Z", "pts": 18, "reb": 4, "ast": 5, "min": "31:00"},
        {"game_date": "2026-04-06T00:00:00Z", "pts": 22, "reb": 4, "ast": 5, "min": "33:00"},
    ]
    def fail():
        raise BallDontLieRequestError("authentication_or_entitlement", "no injury access")
    provider._load_injuries = fail

    with pytest.raises(BallDontLieRequestError, match="no injury access"):
        provider.get_players_for_game("100", {"22"})


def test_live_mode_with_no_key_never_falls_back_to_sample(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("BALLDONTLIE_API_KEY", "")
    from config import reload_config
    reload_config()

    registry = ProviderRegistry.build()
    assert registry.active_providers == []
    with pytest.raises(BallDontLieRequestError) as failure:
        registry.get_games_for_date(date(2026, 4, 10))
    assert failure.value.code == "missing_credential"
    assert registry.status()["reason"] == "missing_credential"


def test_live_provider_failure_is_not_reported_as_an_empty_slate(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("BALLDONTLIE_API_KEY", "fixture-key")
    from config import reload_config
    reload_config()
    registry = ProviderRegistry.build()

    def fail(_date):
        raise BallDontLieRequestError("rate_limited", "provider throttled")

    monkeypatch.setattr(registry._provider, "get_games_for_date", fail)
    with pytest.raises(BallDontLieRequestError, match="provider throttled"):
        registry.get_games_for_date(date(2026, 4, 10))
    assert registry.status() == {
        "mode": "live", "state": "error", "reason": "rate_limited",
    }
