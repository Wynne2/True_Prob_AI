"""
Configuration and environment variable management.

All API keys and tunable runtime settings are loaded from environment
variables (or a .env file).  If a provider key is absent the platform
degrades gracefully: the provider is skipped and a warning is logged, but
all other providers and the sample-data fallback continue to work normally.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root if present (safe no-op if absent)
_ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)


# ---------------------------------------------------------------------------
# Provider credential availability flags
# ---------------------------------------------------------------------------

def _key(name: str) -> str | None:
    """Return the env-var value or None."""
    return os.environ.get(name) or None


@dataclass(frozen=True)
class ProviderCredentials:
    """Snapshot of all provider API keys at startup."""
    sportsdataio_key: str | None = field(default_factory=lambda: _key("SPORTSDATAIO_API_KEY"))
    sportradar_key: str | None = field(default_factory=lambda: _key("SPORTRADAR_API_KEY"))
    odds_api_key: str | None = field(default_factory=lambda: _key("THE_ODDS_API_KEY"))
    fantasypros_key: str | None = field(default_factory=lambda: _key("FANTASYPROS_API_KEY"))
    nba_official_key: str | None = field(default_factory=lambda: _key("NBA_OFFICIAL_API_KEY"))
    statmuse_key: str | None = field(default_factory=lambda: _key("STATMUSE_API_KEY"))
    rotogrinders_key: str | None = field(default_factory=lambda: _key("ROTOGRINDERS_API_KEY"))
    rotowire_key: str | None = field(default_factory=lambda: _key("ROTOWIRE_API_KEY"))

    @property
    def available_providers(self) -> list[str]:
        mapping = {
            "sportsdataio": self.sportsdataio_key,
            "sportradar": self.sportradar_key,
            "odds_api": self.odds_api_key,
            "fantasypros": self.fantasypros_key,
            "nba_official": self.nba_official_key,
            "statmuse": self.statmuse_key,
            "rotogrinders": self.rotogrinders_key,
            "rotowire": self.rotowire_key,
        }
        return [name for name, key in mapping.items() if key]

    @property
    def missing_providers(self) -> list[str]:
        mapping = {
            "sportsdataio": self.sportsdataio_key,
            "sportradar": self.sportradar_key,
            "odds_api": self.odds_api_key,
            "fantasypros": self.fantasypros_key,
            "nba_official": self.nba_official_key,
            "statmuse": self.statmuse_key,
            "rotogrinders": self.rotogrinders_key,
            "rotowire": self.rotowire_key,
        }
        return [name for name, key in mapping.items() if not key]


# ---------------------------------------------------------------------------
# SportsDataIO endpoints
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SportsDataIOConfig:
    base_url: str = "https://api.sportsdata.io/v3/nba"
    odds_base_url: str = "https://api.sportsdata.io/v3/nba"
    timeout: int = 15


# ---------------------------------------------------------------------------
# Sportradar endpoints
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SportradarConfig:
    base_url: str = "https://api.sportradar.us/nba/trial/v8/en"
    timeout: int = 15


# ---------------------------------------------------------------------------
# The Odds API endpoints
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OddsAPIConfig:
    base_url: str = "https://api.the-odds-api.com/v4"
    sport_key: str = "basketball_nba"
    regions: str = "us"                        # comma-separated: us, uk, eu, au
    markets: str = "player_points,player_rebounds,player_assists,player_threes,player_blocks,player_steals,player_turnovers"
    odds_format: str = "american"
    timeout: int = 20


# ---------------------------------------------------------------------------
# Application-level settings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AppSettings:
    # Provider priority order (first provider with data wins for each call)
    provider_priority: list[str] = field(default_factory=lambda: [
        "sportsdataio",
        "sportradar",
        "odds_api",
        "fantasypros",
        "csv_import",
        "sample",   # always last
    ])

    # Fallback to sample data when all live providers fail
    use_sample_fallback: bool = True

    # HTTP request retry config
    http_retries: int = 3
    http_backoff_factor: float = 0.5

    # Logging
    log_level: str = os.environ.get("LOG_LEVEL", "INFO")
    log_to_file: bool = os.environ.get("LOG_TO_FILE", "false").lower() == "true"
    log_file_path: str = os.environ.get("LOG_FILE", "nba_prop_ai.log")

    # CSV import
    csv_players_path: str = os.environ.get("CSV_PLAYERS_PATH", "data/import/players.csv")
    csv_odds_path: str = os.environ.get("CSV_ODDS_PATH", "data/import/odds.csv")
    csv_defense_path: str = os.environ.get("CSV_DEFENSE_PATH", "data/import/defense.csv")

    # Caching (in-memory TTL in seconds; 0 = no cache)
    cache_ttl_seconds: int = int(os.environ.get("CACHE_TTL", "300"))


# ---------------------------------------------------------------------------
# Singleton accessors
# ---------------------------------------------------------------------------

_credentials: ProviderCredentials | None = None
_app_settings: AppSettings | None = None
_sdio_config: SportsDataIOConfig | None = None
_sr_config: SportradarConfig | None = None
_odds_config: OddsAPIConfig | None = None


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


def get_sdio_config() -> SportsDataIOConfig:
    global _sdio_config
    if _sdio_config is None:
        _sdio_config = SportsDataIOConfig()
    return _sdio_config


def get_sportradar_config() -> SportradarConfig:
    global _sr_config
    if _sr_config is None:
        _sr_config = SportradarConfig()
    return _sr_config


def get_odds_api_config() -> OddsAPIConfig:
    global _odds_config
    if _odds_config is None:
        _odds_config = OddsAPIConfig()
    return _odds_config


def reload_config() -> None:
    """Force re-read of env vars (useful in tests)."""
    global _credentials, _app_settings, _sdio_config, _sr_config, _odds_config
    load_dotenv(dotenv_path=_ENV_PATH, override=True)
    _credentials = None
    _app_settings = None
    _sdio_config = None
    _sr_config = None
    _odds_config = None
