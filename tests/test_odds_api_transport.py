import json
import logging
from urllib.parse import quote_plus

import pytest
import requests
from requests.adapters import HTTPAdapter

from providers import odds_api_provider
from providers.odds_api_provider import OddsAPIProvider
from utils.api_debug import capture_api_responses


def test_odds_requests_verify_tls_certificates(monkeypatch):
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    captured = {}

    def fake_send(self, request, **kwargs):
        captured["verify"] = kwargs["verify"]
        response = requests.Response()
        response.status_code = 200
        response._content = b"[]"
        response.request = request
        response.url = request.url
        return response

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)

    assert OddsAPIProvider("test-key")._get("sports") == []
    assert captured["verify"] is True


def test_odds_requests_honor_custom_trusted_ca_bundle(monkeypatch, tmp_path):
    ca_bundle = str(tmp_path / "trusted-ca.pem")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", ca_bundle)
    captured = {}

    def fake_send(self, request, **kwargs):
        captured["verify"] = kwargs["verify"]
        response = requests.Response()
        response.status_code = 200
        response._content = b"[]"
        response.request = request
        response.url = request.url
        return response

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)

    assert OddsAPIProvider("test-key")._get("sports") == []
    assert captured["verify"] == ca_bundle


def test_tls_errors_do_not_expose_api_key_in_logs_or_debug(monkeypatch, caplog):
    key = "test-key with+special/chars"
    encoded_key = quote_plus(key)
    provider = OddsAPIProvider(key)

    def fail_request(self, url, **kwargs):
        raise requests.exceptions.SSLError(
            f"TLS failed for {url}?apiKey={encoded_key}; raw key: {key}"
        )

    monkeypatch.setattr(requests.Session, "get", fail_request)
    monkeypatch.setattr(odds_api_provider.time, "sleep", lambda _: None)

    with caplog.at_level(logging.WARNING), capture_api_responses() as records:
        assert provider._get("sports") is None

    assert len(records) == 4
    for text in [caplog.text, *(record["error"] for record in records)]:
        assert key not in text
        assert encoded_key not in text
        assert "***" in text


def test_unauthorized_response_does_not_log_api_key(monkeypatch, caplog):
    key = "test-odds-key"
    provider = OddsAPIProvider(key)
    response = requests.Response()
    response.status_code = 401
    response._content = ('{"message": "Invalid key: ' + key + '"}').encode()
    monkeypatch.setattr(provider._session, "get", lambda *args, **kwargs: response)

    with caplog.at_level(logging.ERROR):
        assert provider._get("sports") is None

    assert key not in caplog.text
    assert "***" in caplog.text


def test_debug_response_redacts_key_but_keeps_bookmaker_key(monkeypatch):
    key = "test-odds-key"

    def fake_get(self, url, **kwargs):
        response = requests.Response()
        response.status_code = 401
        response._content = json.dumps({
            "message": f"Invalid key: {key}",
            "bookmaker": {"key": "fanduel"},
        }).encode()
        return response

    monkeypatch.setattr(requests.Session, "get", fake_get)

    with capture_api_responses() as records:
        requests.Session().get(
            "https://api.the-odds-api.com/v4/sports",
            params={"apiKey": key},
        )

    assert records[0]["raw"]["message"] == "Invalid key: ***"
    assert records[0]["raw"]["bookmaker"]["key"] == "fanduel"
    assert key not in str(records[0]["preview"])


def test_debug_non_json_response_redacts_authorization_header(monkeypatch):
    key = "test-provider-key"

    def fake_get(self, url, **kwargs):
        response = requests.Response()
        response.status_code = 401
        response._content = f"Invalid credential: {key}".encode()
        return response

    monkeypatch.setattr(requests.Session, "get", fake_get)

    with capture_api_responses() as records:
        requests.Session().get(
            "https://api.balldontlie.io/v1/games",
            headers={"Authorization": key},
        )

    assert records[0]["raw"] == "Invalid credential: ***"
    assert records[0]["error"] == "Response is not valid JSON"


def test_bare_debug_request_redacts_url_and_exception(monkeypatch):
    key = "test-key with+special/chars"
    encoded_key = quote_plus(key)
    url = f"https://api.the-odds-api.com/v4/sports?ApiKey={encoded_key}"

    def fail_request(url, **kwargs):
        raise requests.exceptions.SSLError(f"TLS failed for {url}; raw key: {key}")

    monkeypatch.setattr(requests, "get", fail_request)

    with capture_api_responses() as records:
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(url, params={"apiKey": key})

    for text in (records[0]["url"], records[0]["error"]):
        assert key not in text
        assert encoded_key not in text


@pytest.mark.parametrize("logger_name", [
    "urllib3.connectionpool",
    "urllib3.connection",
    "urllib3.util.retry",
])
def test_urllib3_debug_logging_redacts_odds_query_key(caplog, logger_name):
    key = "test-odds-key with+special/chars"
    encoded_key = quote_plus(key)
    OddsAPIProvider(key)
    logger = logging.getLogger(logger_name)

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        logger.debug("GET %s", f"/v4/sports?apiKey={encoded_key}&regions=us")

    assert key not in caplog.text
    assert encoded_key not in caplog.text
    assert "***" in caplog.text


def test_urllib3_exception_trace_redacts_odds_query_key(caplog):
    key = "test-odds-key"
    OddsAPIProvider(key)
    logger = logging.getLogger("urllib3.connection")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        try:
            raise ValueError(f"Bad response for /v4/sports?apiKey={key}")
        except ValueError:
            logger.warning("Request failed", exc_info=True)

    assert key not in caplog.text
    assert "***" in caplog.text
