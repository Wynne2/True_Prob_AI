"""
Slate Scanner.

Orchestrates the full daily scan:

STEP 1: Pull today's slate (game IDs, team matchups)
        SOURCE: Sportradar (primary) → SportsDataIO (fallback)

STEP 2: Pull live prop markets and odds
        SOURCE: The Odds API (ONLY source for sportsbook pricing)

STEP 3: Warm all service-layer caches (batch, not per-prop)
        - PlayerContextService  → SportsDataIO season stats + depth charts
        - InjuryContextService  → SportsDataIO injuries + projected lineups
        - UsageTrackingService  → nba_api advanced + tracking dashboards
        - SplitsService         → nba_api split context dashboards
        - MatchupContextService → SportsDataIO + nba_api team stats

STEP 4: Build Player objects for all players on today's slate
        SOURCE: PlayerContextService (SportsDataIO primary + nba_api supplement)

STEP 5: Build feature store (FeatureVectors for all player × prop_type combos)
        SOURCE: PlayerFeatureBuilder (blends all service outputs)

STEP 6: For each prop, retrieve cached feature vector, evaluate, compute edge.

This ensures nba_api is called in batches (STEP 3) and never once per prop.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from domain.entities import Game, OddsLine, Player, PropProbability
from utils.date_utils import filter_pregame_games
from domain.enums import PropType
from engine.prop_evaluator import PropEvaluator

logger = logging.getLogger(__name__)


class SlateScanner:
    """Scans every NBA game on a given date and evaluates all props."""

    def __init__(self) -> None:
        self._evaluator = PropEvaluator()
        # Populated by scan() for CLI/UI messaging (pregame filter vs empty slate).
        self.last_raw_slate_game_count: int = 0
        self.last_pregame_slate_game_count: int = 0

    def scan(
        self,
        game_date: date,
        prop_types: Optional[list[PropType]] = None,
        is_playoff: bool = False,
    ) -> list[PropProbability]:
        """
        Scan all games for *game_date* and return all evaluated props.

        Builds the feature store once, then evaluates props from cache.

        Args:
            game_date: The date to scan.
            prop_types: Optional whitelist of prop types.
            is_playoff: When True, mark all slate games as playoff (rotation/minute priors).

        Returns:
            List of PropProbability objects.
        """
        logger.info("SlateScanner: scanning slate for %s", game_date)
        self.last_raw_slate_game_count = 0
        self.last_pregame_slate_game_count = 0

        # ----------------------------------------------------------
        # STEP 1: Pull slate
        # SOURCE: Sportradar → SportsDataIO (via ProviderRegistry)
        # ----------------------------------------------------------
        games, all_odds = self._pull_slate_and_odds(game_date)
        if is_playoff:
            for g in games:
                g.is_playoff = True
            logger.info("SlateScanner: playoff mode ON — %d games flagged", len(games))

        if not games:
            logger.warning("SlateScanner: no games found for %s", game_date)
            return []

        pre_count = len(games)
        self.last_raw_slate_game_count = pre_count
        games = filter_pregame_games(games)
        skipped = pre_count - len(games)
        if skipped:
            logger.info(
                "SlateScanner: skipped %d game(s) already in progress or final (pregame props only)",
                skipped,
            )

        if not games:
            self.last_pregame_slate_game_count = 0
            logger.warning(
                "SlateScanner: no pregame slate left for %s (all games started or finished)",
                game_date,
            )
            return []

        self.last_pregame_slate_game_count = len(games)
        logger.info("SlateScanner: %d pregame games, %d odds lines loaded", len(games), len(all_odds))

        # ----------------------------------------------------------
        # STEP 3: Warm all service-layer caches (batch pulls, not per-prop)
        # SOURCE: nba_api (usage/tracking/splits) + SportsDataIO (injuries/stats)
        # ----------------------------------------------------------
        self._warm_services(game_date)

        # ----------------------------------------------------------
        # STEP 4: Build Player objects for all players on today's slate
        # SOURCE: PlayerContextService
        # ----------------------------------------------------------
        players_by_game, game_map = self._load_players(games, game_date)

        # ----------------------------------------------------------
        # STEP 5 + 6: Build feature store and evaluate
        # ----------------------------------------------------------
        target_prop_types = prop_types or list(PropType)
        all_props = self._evaluate(
            games=games,
            players_by_game=players_by_game,
            game_map=game_map,
            all_odds=all_odds,
            prop_types=target_prop_types,
            game_date=game_date,
        )

        logger.info(
            "SlateScanner: complete — %d prop evaluations across %d games",
            len(all_props), len(games),
        )
        return all_props

    # ------------------------------------------------------------------
    # Internal pipeline steps
    # ------------------------------------------------------------------

    def _pull_slate_and_odds(
        self, game_date: date
    ) -> tuple[list[Game], list[OddsLine]]:
        """
        Load games and odds lines.

        Games:  Sportradar → SportsDataIO (via registry).
        Odds:   The Odds API (via registry).
        """
        from data.loaders import load_games, load_odds
        games = load_games(game_date)
        all_odds = load_odds(game_date)
        return games, all_odds

    def _warm_services(self, game_date: date) -> None:
        """
        Pre-warm all service-layer caches with batch API pulls.

        Called ONCE per daily scan.  All subsequent per-player lookups
        are served from in-memory / disk cache.

        SOURCE:
          - nba_api    → UsageTrackingService, SplitsService
          - SportsDataIO → PlayerContextService, InjuryContextService, MatchupContextService
        """
        from services import (
            injury_context_service,
            matchup_context_service,
            splits_service,
            usage_tracking_service,
        )
        from services.player_context_service import refresh as refresh_player_context

        logger.info("SlateScanner: warming service caches (batch pull)...")

        # PlayerContextService: SportsDataIO season stats + depth chart
        refresh_player_context(game_date)

        # InjuryContextService: SportsDataIO injuries + projected lineups
        injury_context_service.refresh(game_date)

        # UsageTrackingService: nba_api advanced + tracking dashboards
        usage_tracking_service.refresh()

        # SplitsService: nba_api home/away/last-N split dashboards
        splits_service.refresh()

        # MatchupContextService: SportsDataIO + nba_api team stats
        matchup_context_service.refresh()

        logger.info("SlateScanner: service caches warmed")

    def _load_players(
        self, games: list[Game], game_date: date
    ) -> tuple[dict[str, list[Player]], dict[str, Game]]:
        """
        Build player lists for each game.

        SOURCE: PlayerContextService (SportsDataIO + nba_api blend).

        Returns:
            players_by_game: game_id -> list of Player objects
            game_map: game_id -> Game object
        """
        from services.player_context_service import get_players_for_game

        players_by_game: dict[str, list[Player]] = {}
        game_map: dict[str, Game] = {}

        for game in games:
            game_map[game.game_id] = game
            players = get_players_for_game(
                game.game_id,
                game.home_team_id,
                game.away_team_id,
                game_date,
            )
            if not players:
                # Fallback: use the registry directly (SportsDataIO game participants)
                from data.loaders import load_players_for_game
                players = load_players_for_game(game.game_id)

            players_by_game[game.game_id] = players
            logger.debug(
                "Game %s (%s vs %s): %d players",
                game.game_id, game.away_team_abbr, game.home_team_abbr, len(players),
            )

        return players_by_game, game_map

    def _evaluate(
        self,
        games: list[Game],
        players_by_game: dict[str, list[Player]],
        game_map: dict[str, Game],
        all_odds: list[OddsLine],
        prop_types: list[PropType],
        game_date: date,
    ) -> list[PropProbability]:
        """
        Build feature store and run prop evaluator for all players.

        Feature vectors are built ONCE for all (player, prop_type) pairs,
        then each prop evaluation reads from the pre-built store.
        """
        from data.builders.player_feature_builder import build_feature_store
        from services.dvp_service import refresh_dvp_tables

        # Build DvP tables from available game logs before evaluation
        self._ensure_dvp_tables(game_date)

        # Flatten all players for the feature builder
        all_players: list[Player] = []
        opponent_map: dict[str, tuple[str, str]] = {}
        is_home_map: dict[str, bool] = {}
        game_id_map: dict[str, str] = {}
        game_total_map: dict[str, float] = {}
        spread_map: dict[str, float] = {}

        for game in games:
            players = players_by_game.get(game.game_id, [])
            for player in players:
                all_players.append(player)
                is_home = player.team_id == game.home_team_id
                opp_id = game.away_team_id if is_home else game.home_team_id
                opp_abbr = game.away_team_abbr if is_home else game.home_team_abbr
                opponent_map[player.player_id] = (opp_id, opp_abbr)
                is_home_map[player.player_id] = is_home
                game_id_map[player.player_id] = game.game_id
                game_total_map[player.player_id] = game.game_total
                spread_map[player.player_id] = game.home_spread

        # Build the feature store (STEP 5 — all-at-once, never per-prop)
        feature_store = build_feature_store(
            players=all_players,
            opponent_map=opponent_map,
            prop_types=[pt.value for pt in prop_types],
            is_home_map=is_home_map,
            game_id_map=game_id_map,
            game_total_map=game_total_map,
            spread_map=spread_map,
            game_date=game_date,
        )

        # STEP 6: Evaluate each prop using cached feature vectors
        all_prop_results: list[PropProbability] = []

        for game in games:
            players = players_by_game.get(game.game_id, [])
            for player in players:
                is_home = is_home_map.get(player.player_id, True)
                opp_defense = self._load_defense(opponent_map.get(player.player_id, ("", ""))[0])

                props = self._evaluator.evaluate_all_props(
                    player=player,
                    game=game,
                    defense=opp_defense,
                    all_odds=all_odds,
                    is_home=is_home,
                    prop_types=prop_types,
                    feature_store=feature_store,
                )
                all_prop_results.extend(props)

        return all_prop_results

    def _load_defense(self, team_id: str):
        """Load TeamDefense for *team_id* from the service layer."""
        if not team_id:
            return None
        try:
            from data.loaders import load_team_defense
            return load_team_defense(team_id)
        except Exception as exc:
            logger.debug("Could not load defense for team %s: %s", team_id, exc)
            return None

    def _ensure_dvp_tables(self, game_date: date) -> None:
        """
        Build DvP tables if not already loaded.

        SOURCE: nba_api + SportsDataIO game logs via dvp_service.
        """
        from services.dvp_service import is_loaded, refresh_dvp_tables

        if is_loaded():
            return

        try:
            from data.loaders.sportsdataio_loader import (
                fetch_player_season_stats,
                fetch_depth_charts,
                index_by_player_id,
            )

            # Build a position map from SportsDataIO depth charts (primary)
            depth = fetch_depth_charts()
            position_map = {
                r["player_id"]: r.get("position", "G")
                for r in depth if r.get("player_id")
            }

            # Fetch real game logs from SportsDataIO for DvP computation.
            # Use the feature store (already built by _build_feature_store) when
            # available; otherwise fall back to fetching the last 15 games for
            # each active player in the position map.
            game_logs: list[dict] = []
            try:
                from data.loaders.sportsdataio_loader import fetch_player_game_logs
                from domain.constants import SDIO_SEASON
                for player_id in list(position_map.keys())[:150]:  # cap API calls
                    logs = fetch_player_game_logs(
                        player_id, season=SDIO_SEASON, num_games=15
                    )
                    game_logs.extend(logs)
                logger.info(
                    "DvP: loaded %d game log records from %d players",
                    len(game_logs), min(len(position_map), 150),
                )
            except Exception as log_exc:
                logger.warning("DvP: could not fetch game logs (%s); using empty set", log_exc)

            refresh_dvp_tables(
                player_game_logs=game_logs,
                position_map=position_map,
                cache_date=game_date,
            )
        except Exception as exc:
            logger.warning("DvP table build failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Filtered scan convenience method
    # ------------------------------------------------------------------

    def scan_with_filter(
        self,
        game_date: date,
        min_edge: float = 0.0,
        prop_types: Optional[list[PropType]] = None,
        min_confidence: Optional[str] = None,
        is_playoff: bool = False,
    ) -> list[PropProbability]:
        """Scan and immediately apply basic filters."""
        from domain.enums import ConfidenceTier
        all_props = self.scan(game_date, prop_types=prop_types, is_playoff=is_playoff)
        filtered = [p for p in all_props if p.edge >= min_edge]

        if min_confidence:
            tier_order = {"very_low": 0, "low": 1, "medium": 2, "high": 3}
            min_level = tier_order.get(min_confidence.lower(), 0)
            filtered = [
                p for p in filtered
                if tier_order.get(p.confidence.value, 0) >= min_level
            ]

        return filtered
