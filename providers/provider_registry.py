"""
Provider Registry.

The registry maintains the ordered list of active providers and routes
each data request to the first provider that can fulfil it.

Provider chain:
    BallDontLie  ->  CSV import (manual override, no key required)

No sample-data fallback.  If BallDontLie returns no data, the engine
receives an empty result — never fake data.

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
    Instantiate the BallDontLie provider (primary) and the CSV import
    provider (manual override).  No sample-data fallback is added.
    """
    from providers.csv_import_provider import CSVImportProvider

    creds = get_credentials()
    providers: list[BaseProvider] = []

    if creds.balldontlie_key:
        try:
            from providers.balldontlie_provider import BallDontLieProvider
            providers.append(BallDontLieProvider(creds.balldontlie_key))
            logger.info("BallDontLie provider registered")
        except Exception as exc:
            logger.warning("Failed to load BallDontLie provider: %s", exc)
    else:
        logger.warning(
            "No BALLDONTLIE_API_KEY found — no live data will be available. "
            "Add your key to .env to enable data fetching."
        )

    # CSV import is always available (no key required); it's a no-op if
    # the import files don't exist.
    providers.append(CSVImportProvider())
    logger.debug("CSV import provider registered (manual override)")

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
        lines = ["Provider Registry (BallDontLie -> CSV):"]
        for i, p in enumerate(self._providers, 1):
            status = "live" if p.is_available() else "no key / stub"
            lines.append(f"  {i}. {p.__class__.__name__} ({status})")
        return "\n".join(lines)
