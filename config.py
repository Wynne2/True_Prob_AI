"""
Configuration and environment variable management.

All API keys and tunable runtime settings are loaded from environment
variables (or a .env file).

Active provider credentials (as of re-architecture):
  - SPORTSDATAIO_API_KEY  →  injuries, rosters, season stats, depth charts
  - SPORTRADAR_API_KEY    →  game slate (primary)
  - THE_ODDS_API_KEY      →  sportsbook odds, prop lines, implied probability

nba_api does NOT require an API key.  It uses public NBA.com endpoints.

Retired provider credentials (no longer used in the engine):
  - BALLDONTLIE_API_KEY
  - FANTASYPROS_API_KEY
  - NBA_OFFICIAL_API_KEY
  - STATMUSE_API_KEY
  - ROTOGRINDERS_API_KEY
  - ROTOWIRE_API_KEY
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
    """Active provider API keys."""

    # PRIMARY MODELING SOURCES
    sportsdataio_key: str | None = field(
        default_factory=lambda: _key("SPORTSDATAIO_API_KEY")
    )
    # nba_api: no key required — uses public NBA.com endpoints

    # SLATE SUPPORT
    sportradar_key: str | None = field(
        default_factory=lambda: _key("SPORTRADAR_API_KEY")
    )

    # ODDS / SPORTSBOOK PRICING
    odds_api_key: str | None = field(
        default_factory=lambda: _key("THE_ODDS_API_KEY")
    )

    @property
    def available_providers(self) -> list[str]:
        providers = ["nba_api"]   # always available (no key)
        if self.sportsdataio_key:
            providers.append("sportsdataio")
        if self.sportradar_key:
            providers.append("sportradar")
        if self.odds_api_key:
            providers.append("odds_api")
        return providers

    @property
    def missing_providers(self) -> list[str]:
        missing = []
        if not self.sportsdataio_key:
            missing.append("sportsdataio")
        if not self.sportradar_key:
            missing.append("sportradar")
        if not self.odds_api_key:
            missing.append("odds_api")
        return missing


# ---------------------------------------------------------------------------
# SportsDataIO config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SportsDataIOConfig:
    base_url: str = "https://api.sportsdata.io/v3/nba"
    odds_base_url: str = "https://api.sportsdata.io/v3/nba"
    timeout: int = 15


# ---------------------------------------------------------------------------
# Sportradar Synergy Basketball config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SportradarConfig:
    """Configuration for the Sportradar Synergy Basketball API.

    Base URL:  https://api.sportradar.com/synergy/basketball/{league}/
    Auth:      x-api-key header  (NOT a query parameter)
    League:    nba

    API reference: https://api.sportradar.com/synergy/basketball/nba/...
    """
    base_url: str = "https://api.sportradar.com/synergy/basketball/nba"
    timeout: int = 15


# ---------------------------------------------------------------------------
# The Odds API config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OddsAPIConfig:
    base_url: str = "https://api.the-odds-api.com/v4"
    sport_key: str = "basketball_nba"
    regions: str = "us"
    markets: str = (
        "player_points,player_rebounds,player_assists,"
        "player_threes,player_blocks,player_steals,player_turnovers"
    )
    odds_format: str = "american"
    timeout: int = 20


# ---------------------------------------------------------------------------
# nba_api config (no key required)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NBAApiConfig:
    """Configuration for nba_api request behaviour."""
    season: str = os.environ.get("NBA_SEASON", "2025-26")
    rate_limit_sleep: float = float(os.environ.get("NBA_API_SLEEP", "0.6"))
    # Disk cache directories (relative to project root)
    cache_dir: str = "data/cache/nba_api"
    derived_cache_dir: str = "data/cache/derived"
    # TTL in seconds for each data type
    ttl_advanced: int = int(os.environ.get("NBA_API_TTL_ADVANCED", "86400"))   # 24h
    ttl_tracking: int = int(os.environ.get("NBA_API_TTL_TRACKING", "86400"))   # 24h
    ttl_gamelogs: int = int(os.environ.get("NBA_API_TTL_GAMELOGS", "43200"))   # 12h
    ttl_splits: int = int(os.environ.get("NBA_API_TTL_SPLITS", "86400"))       # 24h


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
    log_level: str = os.environ.get("LOG_LEVEL", "INFO")
    log_to_file: bool = os.environ.get("LOG_TO_FILE", "false").lower() == "true"
    log_file_path: str = os.environ.get("LOG_FILE", "nba_prop_ai.log")

    # CSV import
    csv_players_path: str = os.environ.get("CSV_PLAYERS_PATH", "data/import/players.csv")
    csv_odds_path: str = os.environ.get("CSV_ODDS_PATH", "data/import/odds.csv")
    csv_defense_path: str = os.environ.get("CSV_DEFENSE_PATH", "data/import/defense.csv")

    # In-memory cache TTL in seconds (0 = no cache)
    cache_ttl_seconds: int = int(os.environ.get("CACHE_TTL", "300"))

    # Disk cache enabled (disable in testing if needed)
    disk_cache_enabled: bool = os.environ.get("DISK_CACHE_ENABLED", "true").lower() == "true"


# ---------------------------------------------------------------------------
# Singleton accessors
# ---------------------------------------------------------------------------

_credentials: ProviderCredentials | None = None
_app_settings: AppSettings | None = None
_sdio_config: SportsDataIOConfig | None = None
_sr_config: SportradarConfig | None = None
_odds_config: OddsAPIConfig | None = None
_nba_config: NBAApiConfig | None = None


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


def get_nba_api_config() -> NBAApiConfig:
    global _nba_config
    if _nba_config is None:
        _nba_config = NBAApiConfig()
    return _nba_config


def reload_config() -> None:
    """Force re-read of env vars (useful in tests)."""
    global _credentials, _app_settings, _sdio_config, _sr_config, _odds_config, _nba_config
    load_dotenv(dotenv_path=_ENV_PATH, override=True)
    _credentials = None
    _app_settings = None
    _sdio_config = None
    _sr_config = None
    _odds_config = None
    _nba_config = None
