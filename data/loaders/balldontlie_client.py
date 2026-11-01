"""
BALLDONTLIE NBA GOAT HTTP client.

Auth header is ``Authorization: <key>`` from ``BALLDONTLIE_API_KEY`` or ``BDL_API_KEY``.
The key is never logged. Cursor pagination follows ``meta.next_cursor``.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

from config import get_bdl_config, get_credentials

logger = logging.getLogger(__name__)


class BallDontLieError(RuntimeError):
    pass


class BallDontLieAuthError(BallDontLieError):
    pass


def _flatten_params(params: dict[str, Any] | None) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            name = key if str(key).endswith("[]") else f"{key}[]"
            for item in value:
                items.append((name, item))
        else:
            items.append((key, value))
    return items


class BallDontLieClient:
    """Rate-limited GET client with retries and cursor pagination."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        requests_per_minute: Optional[int] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        cfg = get_bdl_config()
        self._key = api_key if api_key is not None else (get_credentials().balldontlie_key or "")
        self._base = (base_url or cfg.base_url).rstrip("/")
        rpm = requests_per_minute if requests_per_minute is not None else cfg.requests_per_minute
        self._min_interval = 60.0 / max(int(rpm), 1)
        self._timeout = timeout if timeout is not None else cfg.timeout
        self._max_retries = max_retries if max_retries is not None else cfg.max_retries
        self._session = session or requests.Session()
        self._last_request_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._key)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._key}

    def _wait_turn(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """One authenticated GET. Raises on auth failure. Retries 429 and 5xx."""
        if not self._key:
            raise BallDontLieAuthError("BALLDONTLIE_API_KEY or BDL_API_KEY is not set")

        url = f"{self._base}/{path.lstrip('/')}"
        query = _flatten_params(params)
        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            self._wait_turn()
            self._last_request_at = time.monotonic()
            try:
                response = self._session.get(
                    url, headers=self._headers(), params=query, timeout=self._timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(8.0, 0.5 * (2 ** attempt)))
                continue

            status = response.status_code
            if status in (401, 403):
                raise BallDontLieAuthError(
                    f"BALLDONTLIE rejected the API key (HTTP {status}) for {path}"
                )
            if status == 429 or status >= 500:
                last_error = BallDontLieError(f"HTTP {status} for {path}")
                time.sleep(min(8.0, 0.5 * (2 ** attempt)))
                continue
            if status >= 400:
                detail = " ".join((response.text or "").split())[:180]
                raise BallDontLieError(f"HTTP {status} for {path}: {detail}")
            body = response.json()
            if not isinstance(body, dict):
                raise BallDontLieError(f"Unexpected BALLDONTLIE payload for {path}")
            return body

        raise BallDontLieError(f"BALLDONTLIE request failed for {path}: {last_error}")

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Follow ``meta.next_cursor`` and drop duplicate stable ids."""
        params = dict(params or {})
        params.setdefault("per_page", get_bdl_config().per_page)
        cursor: Any = None
        rows: list[dict] = []
        seen: set[tuple] = set()

        while True:
            page_params = dict(params)
            if cursor is not None:
                page_params["cursor"] = cursor
            body = self.get(path, page_params)
            for item in body.get("data") or []:
                if not isinstance(item, dict):
                    continue
                key = _stable_row_key(item)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(item)
            cursor = (body.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
        return rows


def _stable_row_key(item: dict) -> tuple:
    player = item.get("player") if isinstance(item.get("player"), dict) else {}
    game = item.get("game") if isinstance(item.get("game"), dict) else {}
    if player.get("id") is not None and game.get("id") is not None:
        return ("player_game", player.get("id"), game.get("id"))
    if player.get("id") is not None and item.get("season") is not None:
        return ("player_season", player.get("id"), item.get("season"), item.get("type"))
    if item.get("id") is not None and "player" not in item:
        return ("id", item.get("id"))
    if player.get("id") is not None:
        return ("player", player.get("id"))
    team = item.get("team") if isinstance(item.get("team"), dict) else {}
    if team.get("id") is not None:
        return ("team", team.get("id"), item.get("season"))
    return ("fallback", tuple(sorted((str(k), str(v)) for k, v in item.items() if not isinstance(v, (dict, list)))))
