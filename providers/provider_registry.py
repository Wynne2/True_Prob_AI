"""
Provider Registry.

Routes each data request to the appropriate provider according to the
source-of-truth assignments from the architecture spec:

  Slate (game schedule):
      Sportradar  ->  SportsDataIO (fallback)

  Player-prop modeling data:
      nba_api     (PRIMARY: usage, touches, possessions, pace, splits)
      SportsDataIO (PRIMARY: injuries, rosters, season stats, depth charts)

  Odds / implied probability:
      The Odds API (ONLY source for sportsbook pricing)

  Cache fallback:
      Derived cache -> graceful empty if all live sources fail

Retired from engine flow (stubs remain but not wired):
  - BallDontLie
  - FantasyPros
  - StatMuse
  - RotoGrinders
  - Rotowire
  - NBA Official (API-key based; nba_api Python package is used instead)

Usage:
    registry = ProviderRegistry.build()
    games    = registry.get_games_for_date(date.today())
    props    = registry.get_player_props(date.today())
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from config import get_credentials
from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import DataSource, PropType
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


def _build_providers() -> dict[str, list[BaseProvider]]:
    """
    Instantiate and return providers grouped by role.

    Groups:
      'slate'    - game schedule / game IDs
      'modeling' - player stats / context / injuries
      'odds'     - sportsbook pricing
      'general'  - fallback for any unrouted call
    """
    creds = get_credentials()
    groups: dict[str, list[BaseProvider]] = {
        "slate": [],
        "modeling": [],
        "odds": [],
        "general": [],
    }

    # ------------------------------------------------------------------
    # SLATE: Sportradar primary -> SportsDataIO fallback
    # ------------------------------------------------------------------
    if creds.sportradar_key:
        try:
            from providers.sportradar_provider import SportradarProvider
            p = SportradarProvider(creds.sportradar_key)
            groups["slate"].append(p)
            groups["general"].append(p)
            logger.info("Sportradar provider registered (slate primary)")
        except Exception as exc:
            logger.warning("Failed to load Sportradar provider: %s", exc)

    # ------------------------------------------------------------------
    # MODELING: nba_api (no key required) + SportsDataIO
    # nba_api handles: usage, touches, possessions, pace, splits
    # SportsDataIO handles: injuries, season stats, rosters, depth charts
    # ------------------------------------------------------------------
    try:
        from providers.nba_api_provider import NBAApiProvider
        nba = NBAApiProvider()
        if nba.is_available():
            groups["modeling"].append(nba)
            logger.info("nba_api provider registered (modeling primary: usage/tracking/splits)")
        else:
            logger.warning("nba_api package not installed - install with: pip install nba_api")
    except Exception as exc:
        logger.warning("Failed to load nba_api provider: %s", exc)

    if creds.sportsdataio_key:
        try:
            from providers.sportsdataio_provider import SportsDataIOProvider
            sdio = SportsDataIOProvider(creds.sportsdataio_key)
            groups["modeling"].append(sdio)
            groups["slate"].append(sdio)   # also a slate fallback
            groups["general"].append(sdio)
            logger.info(
                "SportsDataIO provider registered "
                "(modeling primary: injuries/stats/rosters/depth)"
            )
        except Exception as exc:
            logger.warning("Failed to load SportsDataIO provider: %s", exc)
    else:
        logger.warning(
            "No SPORTSDATAIO_API_KEY found - injuries, rosters, and season stats "
            "will be unavailable.  Add your key to .env to enable this source."
        )

    # ------------------------------------------------------------------
    # ODDS: The Odds API ONLY
    # ------------------------------------------------------------------
    if creds.odds_api_key:
        try:
            from providers.odds_api_provider import OddsAPIProvider
            odds = OddsAPIProvider(creds.odds_api_key)
            groups["odds"].append(odds)
            groups["general"].append(odds)
            logger.info("The Odds API provider registered (odds only)")
        except Exception as exc:
            logger.warning("Failed to load Odds API provider: %s", exc)
    else:
        logger.warning(
            "No THE_ODDS_API_KEY found - prop odds will be unavailable. "
            "Add your key to .env to enable sportsbook data."
        )

    # ------------------------------------------------------------------
    # CSV import: always available, last resort override
    # ------------------------------------------------------------------
    try:
        from providers.csv_import_provider import CSVImportProvider
        csv_p = CSVImportProvider()
        groups["general"].append(csv_p)
        logger.debug("CSV import provider registered (manual override)")
    except Exception as exc:
        logger.warning("Failed to load CSV import provider: %s", exc)

    return groups


class ProviderRegistry:
    """
    Central routing layer for all data requests.

    Providers are tried in priority order per group.  The first provider
    that returns a non-empty result wins.  If every provider fails or
    returns empty, an empty list / None is returned and a warning is logged.

    Provider assignments (per architecture spec):
      Slate:     Sportradar -> SportsDataIO
      Modeling:  nba_api + SportsDataIO (complementary, not exclusive)
      Odds:      The Odds API only
    """

    def __init__(self, groups: dict[str, list[BaseProvider]]) -> None:
        self._groups = groups

    @classmethod
    def build(cls) -> "ProviderRegistry":
        """Factory: instantiate all configured providers and return a registry."""
        return cls(_build_providers())

    # ------------------------------------------------------------------
    # Internal routing helpers
    # ------------------------------------------------------------------

    def _try_list(self, group: str, method_name: str, *args, **kwargs) -> list:
        """Call *method_name* on each provider in *group*, return first non-empty list."""
        for provider in self._groups.get(group, []):
            try:
                result = getattr(provider, method_name)(*args, **kwargs)
                if result:
                    logger.debug(
                        "%s returned %d records from %s",
                        method_name, len(result), provider.source_name,
                    )
                    return result
            except NotImplementedError:
                continue
            except Exception as exc:
                logger.warning("%s failed on %s: %s", method_name, provider.source_name, exc)
        logger.warning("%s [%s]: all providers returned empty or failed", method_name, group)
        return []

    def _try_optional(self, group: str, method_name: str, *args, **kwargs):
        """Call *method_name* on each provider in *group*, return first non-None result."""
        for provider in self._groups.get(group, []):
            try:
                result = getattr(provider, method_name)(*args, **kwargs)
                if result is not None:
                    return result
            except NotImplementedError:
                continue
            except Exception as exc:
                logger.warning("%s failed on %s: %s", method_name, provider.source_name, exc)
        return None

    # ------------------------------------------------------------------
    # Schedule / Games  (SOURCE: Sportradar -> SportsDataIO)
    # ------------------------------------------------------------------

    def get_games_for_date(self, game_date: date) -> list[Game]:
        return self._try_list("slate", "get_games_for_date", game_date)

    # ------------------------------------------------------------------
    # Player data  (SOURCE: SportsDataIO primary, nba_api supplement)
    # ------------------------------------------------------------------

    def get_players_for_game(self, game_id: str) -> list[Player]:
        return self._try_list("modeling", "get_players_for_game", game_id)

    def get_player_context(self, player_id: str) -> Optional[Player]:
        return self._try_optional("modeling", "get_player_context", player_id)

    def get_player_recent_form(self, player_id: str, n: int = 10) -> list[dict]:
        return self._try_list("modeling", "get_player_recent_form", player_id, n)

    # ------------------------------------------------------------------
    # Team / matchup context  (SOURCE: SportsDataIO + nba_api blended)
    # ------------------------------------------------------------------

    def get_team_context(self, team_id: str) -> Optional[dict]:
        return self._try_optional("modeling", "get_team_context", team_id)

    def get_team_defense(self, team_id: str) -> Optional[TeamDefense]:
        return self._try_optional("modeling", "get_team_defense", team_id)

    def get_defense_vs_position(
        self, team_id: str, position: str, prop_type: PropType
    ) -> float:
        for provider in self._groups.get("modeling", []):
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
        for provider in self._groups.get("modeling", []):
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
        return self._try_list("modeling", "get_matchup_history", player_id, opponent_team_id)

    # ------------------------------------------------------------------
    # Injuries / lineup  (SOURCE: SportsDataIO primary)
    # ------------------------------------------------------------------

    def get_injuries(self, game_date: Optional[date] = None) -> list[dict]:
        return self._try_list("modeling", "get_injuries", game_date)

    def get_lineups(self, game_date: Optional[date] = None) -> list[dict]:
        return self._try_list("modeling", "get_lineups", game_date)

    def get_depth_charts(self, team_id: Optional[str] = None) -> list[dict]:
        return self._try_list("modeling", "get_depth_charts", team_id)

    # ------------------------------------------------------------------
    # Odds / props  (SOURCE: The Odds API ONLY)
    # ------------------------------------------------------------------

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        """
        Return all player prop lines for *game_date*.

        SOURCE: The Odds API (ONLY source for sportsbook pricing).
        """
        return self._try_list("odds", "get_player_props", game_date)

    def get_live_odds(self, game_date: date) -> list[OddsLine]:
        return self._try_list("odds", "get_live_odds", game_date)

    def get_historical_odds(
        self, prop_type: PropType, days_back: int = 7
    ) -> list[OddsLine]:
        return self._try_list("odds", "get_historical_odds", prop_type, days_back)

    # ------------------------------------------------------------------
    # Tracking metrics  (SOURCE: nba_api primary)
    # ------------------------------------------------------------------

    def get_tracking_metrics(self, player_id: str) -> Optional[dict]:
        return self._try_optional("modeling", "get_tracking_metrics", player_id)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @property
    def active_providers(self) -> list[str]:
        seen: set[str] = set()
        result = []
        for group_providers in self._groups.values():
            for p in group_providers:
                key = p.__class__.__name__
                if key not in seen:
                    seen.add(key)
                    result.append(p.source_name.value)
        return result

    def summary(self) -> str:
        lines = [
            "Provider Registry",
            "  Slate  (Sportradar -> SportsDataIO fallback):",
        ]
        for p in self._groups.get("slate", []):
            status = "live" if p.is_available() else "unavailable"
            lines.append(f"    - {p.__class__.__name__} [{status}]")

        lines.append("  Modeling (nba_api + SportsDataIO):")
        for p in self._groups.get("modeling", []):
            status = "live" if p.is_available() else "unavailable"
            lines.append(f"    - {p.__class__.__name__} [{status}]")

        lines.append("  Odds (The Odds API only):")
        for p in self._groups.get("odds", []):
            status = "live" if p.is_available() else "unavailable"
            lines.append(f"    - {p.__class__.__name__} [{status}]")

        return "\n".join(lines)
