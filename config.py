"""
Configuration and environment variable management.

All API keys and tunable runtime settings are loaded from environment
variables (or a .env file).

BALLDONTLIE is the only supported live sports-data provider. Demo and replay
modes use local records only and never make external provider requests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)


def _key(name: str) -> str | None:
    return os.environ.get(name) or None


# ---------------------------------------------------------------------------
# Provider credentials
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderCredentials:
    """The single credential permitted for live sports-data retrieval."""

    balldontlie_key: str | None = field(
        default_factory=lambda: _key("BALLDONTLIE_API_KEY")
    )

    @property
    def available_providers(self) -> list[str]:
        return ["balldontlie"] if self.balldontlie_key else []

    @property
    def missing_providers(self) -> list[str]:
        return [] if self.balldontlie_key else ["balldontlie"]


@dataclass(frozen=True)
class BallDontLieConfig:
    """Transport and cache policy for the single live provider."""

    base_url: str = field(default_factory=lambda: os.environ.get("BALLDONTLIE_BASE_URL", "https://api.balldontlie.io"))
    timeout: int = field(default_factory=lambda: int(os.environ.get("BALLDONTLIE_TIMEOUT", "20")))
    max_retries: int = field(default_factory=lambda: int(os.environ.get("BALLDONTLIE_MAX_RETRIES", "3")))
    request_budget_per_minute: int = field(default_factory=lambda: int(
        os.environ.get("BALLDONTLIE_REQUEST_BUDGET_PER_MINUTE", "60")
    ))
    odds_refresh_seconds: int = field(default_factory=lambda: int(os.environ.get("BALLDONTLIE_ODDS_REFRESH_SECONDS", "60")))
    stable_cache_seconds: int = field(default_factory=lambda: int(os.environ.get("BALLDONTLIE_STABLE_CACHE_SECONDS", "86400")))


# ---------------------------------------------------------------------------
# Application-level settings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AppSettings:
    # Whether to fall back gracefully when providers fail
    graceful_fallback: bool = True

    # HTTP retry config
    http_retries: int = 3
    http_backoff_factor: float = 0.5

    # Logging
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))
    log_to_file: bool = field(default_factory=lambda: os.environ.get("LOG_TO_FILE", "false").lower() == "true")
    log_file_path: str = field(default_factory=lambda: os.environ.get("LOG_FILE", "nba_prop_ai.log"))

    # CSV import
    csv_players_path: str = field(default_factory=lambda: os.environ.get("CSV_PLAYERS_PATH", "data/import/players.csv"))
    csv_odds_path: str = field(default_factory=lambda: os.environ.get("CSV_ODDS_PATH", "data/import/odds.csv"))
    csv_defense_path: str = field(default_factory=lambda: os.environ.get("CSV_DEFENSE_PATH", "data/import/defense.csv"))

    # In-memory cache TTL in seconds (0 = no cache)
    cache_ttl_seconds: int = field(default_factory=lambda: int(os.environ.get("CACHE_TTL", "300")))

    # Disk cache enabled (disable in testing if needed)
    disk_cache_enabled: bool = field(default_factory=lambda: os.environ.get("DISK_CACHE_ENABLED", "true").lower() == "true")
    # "demo" uses clearly labelled static data. "live" never substitutes sample
    # opportunities when a configured provider is unavailable.
    app_mode: str = field(default_factory=lambda: os.environ.get("APP_MODE", "demo").strip().lower())
    evaluation_db_path: str = field(default_factory=lambda: os.environ.get(
        "EVALUATION_DB_PATH", "data/evaluation/forecast_ledger.sqlite3"
    ))
    max_quote_age_seconds: int = field(default_factory=lambda: int(os.environ.get("MAX_QUOTE_AGE_SECONDS", "120")))


# ---------------------------------------------------------------------------
# Singleton accessors
# ---------------------------------------------------------------------------

_credentials: ProviderCredentials | None = None
_app_settings: AppSettings | None = None
_bdl_config: BallDontLieConfig | None = None


def get_credentials() -> ProviderCredentials:
    global _credentials
    if _credentials is None:
        _credentials = ProviderCredentials()
    return _credentials


def get_settings() -> AppSettings:
    global _app_settings
    if _app_settings is None:
        _app_settings = AppSettings()
    return _app_settings


def get_bdl_config() -> BallDontLieConfig:
    global _bdl_config
    if _bdl_config is None:
        _bdl_config = BallDontLieConfig()
    return _bdl_config


def reload_config() -> None:
    """Force re-read of env vars (useful in tests)."""
    global _credentials, _app_settings, _bdl_config
    load_dotenv(dotenv_path=_ENV_PATH, override=False)
    _credentials = None
    _app_settings = None
    _bdl_config = None
