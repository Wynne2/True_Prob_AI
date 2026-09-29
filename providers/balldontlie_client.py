"""Authenticated, bounded BALLDONTLIE HTTP client.

The client is deliberately transport-only. It preserves provider payloads and
timestamps for the adapter, retries only transient failures, and never turns a
provider failure into an empty successful response.
"""

from __future__ import annotations

import random
import time
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, ClassVar, Optional

import requests

from config import BallDontLieConfig
from services.cache_service import get_cache


class BallDontLieRequestError(RuntimeError):
    """A non-empty provider failure suitable for UI and activity reporting."""

    def __init__(self, code: str, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ProviderPayload:
    """Provider JSON with a distinct retrieval time."""

    body: dict[str, Any]
    fetched_at: datetime


class BallDontLieClient:
    """One TLS-verified session, bounded retries, cursors, and provider cache."""

    _TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
    _budget_lock: ClassVar[Lock] = Lock()
    _request_windows: ClassVar[dict[str, tuple[float, int]]] = {}

    def __init__(self, api_key: str, config: BallDontLieConfig) -> None:
        self._config = config
        self._base_url = config.base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"Authorization": api_key, "Accept": "application/json"})
        self._budget_key = hashlib.sha256(api_key.encode()).hexdigest()
        cache_scope = hashlib.sha256(f"{self._base_url}\0{api_key}".encode()).hexdigest()[:16]
        self._cache = get_cache(
            f"balldontlie_{cache_scope}", default_ttl=config.stable_cache_seconds,
        )

    @staticmethod
    def _canonical_params(params: dict[str, Any]) -> tuple[tuple[str, str], ...]:
        pairs: list[tuple[str, str]] = []
        for key, value in sorted(params.items()):
            values = value if isinstance(value, (list, tuple)) else [value]
            pairs.extend((key, str(item)) for item in values)
        return tuple(pairs)

    def _budget_wait(self) -> None:
        with self._budget_lock:
            now = time.monotonic()
            started, count = self._request_windows.get(self._budget_key, (now, 0))
            if now - started >= 60:
                started, count = now, 0
            if count >= self._config.request_budget_per_minute:
                raise BallDontLieRequestError(
                    "request_budget_exhausted",
                    "Configured BALLDONTLIE request budget exhausted; retry after the next minute window.",
                )
            self._request_windows[self._budget_key] = (started, count + 1)

    def get(
        self,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        cache_ttl: int = 0,
    ) -> ProviderPayload:
        params = params or {}
        cache_key = f"v1:{path}:{self._canonical_params(params)!r}"
        if cache_ttl:
            cached = self._cache.get(cache_key)
            if cached:
                return ProviderPayload(
                    body=cached["body"],
                    fetched_at=datetime.fromisoformat(cached["fetched_at"]),
                )

        url = f"{self._base_url}/{path.lstrip('/')}"
        for attempt in range(self._config.max_retries + 1):
            self._budget_wait()
            try:
                response = self._session.get(url, params=params, timeout=self._config.timeout)
            except requests.Timeout as exc:
                if attempt == self._config.max_retries:
                    raise BallDontLieRequestError("timeout", "BALLDONTLIE request timed out.") from exc
                self._sleep(attempt)
                continue
            except requests.RequestException as exc:
                raise BallDontLieRequestError("network_error", "BALLDONTLIE network request failed.") from exc

            if response.status_code in (401, 403):
                raise BallDontLieRequestError(
                    "authentication_or_entitlement",
                    "BALLDONTLIE rejected the credential or plan entitlement.",
                    status_code=response.status_code,
                )
            if response.status_code not in self._TRANSIENT_STATUS and not response.ok:
                raise BallDontLieRequestError(
                    "http_error",
                    f"BALLDONTLIE returned HTTP {response.status_code}.",
                    status_code=response.status_code,
                )
            if response.status_code in self._TRANSIENT_STATUS:
                if attempt == self._config.max_retries:
                    raise BallDontLieRequestError(
                        "rate_limited" if response.status_code == 429 else "transient_failure",
                        f"BALLDONTLIE remained unavailable after {attempt + 1} attempt(s).",
                        status_code=response.status_code,
                    )
                retry_after = response.headers.get("Retry-After")
                self._sleep(attempt, retry_after)
                continue
            try:
                body = response.json()
            except ValueError as exc:
                raise BallDontLieRequestError("invalid_json", "BALLDONTLIE returned invalid JSON.") from exc
            if not isinstance(body, dict):
                raise BallDontLieRequestError("invalid_payload", "BALLDONTLIE payload was not an object.")
            payload = ProviderPayload(body=body, fetched_at=datetime.now(timezone.utc))
            if cache_ttl:
                self._cache.set(
                    cache_key,
                    {"body": body, "fetched_at": payload.fetched_at.isoformat()},
                    ttl_seconds=cache_ttl,
                )
            return payload
        raise AssertionError("Retry loop exhausted unexpectedly")

    def get_all(
        self,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        cache_ttl: int = 0,
    ) -> tuple[list[dict[str, Any]], datetime]:
        """Fetch documented cursor pages and retain the newest retrieval time."""
        base_params = dict(params or {})
        all_rows: list[dict[str, Any]] = []
        cursor: Any = None
        fetched_at: Optional[datetime] = None
        while True:
            page_params = dict(base_params)
            if cursor is not None:
                page_params["cursor"] = cursor
            payload = self.get(path, params=page_params, cache_ttl=cache_ttl)
            fetched_at = payload.fetched_at
            rows = payload.body.get("data", [])
            if not isinstance(rows, list):
                raise BallDontLieRequestError("invalid_payload", "BALLDONTLIE data field was not a list.")
            all_rows.extend(row for row in rows if isinstance(row, dict))
            cursor = (payload.body.get("meta") or {}).get("next_cursor")
            if not cursor:
                return all_rows, fetched_at

    @staticmethod
    def _sleep(attempt: int, retry_after: Optional[str] = None) -> None:
        try:
            delay = float(retry_after) if retry_after else 0.5 * (2**attempt)
        except ValueError:
            delay = 0.5 * (2**attempt)
        time.sleep(min(30.0, delay) + random.uniform(0.0, 0.25))
