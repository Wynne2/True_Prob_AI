"""Shared deterministic runtime configuration for offline test tiers."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def deterministic_runtime(monkeypatch):
    monkeypatch.setenv("APP_MODE", "demo")
    monkeypatch.setenv("DISK_CACHE_ENABLED", "false")
    # Keep an explicit empty value so python-dotenv cannot repopulate a real
    # developer credential from the ignored local .env during reload_config().
    monkeypatch.setenv("BALLDONTLIE_API_KEY", "")
    from config import reload_config
    from data.loaders import reset_registry

    reload_config()
    reset_registry()
