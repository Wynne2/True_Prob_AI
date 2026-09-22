"""Shared deterministic runtime configuration for offline test tiers."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def deterministic_runtime(monkeypatch):
    monkeypatch.setenv("APP_MODE", "demo")
    monkeypatch.setenv("DISK_CACHE_ENABLED", "false")
    monkeypatch.delenv("BALLDONTLIE_API_KEY", raising=False)
    from config import reload_config

    reload_config()
