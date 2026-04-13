"""
Provider Registry.

The registry maintains the ordered list of active providers and routes
each data request to the first provider that can fulfil it.  If all live
providers fail or are unavailable, the registry falls back to the sample
data provider automatically.

Usage:
    registry = ProviderRegistry.build()
    games = registry.get_games_for_date(date.today())
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from config import get_credentials, get_settings
from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import DataSource, PropType
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


def _build_providers() -> list[BaseProvider]:
    """
    Instantiate all providers whose credentials are present, plus the
    always-available fallbacks (CSV import and sample data).
    """
    from providers.sample_provider import SampleProvider
    from providers.csv_import_provider import CSVImportProvider

    creds = get_credentials()
    providers: list[BaseProvider] = []

    if creds.sportsdataio_key:
        try:
            from providers.sportsdataio_provider import SportsDataIOProvider
            providers.append(SportsDataIOProvider(creds.sportsdataio_key))
            logger.info("SportsDataIO provider registered")
        except Exception as exc:
            logger.warning("Failed to load SportsDataIO provider: %s", exc)
    else:
        logger.info("SportsDataIO skipped – no SPORTSDATAIO_API_KEY")

    if creds.sportradar_key:
        try:
            from providers.sportradar_provider import SportradarProvider
            providers.append(SportradarProvider(creds.sportradar_key))
            logger.info("Sportradar provider registered")
        except Exception as exc:
            logger.warning("Failed to load Sportradar provider: %s", exc)
    else:
        logger.info("Sportradar skipped – no SPORTRADAR_API_KEY")

    if creds.odds_api_key:
        try:
            from providers.odds_api_provider import OddsAPIProvider
            providers.append(OddsAPIProvider(creds.odds_api_key))
            logger.info("The Odds API provider registered")
        except Exception as exc:
            logger.warning("Failed to load Odds API provider: %s", exc)
    else:
        logger.info("The Odds API skipped – no THE_ODDS_API_KEY")

    if creds.fantasypros_key:
        try:
            from providers.fantasypros_provider import FantasyProsProvider
            providers.append(FantasyProsProvider(creds.fantasypros_key))
            logger.info("FantasyPros provider registered")
        except Exception as exc:
            logger.warning("Failed to load FantasyPros provider: %s", exc)

    if creds.nba_official_key:
        try:
            from providers.nba_official_provider import NBAOfficialProvider
            providers.append(NBAOfficialProvider(creds.nba_official_key))
            logger.info("NBA Official provider registered")
        except Exception as exc:
            logger.warning("Failed to load NBA Official provider: %s", exc)

    if creds.statmuse_key:
        try:
            from providers.statmuse_provider import StatMuseProvider
            providers.append(StatMuseProvider(creds.statmuse_key))
            logger.info("StatMuse provider registered")
        except Exception as exc:
            logger.warning("Failed to load StatMuse provider: %s", exc)

    # CSV import is always available (no key required); it's a no-op if
    # the CSV files don't exist.
    providers.append(CSVImportProvider())

    # Sample data is always last — guaranteed fallback
    providers.append(SampleProvider())
    logger.info("Sample data fallback registered (always active)")

    return providers


class ProviderRegistry:
    """
    Central routing layer for all data requests.

    Providers are tried in priority order.  The first one that returns a
    non-empty result wins.  If every provider fails, an empty list / None
    is returned and a warning is logged.
    """

    def __init__(self, providers: list[BaseProvider]) -> None:
        self._providers = providers

    @classmethod
    def build(cls) -> "ProviderRegistry":
        """Factory: instantiate all configured providers and return a registry."""
        return cls(_build_providers())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_list(self, method_name: str, *args, **kwargs) -> list:
        """Call *method_name* on each provider, return first non-empty list."""
        for provider in self._providers:
            try:
                result = getattr(provider, method_name)(*args, **kwargs)
                if result:
                    logger.debug(
                        "%s returned %d records from %s",
                        method_name,
                        len(result),
                        provider.source_name,
                    )
                    return result
            except NotImplementedError:
                continue
            except Exception as exc:
                logger.warning(
                    "%s failed on %s: %s", method_name, provider.source_name, exc
                )
        logger.warning("%s: all providers returned empty or failed", method_name)
        return []

    def _try_optional(self, method_name: str, *args, **kwargs):
        """Call *method_name* on each provider, return first non-None result."""
        for provider in self._providers:
            try:
                result = getattr(provider, method_name)(*args, **kwargs)
                if result is not None:
                    return result
            except NotImplementedError:
                continue
            except Exception as exc:
                logger.warning(
                    "%s failed on %s: %s", method_name, provider.source_name, exc
                )
        return None

    # ------------------------------------------------------------------
    # Public API (mirrors BaseProvider interface)
    # ------------------------------------------------------------------

    def get_games_for_date(self, game_date: date) -> list[Game]:
        return self._try_list("get_games_for_date", game_date)

    def get_players_for_game(self, game_id: str) -> list[Player]:
        return self._try_list("get_players_for_game", game_id)

    def get_player_context(self, player_id: str) -> Optional[Player]:
        return self._try_optional("get_player_context", player_id)

    def get_player_recent_form(self, player_id: str, n: int = 10) -> list[dict]:
        return self._try_list("get_player_recent_form", player_id, n)

    def get_team_context(self, team_id: str) -> Optional[dict]:
        return self._try_optional("get_team_context", team_id)

    def get_team_defense(self, team_id: str) -> Optional[TeamDefense]:
        return self._try_optional("get_team_defense", team_id)

    def get_defense_vs_position(
        self, team_id: str, position: str, prop_type: PropType
    ) -> float:
        for provider in self._providers:
            try:
                result = provider.get_defense_vs_position(team_id, position, prop_type)
                if result and result > 0:
                    return result
            except NotImplementedError:
                continue
            except Exception as exc:
                logger.warning("get_defense_vs_position failed: %s", exc)
        return 0.0

    def get_fantasy_points_allowed(self, team_id: str, position: str) -> float:
        for provider in self._providers:
            try:
                result = provider.get_fantasy_points_allowed(team_id, position)
                if result and result > 0:
                    return result
            except NotImplementedError:
                continue
            except Exception as exc:
                logger.warning("get_fantasy_points_allowed failed: %s", exc)
        return 0.0

    def get_matchup_history(
        self, player_id: str, opponent_team_id: str
    ) -> list[dict]:
        return self._try_list("get_matchup_history", player_id, opponent_team_id)

    def get_injuries(self, game_date: Optional[date] = None) -> list[dict]:
        return self._try_list("get_injuries", game_date)

    def get_lineups(self, game_date: Optional[date] = None) -> list[dict]:
        return self._try_list("get_lineups", game_date)

    def get_depth_charts(self, team_id: Optional[str] = None) -> list[dict]:
        return self._try_list("get_depth_charts", team_id)

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        return self._try_list("get_player_props", game_date)

    def get_live_odds(self, game_date: date) -> list[OddsLine]:
        return self._try_list("get_live_odds", game_date)

    def get_historical_odds(
        self, prop_type: PropType, days_back: int = 7
    ) -> list[OddsLine]:
        return self._try_list("get_historical_odds", prop_type, days_back)

    def get_tracking_metrics(self, player_id: str) -> Optional[dict]:
        return self._try_optional("get_tracking_metrics", player_id)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @property
    def active_providers(self) -> list[str]:
        return [p.source_name.value for p in self._providers]

    def summary(self) -> str:
        lines = ["Provider Registry:"]
        for i, p in enumerate(self._providers, 1):
            status = "available" if p.is_available() else "fallback/stub"
            lines.append(f"  {i}. {p.__class__.__name__} ({status})")
        return "\n".join(lines)
