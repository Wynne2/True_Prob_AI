"""Shared test setup: keep the suite offline and deterministic."""

import pytest


@pytest.fixture(autouse=True)
def _no_live_llm_news(monkeypatch):
    """Tests never call OpenAI for injury-news features unless they inject a fake client."""
    monkeypatch.setenv("AI_NEWS_FEATURES", "0")
