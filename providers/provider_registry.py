"""
Provider Registry.

Routes each data request to the appropriate provider according to the
source-of-truth assignments from the architecture spec:

  Slate (game schedule):
      BALLDONTLIE

  Player-prop modeling data:
      BALLDONTLIE (stats, injuries, pace, tracking)

  Odds / implied probability (merged across every source):
      Kalshi       (public, keyless prediction market)
      Polymarket   (public, keyless prediction market)
      The Odds API (sportsbooks, when THE_ODDS_API_KEY is set)

  Cache fallback:
      Derived cache -> graceful empty if all live sources fail

Retired from the live scan: SportsDataIO, Sportradar, FantasyPros, StatMuse,
RotoGrinders, Rotowire, NBA Official.

Usage:
    registry = ProviderRegistry.build()
    games    = registry.get_games_for_date(date.today())
    props    = registry.get_player_props(date.today())
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Optional

from config import get_credentials, get_prediction_market_config
from domain.entities import Game, GameLine, OddsLine, Player, TeamDefense
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
    # SLATE + MODELING: BALLDONTLIE only
    # ------------------------------------------------------------------
    if creds.balldontlie_key:
        try:
            from providers.balldontlie_provider import BallDontLieProvider
            bdl = BallDontLieProvider(creds.balldontlie_key)
            groups["slate"].append(bdl)
            groups["modeling"].append(bdl)
            logger.info("BALLDONTLIE provider registered (slate, players, injuries)")
        except Exception as exc:
            logger.warning("Failed to load BALLDONTLIE provider: %s", exc)
    else:
        logger.warning("No BALLDONTLIE_API_KEY — games, stats, and injuries will be missing")

    try:
        from providers.nba_api_provider import NBAApiProvider
        nba = NBAApiProvider()
        if nba.is_available():
            groups["modeling"].append(nba)
            logger.info("BALLDONTLIE stats facade registered (usage/tracking/splits)")
    except Exception as exc:
        logger.warning("Failed to load stats facade: %s", exc)

    # ------------------------------------------------------------------
    # ODDS: Kalshi + Polymarket (keyless) + The Odds API (keyed) — merged
    # ------------------------------------------------------------------
    pm_cfg = get_prediction_market_config()
    if pm_cfg.kalshi_enabled:
        try:
            from providers.kalshi_provider import KalshiProvider
            groups["odds"].append(KalshiProvider())
            logger.info("Kalshi provider registered (public NBA props + game lines)")
        except Exception as exc:
            logger.warning("Failed to load Kalshi provider: %s", exc)
    if pm_cfg.polymarket_enabled:
        try:
            from providers.polymarket_provider import PolymarketProvider
            groups["odds"].append(PolymarketProvider())
            logger.info("Polymarket provider registered (public NBA props + game lines)")
        except Exception as exc:
            logger.warning("Failed to load Polymarket provider: %s", exc)

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
            "No THE_ODDS_API_KEY found - sportsbook odds unavailable "
            "(Kalshi/Polymarket prediction-market odds still load)."
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

    Odds are the exception: every odds provider is queried and the results
    are merged, so each exchange/sportsbook contributes its own book.

    Provider assignments (per architecture spec):
      Slate:     BALLDONTLIE
      Modeling:  BALLDONTLIE
      Odds:      Kalshi + Polymarket + The Odds API (merged)
    """

    def __init__(self, groups: dict[str, list[BaseProvider]]) -> None:
        self._groups = groups
        self.last_odds_counts: dict[str, int] = {}
        self.last_odds_errors: dict[str, str] = {}

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

    def _merge_list(self, group: str, method_name: str, *args, **kwargs) -> list:
        """Call *method_name* on every provider in *group* in parallel and concatenate results."""
        providers = [p for p in self._groups.get(group, []) if hasattr(p, method_name)]
        counts: dict[str, int] = {}
        errors: dict[str, str] = {}

        def call(provider: BaseProvider):
            try:
                return provider, getattr(provider, method_name)(*args, **kwargs) or [], None
            except NotImplementedError:
                return provider, [], None
            except Exception as exc:
                return provider, [], exc

        merged: list = []
        if not providers:
            logger.warning("%s [%s]: no providers registered", method_name, group)
        else:
            with ThreadPoolExecutor(max_workers=len(providers)) as pool:
                outcomes = list(pool.map(call, providers))
            for provider, result, exc in outcomes:
                name = provider.source_name.value
                if exc is not None:
                    logger.warning("%s failed on %s: %s", method_name, name, exc)
                    errors[name] = str(exc)
                elif not result and getattr(provider, "last_errors", None):
                    errors[name] = "; ".join(provider.last_errors[:2])
                counts[name] = len(result)
                merged.extend(result)
            if not merged:
                logger.warning("%s [%s]: all providers returned empty or failed", method_name, group)
        if method_name == "get_player_props":
            self.last_odds_counts = counts
            self.last_odds_errors = errors
        return merged

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
    # Schedule / Games
    # ------------------------------------------------------------------

    def get_games_for_date(self, game_date: date) -> list[Game]:
        return self._try_list("slate", "get_games_for_date", game_date)

    # ------------------------------------------------------------------
    # Player data
    # ------------------------------------------------------------------

    def get_players_for_game(self, game_id: str) -> list[Player]:
        return self._try_list("modeling", "get_players_for_game", game_id)

    def get_player_context(self, player_id: str) -> Optional[Player]:
        """
        Return a fully-hydrated Player entity.

        First modeling provider that returns a player wins. Live scans
        use BALLDONTLIE only.
        """
        for provider in self._groups.get("modeling", []):
            try:
                result = provider.get_player_context(player_id)
                if result is not None:
                    return result
            except NotImplementedError:
                continue
            except Exception as exc:
                logger.warning("get_player_context failed on %s: %s", provider.source_name, exc)
        return None

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
    # Odds / props  (SOURCE: Kalshi + Polymarket + The Odds API, merged)
    # ------------------------------------------------------------------

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        """
        Return all player prop lines for *game_date* from every odds source.

        Per-source counts and errors are kept on ``last_odds_counts`` /
        ``last_odds_errors`` for the UI.
        """
        return self._merge_list("odds", "get_player_props", game_date)

    def get_live_odds(self, game_date: date) -> list[OddsLine]:
        return self._merge_list("odds", "get_live_odds", game_date)

    def get_game_lines(self, game_date: date) -> list[GameLine]:
        """Moneyline / spread / total sides for *game_date* from prediction markets."""
        return self._merge_list("odds", "get_game_lines", game_date)

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
            "  Slate  (BALLDONTLIE):",
        ]
        for p in self._groups.get("slate", []):
            status = "live" if p.is_available() else "unavailable"
            lines.append(f"    - {p.__class__.__name__} [{status}]")

        lines.append("  Modeling (BALLDONTLIE):")
        for p in self._groups.get("modeling", []):
            status = "live" if p.is_available() else "unavailable"
            lines.append(f"    - {p.__class__.__name__} [{status}]")

        lines.append("  Odds (Kalshi + Polymarket + The Odds API, merged):")
        for p in self._groups.get("odds", []):
            status = "live" if p.is_available() else "unavailable"
            lines.append(f"    - {p.__class__.__name__} [{status}]")

        return "\n".join(lines)
