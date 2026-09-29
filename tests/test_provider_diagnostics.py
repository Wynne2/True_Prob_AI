"""Provider diagnostics should issue uncached, bounded calls without global patches."""

from datetime import date, datetime, timezone

import requests
import pytest

from providers.balldontlie_client import BallDontLieRequestError
from services.provider_diagnostics import diagnose_provider


class _Client:
    def __init__(self, *, games):
        self.games = games
        self.calls = []

    def get_all(self, path, *, params):
        self.calls.append((path, params))
        if path.endswith("/games"):
            return self.games, datetime.now(timezone.utc)
        return [{"id": "quote-1"}], datetime.now(timezone.utc)


def test_diagnostics_probe_first_game_and_keep_preview_bounded():
    client = _Client(games=[{"id": index} for index in range(5)])
    original_get = requests.Session.get

    checks = diagnose_provider(date(2026, 4, 10), "fixture-key", client=client)

    assert requests.Session.get is original_get
    assert len(checks) == 2
    assert checks[0]["records"] == 5
    assert len(checks[0]["preview"]) == 2
    assert client.calls[1] == ("/nba/v2/odds/player_props", {"game_id": "0"})


def test_diagnostics_do_not_make_prop_request_for_empty_schedule():
    client = _Client(games=[])
    checks = diagnose_provider(date(2026, 4, 10), "fixture-key", client=client)
    assert len(checks) == 1
    assert checks[0]["records"] == 0


def test_diagnostics_surface_provider_failures():
    class _FailingClient:
        def get_all(self, *_args, **_kwargs):
            raise BallDontLieRequestError("rate_limited", "provider throttled")

    with pytest.raises(BallDontLieRequestError, match="provider throttled"):
        diagnose_provider(date(2026, 4, 10), "fixture-key", client=_FailingClient())
