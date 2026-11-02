"""
Configuration and environment variable management.

All API keys and tunable runtime settings are loaded from environment
variables (or a .env file).

Active provider credentials (as of re-architecture):
  - BALLDONTLIE_API_KEY or BDL_API_KEY → player stats, tracking, pace, splits
  - THE_ODDS_API_KEY      →  sportsbook odds, prop lines, implied probability

Keyless public sources (enabled by default):
  - Kalshi        →  NBA player props + game lines (public market data API)
  - Polymarket    →  NBA player props + game lines (public Gamma API)
  Disable with KALSHI_ENABLED=false / POLYMARKET_ENABLED=false.
NBA.com / nba_api is no longer the stats source.

Retired provider credentials (no longer used in the engine):
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


def _flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


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
    balldontlie_key: str | None = field(
        default_factory=lambda: _key("BALLDONTLIE_API_KEY") or _key("BDL_API_KEY")
    )

    # ODDS / SPORTSBOOK PRICING
    odds_api_key: str | None = field(
        default_factory=lambda: _key("THE_ODDS_API_KEY") or _key("ODDS_API_KEY")
    )

    @property
    def available_providers(self) -> list[str]:
        providers = []
        if self.balldontlie_key:
            providers.append("balldontlie")
        if self.odds_api_key:
            providers.append("odds_api")
        pm = get_prediction_market_config()
        if pm.kalshi_enabled:
            providers.append("kalshi")
        if pm.polymarket_enabled:
            providers.append("polymarket")
        return providers

    @property
    def missing_providers(self) -> list[str]:
        missing = []
        if not self.balldontlie_key:
            missing.append("balldontlie")
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
# The Odds API config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OddsAPIConfig:
    base_url: str = "https://api.the-odds-api.com/v4"
    sport_key: str = "basketball_nba"
    # The Odds API uses comma-separated region codes, e.g. us,us2 (second US book group).
    # Override with env ODDS_API_REGIONS — do not use "us_2"; the API expects "us2".
    regions: str = field(
        default_factory=lambda: os.environ.get("ODDS_API_REGIONS", "us,us2")
    )
    markets: str = (
        "player_points,player_rebounds,player_assists,"
        "player_threes,player_blocks,player_steals,player_turnovers"
    )
    odds_format: str = "american"
    timeout: int = 20


# ---------------------------------------------------------------------------
# Prediction markets (Kalshi / Polymarket) — public, no API key
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PredictionMarketConfig:
    kalshi_enabled: bool = field(default_factory=lambda: _flag("KALSHI_ENABLED"))
    polymarket_enabled: bool = field(default_factory=lambda: _flag("POLYMARKET_ENABLED"))
    kalshi_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2"
        )
    )
    polymarket_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "POLYMARKET_BASE_URL", "https://gamma-api.polymarket.com"
        )
    )
    # Polymarket Gamma series id for NBA (from GET /sports).
    polymarket_nba_series_id: str = field(
        default_factory=lambda: os.environ.get("POLYMARKET_NBA_SERIES_ID", "10345")
    )
    timeout: int = field(default_factory=lambda: int(os.environ.get("PREDICTION_MARKET_TIMEOUT", "20")))
    # Skip books whose Yes ask + No ask exceeds $1 by more than this (stale/empty books).
    max_spread: float = field(
        default_factory=lambda: float(os.environ.get("PREDICTION_MARKET_MAX_SPREAD", "0.15"))
    )
    # Ignore asks outside this band (near-certain / lottery-ticket quotes).
    min_price: float = field(
        default_factory=lambda: float(os.environ.get("PREDICTION_MARKET_MIN_PRICE", "0.02"))
    )
    max_price: float = field(
        default_factory=lambda: float(os.environ.get("PREDICTION_MARKET_MAX_PRICE", "0.98"))
    )
    kalshi_fee_rate: float = field(
        default_factory=lambda: float(os.environ.get("KALSHI_FEE_RATE", "0.07"))
    )


# ---------------------------------------------------------------------------
# OpenAI (AI Advisor reasoning layer — optional; only `main.py advisor` needs it)
# ---------------------------------------------------------------------------

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class OpenAIConfig:
    """Settings for the AI Advisor. The key is only required when the LLM layer runs."""
    api_key: str | None = field(default_factory=lambda: _key("OPENAI_API_KEY"))
    model: str = field(default_factory=lambda: (os.environ.get("OPENAI_MODEL") or "gpt-6.1-sol").strip())
    reasoning_effort: str = field(
        default_factory=lambda: (os.environ.get("OPENAI_REASONING_EFFORT") or "medium").strip().lower()
    )
    timeout: int = field(default_factory=lambda: _int_env("OPENAI_TIMEOUT", 120))
    max_output_tokens: int = field(default_factory=lambda: _int_env("OPENAI_MAX_OUTPUT_TOKENS", 8000))
    base_url: str = field(
        default_factory=lambda: (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


# ---------------------------------------------------------------------------
# BALLDONTLIE GOAT (replaces nba_api / NBA.com)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BallDontLieConfig:
    """BALLDONTLIE NBA GOAT client settings. Trial tier is 5 req/min; paid GOAT is 600."""
    base_url: str = "https://api.balldontlie.io"
    timeout: int = int(os.environ.get("BDL_TIMEOUT", "30"))
    requests_per_minute: int = int(os.environ.get("BDL_REQUESTS_PER_MINUTE", "5"))
    max_retries: int = int(os.environ.get("BDL_MAX_RETRIES", "4"))
    per_page: int = 100


@dataclass(frozen=True)
class NBAApiConfig:
    """Legacy name kept so older imports still resolve. Stats now come from BALLDONTLIE."""
    season: str = os.environ.get("NBA_SEASON", "2025-26")
    rate_limit_sleep: float = float(os.environ.get("BDL_MIN_INTERVAL", "0"))
    ttl_advanced: int = int(os.environ.get("BDL_TTL_ADVANCED", "86400"))
    ttl_tracking: int = int(os.environ.get("BDL_TTL_TRACKING", "86400"))
    ttl_gamelogs: int = int(os.environ.get("BDL_TTL_GAMELOGS", "43200"))
    ttl_splits: int = int(os.environ.get("BDL_TTL_SPLITS", "86400"))


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
_odds_config: OddsAPIConfig | None = None
_nba_config: NBAApiConfig | None = None
_bdl_config: BallDontLieConfig | None = None
_pm_config: PredictionMarketConfig | None = None
_openai_config: OpenAIConfig | None = None


def get_openai_config() -> OpenAIConfig:
    global _openai_config
    if _openai_config is None:
        _openai_config = OpenAIConfig()
    return _openai_config


def get_prediction_market_config() -> PredictionMarketConfig:
    global _pm_config
    if _pm_config is None:
        _pm_config = PredictionMarketConfig()
    return _pm_config


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


def get_bdl_config() -> BallDontLieConfig:
    global _bdl_config
    if _bdl_config is None:
        _bdl_config = BallDontLieConfig()
    return _bdl_config


def reload_config() -> None:
    """Force re-read of env vars (useful in tests)."""
    global _credentials, _app_settings, _sdio_config, _odds_config, _nba_config, _bdl_config, _pm_config, _openai_config
    load_dotenv(dotenv_path=_ENV_PATH, override=True)
    _credentials = None
    _pm_config = None
    _openai_config = None
    _app_settings = None
    _sdio_config = None
    _odds_config = None
    _nba_config = None
    _bdl_config = None
