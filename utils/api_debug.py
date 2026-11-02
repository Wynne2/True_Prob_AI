"""
Shared API debug / diagnostics utilities.

Used by both the CLI (main.py --debug-api) and the Streamlit UI
(🔬 API Debug tab) to capture and inspect raw HTTP responses from
every configured data provider.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from typing import Generator
from urllib.parse import parse_qs, quote, quote_plus, urlencode, urlparse, urlunparse

# ---------------------------------------------------------------------------
# URL sanitisation
# ---------------------------------------------------------------------------

_SENSITIVE_PARAMS = ("apiKey", "api_key", "key")
_SENSITIVE_PARAM_NAMES = frozenset(param.lower() for param in _SENSITIVE_PARAMS)
_SENSITIVE_QUERY = re.compile(r"""(?i)(\b(?:api_?key|key)=)[^&;\s'\"<>\])]+""")
_BEARER_TOKEN = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._\-~+/=]+")
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")


def redact_sensitive_text(text: str, sensitive_values: Iterable[str] = ()) -> str:
    """Redact API-key query parameters, bearer tokens, OpenAI keys and known key values."""
    redacted = _SENSITIVE_QUERY.sub(r"\1***", text)
    redacted = _BEARER_TOKEN.sub(r"\1***", redacted)
    redacted = _OPENAI_KEY.sub("sk-***", redacted)
    for value in sensitive_values:
        if value:
            for encoded in {value, quote(value, safe=""), quote_plus(value)}:
                redacted = redacted.replace(encoded, "***")
    return redacted


def _redact_data(data: object, sensitive_values: list[str]) -> object:
    if isinstance(data, str):
        return redact_sensitive_text(data, sensitive_values)
    if isinstance(data, list):
        return [_redact_data(item, sensitive_values) for item in data]
    if isinstance(data, dict):
        return {
            redact_sensitive_text(key, sensitive_values) if isinstance(key, str) else key:
            _redact_data(value, sensitive_values)
            for key, value in data.items()
        }
    return data


def redact_url(url: str) -> str:
    """Return *url* with known API-key query params replaced by '***'."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    for param in qs:
        if param.lower() in _SENSITIVE_PARAM_NAMES:
            qs[param] = ["***"]
    redacted_query = urlencode(
        {k: v[0] if len(v) == 1 else v for k, v in qs.items()}, doseq=True
    )
    return urlunparse(parsed._replace(query=redacted_query))


def provider_label_from_url(url: str) -> str:
    """Best-effort provider name derived from the request URL hostname."""
    host = urlparse(url).hostname or url
    mapping = {
        "api.the-odds-api.com": "The Odds API",
        "kalshi.com": "Kalshi",
        "polymarket.com": "Polymarket",
        "api.sportsdata.io": "SportsDataIO",
        "api.sportradar.com": "Sportradar",
        "api.fantasypros.com": "FantasyPros",
        "api.nba.com": "NBA Official",
        "www.statmuse.com": "StatMuse",
    }
    for fragment, label in mapping.items():
        if fragment in host:
            return label
    return host


# ---------------------------------------------------------------------------
# Response capture context manager
# ---------------------------------------------------------------------------

def _preview(data: object, max_list: int = 5) -> object:
    """Return a size-limited preview of *data* suitable for display."""
    if isinstance(data, list):
        preview = data[:max_list]
        if len(data) > max_list:
            preview = list(preview) + [f"… ({len(data) - max_list} more items)"]
        return preview
    if isinstance(data, dict):
        out: dict = {}
        for k, v in data.items():
            if isinstance(v, list) and len(v) > max_list:
                out[k] = v[:max_list] + [f"… ({len(v) - max_list} more)"]
            elif isinstance(v, str) and len(v) > 300:
                out[k] = v[:300] + "…"
            else:
                out[k] = v
        return out
    return data


@contextmanager
def capture_api_responses(max_list: int = 5) -> Generator[list[dict], None, None]:
    """
    Context manager that intercepts every HTTP GET call made inside the
    ``with`` block — both bare ``requests.get`` and ``requests.Session.get``
    (used by The Odds API provider) — and appends a structured record to the
    yielded list.

    Each record contains:
        provider   – human-readable name derived from the URL hostname
        url        – redacted URL (API keys hidden)
        status     – HTTP status code
        ok         – True if status < 400
        item_count – len of response if it's a list, else None
        preview    – truncated data (for display)
        raw        – full parsed JSON (or text on parse failure)
        error      – error string if the request itself raised
    """
    import requests as _requests  # local import to avoid circular deps

    captured: list[dict] = []
    _original_get = _requests.get
    _original_session_get = _requests.Session.get

    def _make_record(url: str) -> dict:
        return {
            "provider": provider_label_from_url(url),
            "url": redact_url(url),
            "status": None,
            "ok": False,
            "item_count": None,
            "preview": None,
            "raw": None,
            "error": None,
        }

    def _sensitive_values(url: str, kwargs: dict) -> list[str]:
        values = [
            value
            for name, entries in parse_qs(urlparse(url).query).items()
            if name.lower() in _SENSITIVE_PARAM_NAMES
            for value in entries
        ]
        params = kwargs.get("params")
        if isinstance(params, Mapping):
            values.extend(
                value
                for name, value in params.items()
                if isinstance(name, str)
                and name.lower() in _SENSITIVE_PARAM_NAMES
                and isinstance(value, str)
            )
        headers = kwargs.get("headers")
        if isinstance(headers, Mapping):
            values.extend(
                value
                for name, value in headers.items()
                if isinstance(name, str)
                and name.lower() in {"authorization", "x-api-key"}
                and isinstance(value, str)
            )
        return values

    def _fill_record(record: dict, response, sensitive_values: list[str]) -> None:
        record["status"] = response.status_code
        record["ok"] = response.ok
        try:
            data = response.json()
        except ValueError:
            record["raw"] = redact_sensitive_text(response.text[:3000], sensitive_values)
            record["error"] = "Response is not valid JSON"
            return
        data = _redact_data(data, sensitive_values)
        record["raw"] = data
        record["preview"] = _preview(data, max_list)
        if isinstance(data, list):
            record["item_count"] = len(data)

    def _redact_error(exc: Exception, url: str, kwargs: dict) -> str:
        return redact_sensitive_text(str(exc), _sensitive_values(url, kwargs))

    def _capturing_get(url, **kwargs):
        record = _make_record(url)
        try:
            response = _original_get(url, **kwargs)
            _fill_record(record, response, _sensitive_values(url, kwargs))
        except Exception as exc:
            record["error"] = _redact_error(exc, url, kwargs)
            captured.append(record)
            raise
        captured.append(record)
        return response

    def _capturing_session_get(self, url, **kwargs):
        record = _make_record(url)
        try:
            response = _original_session_get(self, url, **kwargs)
            _fill_record(record, response, _sensitive_values(url, kwargs))
        except Exception as exc:
            record["error"] = _redact_error(exc, url, kwargs)
            captured.append(record)
            raise
        captured.append(record)
        return response

    _requests.get = _capturing_get  # type: ignore[assignment]
    _requests.Session.get = _capturing_session_get  # type: ignore[assignment]
    try:
        yield captured
    finally:
        _requests.get = _original_get  # type: ignore[assignment]
        _requests.Session.get = _original_session_get  # type: ignore[assignment]
