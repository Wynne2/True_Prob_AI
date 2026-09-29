"""Transport contracts: BDL-only host, header auth, pagination, and failures."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from config import BallDontLieConfig
from providers.balldontlie_client import BallDontLieClient, BallDontLieRequestError


class _Response:
    def __init__(self, status_code: int, body: dict, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.ok = status_code < 400

    def json(self):
        return self._body


def _client() -> BallDontLieClient:
    return BallDontLieClient(
        "secret",
        BallDontLieConfig(base_url="https://api.balldontlie.io", max_retries=0),
    )


def test_client_uses_authorization_header_and_cursor_pagination(monkeypatch):
    client = _client()
    calls = []
    responses = [
        _Response(200, {"data": [{"id": 1}], "meta": {"next_cursor": 2}}),
        _Response(200, {"data": [{"id": 2}], "meta": {}}),
    ]

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(client._session, "get", get)
    rows, _ = client.get_all("/nba/v1/games", params={"per_page": 1})

    assert [row["id"] for row in rows] == [1, 2]
    assert all(url.startswith("https://api.balldontlie.io/nba/v1/games") for url, _ in calls)
    assert client._session.headers["Authorization"] == "secret"
    assert calls[1][1]["params"]["cursor"] == 2


@pytest.mark.parametrize("status,code", [(401, "authentication_or_entitlement"), (403, "authentication_or_entitlement"), (429, "rate_limited")])
def test_client_surfaces_provider_failures_without_empty_success(monkeypatch, status, code):
    client = _client()
    monkeypatch.setattr(client._session, "get", lambda *_args, **_kwargs: _Response(status, {}))

    with pytest.raises(BallDontLieRequestError) as raised:
        client.get("/nba/v1/games")
    assert raised.value.code == code


def test_client_preserves_retrieval_timestamp(monkeypatch):
    client = _client()
    monkeypatch.setattr(client._session, "get", lambda *_args, **_kwargs: _Response(200, {"data": []}))
    payload = client.get("/nba/v1/games")
    assert payload.fetched_at.tzinfo == timezone.utc


def test_request_budget_is_shared_across_client_instances(monkeypatch):
    config = BallDontLieConfig(
        base_url="https://api.balldontlie.io", max_retries=0, request_budget_per_minute=1,
    )
    first = BallDontLieClient("shared-budget-fixture", config)
    second = BallDontLieClient("shared-budget-fixture", config)
    monkeypatch.setattr(first._session, "get", lambda *_args, **_kwargs: _Response(200, {"data": []}))
    monkeypatch.setattr(second._session, "get", lambda *_args, **_kwargs: _Response(200, {"data": []}))

    first.get("/nba/v1/games")
    with pytest.raises(BallDontLieRequestError) as failure:
        second.get("/nba/v1/games")
    assert failure.value.code == "request_budget_exhausted"


def test_cached_payloads_are_isolated_by_provider_account_and_host():
    config = BallDontLieConfig(base_url="https://api.balldontlie.io")
    first = BallDontLieClient("account-one", config)
    same = BallDontLieClient("account-one", config)
    other = BallDontLieClient("account-two", config)
    host = BallDontLieClient(
        "account-one",
        BallDontLieConfig(base_url="https://other.example.test"),
    )

    assert first._cache is same._cache
    assert first._cache is not other._cache
    assert first._cache is not host._cache
