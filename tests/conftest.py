"""Shared test setup: keep the suite offline and deterministic."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits real APIs for today's slate; skip with -m 'not live'")


@pytest.fixture(autouse=True)
def _no_live_llm_news(monkeypatch):
    """Tests never call OpenAI for injury-news features unless they inject a fake client."""
    monkeypatch.setenv("AI_NEWS_FEATURES", "0")
