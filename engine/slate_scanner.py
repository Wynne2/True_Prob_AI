"""Pregame scan coordinator for the BALLDONTLIE-only P/R/A workflow."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date
from typing import Optional

from domain.entities import Game, OddsLine, Player, PropProbability
from utils.date_utils import filter_pregame_games
from domain.enums import InjuryStatus, PropType
from engine.prop_evaluator import PropEvaluator

logger = logging.getLogger(__name__)


class SlateScanner:
    """Scans every NBA game on a given date and evaluates all props."""

    def __init__(self) -> None:
        self._evaluator = PropEvaluator()
        # Populated by scan() for CLI/UI messaging (pregame filter vs empty slate).
        self.last_raw_slate_game_count: int = 0
        self.last_pregame_slate_game_count: int = 0
        self.last_exclusions: list[dict[str, str]] = []
        self.last_updates: list = []
        self._last_odds: list[OddsLine] = []
        self._last_feature_store: dict = {}

    def scan(
        self,
        game_date: date,
        prop_types: Optional[list[PropType]] = None,
        is_playoff: bool = False,
    ) -> list[PropProbability]:
        """
        Scan all games for *game_date* and return all evaluated props.

        Resolves scheduled games, actual quoted player IDs, active roster
        membership, availability observations, and completed historical logs
        before evaluating P/R/A offers.

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
        # Step 1: pull one-provider schedule and player-prop quote observations.
        # ----------------------------------------------------------
        games, all_odds = self._pull_slate_and_odds(game_date)
        from services.activity_service import quote_updates
        self.last_updates = quote_updates(self._last_odds, all_odds)
        self._last_odds = all_odds
        if is_playoff:
            for g in games:
                g.is_playoff = True
            logger.info("SlateScanner: playoff mode ON — %d games flagged", len(games))

        if not games:
            logger.warning("SlateScanner: no games found for %s", game_date)
            return []

        pre_count = len(games)
        self.last_raw_slate_game_count = pre_count
        from config import get_settings
        games = (
            filter_pregame_games(games)
            if get_settings().app_mode == "live"
            else games
        )
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
        # Step 2: the provider client owns bounded cache and retry behavior.
        # ----------------------------------------------------------
        self._warm_services(game_date)

        # ----------------------------------------------------------
        # Step 3: build only quoted current-roster participants from completed
        # historical statistics. Target-game box scores are never consulted.
        # ----------------------------------------------------------
        players_by_game, game_map = self._load_players(games, game_date, all_odds)

        # ----------------------------------------------------------
        # Step 4: evaluate the initial supported P/R/A scope and persist inputs.
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
        self._record_forecasts(all_props, self._last_feature_store)

        logger.info(
            "SlateScanner: complete — %d prop evaluations across %d games",
            len(all_props), len(games),
        )
        return all_props

    def _record_forecasts(
        self,
        props: list[PropProbability],
        feature_store: Optional[dict] = None,
    ) -> None:
        """Append a reproducible record of every evaluated offer."""
        if not props:
            return
        try:
            from services.evaluation_store import EvaluationStore

            store = EvaluationStore()
            snapshot_id = store.record_forecasts(props, feature_store or {})
            store.record_exclusions(self.last_exclusions, snapshot_id=snapshot_id)
        except Exception as exc:
            logger.warning("SlateScanner: forecast ledger write failed: %s", exc)

    # ------------------------------------------------------------------
    # Internal pipeline steps
    # ------------------------------------------------------------------

    def _pull_slate_and_odds(
        self, game_date: date
    ) -> tuple[list[Game], list[OddsLine]]:
        """
        Load games and odds lines.

        Both schedule and quotes come from BALLDONTLIE in live mode.
        """
        from data.loaders import load_games, load_odds
        games = load_games(game_date)
        all_odds = load_odds(game_date)
        return games, self._canonicalize_odds_event_ids(games, all_odds)

    @staticmethod
    def _canonicalize_odds_event_ids(
        games: list[Game],
        odds: list[OddsLine],
    ) -> list[OddsLine]:
        """Map provider event IDs to one unambiguous canonical slate game ID."""
        games_by_id = {game.game_id: game for game in games}
        canonical: list[OddsLine] = []
        rejected = 0
        for quote in odds:
            if quote.game_id in games_by_id:
                canonical.append(quote)
                continue
            quote_teams = {
                quote.event_home_team_abbr.upper(),
                quote.event_away_team_abbr.upper(),
            } - {""}
            matches = [
                game for game in games
                if quote_teams
                and {game.home_team_abbr.upper(), game.away_team_abbr.upper()} == quote_teams
            ]
            if len(matches) == 1:
                canonical.append(replace(quote, game_id=matches[0].game_id))
            else:
                rejected += 1
        if rejected:
            logger.warning(
                "SlateScanner: rejected %d odds quotes without an unambiguous event mapping",
                rejected,
            )
        return canonical

    def _warm_services(self, game_date: date) -> None:
        """
        Provider cache boundary marker for scan logging.
        """
        # BALLDONTLIE provider calls are cached at the client boundary. Keeping
        # context assembly inside the provider prevents legacy direct-provider
        # imports and ensures every live request has one provenance path.
        logger.info("SlateScanner: provider-managed context cache is ready")

    def _load_players(
        self, games: list[Game], game_date: date, all_odds: list[OddsLine]
    ) -> tuple[dict[str, list[Player]], dict[str, Game]]:
        """
        Build player lists for each game.

        Source: BALLDONTLIE active roster and completed historical statistics.

        Returns:
            players_by_game: game_id -> list of Player objects
            game_map: game_id -> Game object
        """
        players_by_game: dict[str, list[Player]] = {}
        game_map: dict[str, Game] = {}

        for game in games:
            game_map[game.game_id] = game
            from data.loaders import load_players_for_game
            candidate_ids = {
                quote.player_id for quote in all_odds
                if quote.game_id == game.game_id
            }
            players = load_players_for_game(
                game.game_id,
                candidate_ids or None,
            )

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
        """Evaluate only the supported provider-backed pregame market scope."""
        return self._evaluate_provider_pregame(games, players_by_game, all_odds, prop_types)

    def _evaluate_provider_pregame(
        self,
        games: list[Game],
        players_by_game: dict[str, list[Player]],
        all_odds: list[OddsLine],
        prop_types: list[PropType],
    ) -> list[PropProbability]:
        """Evaluate BDL/demo players without any legacy data-provider service."""
        supported = {PropType.POINTS, PropType.REBOUNDS, PropType.ASSISTS}
        requested = [prop_type for prop_type in prop_types if prop_type in supported]
        if not requested:
            self.last_exclusions = [{"reason": "unsupported_market_scope"}]
            return []
        results: list[PropProbability] = []
        self.last_exclusions = []
        for game in games:
            for player in players_by_game.get(game.game_id, []):
                is_home = player.team_id == game.home_team_id
                if player.injury_status in {
                    InjuryStatus.UNKNOWN,
                    InjuryStatus.QUESTIONABLE,
                    InjuryStatus.DOUBTFUL,
                    InjuryStatus.DAY_TO_DAY,
                    InjuryStatus.OUT,
                }:
                    self.last_exclusions.append({
                        "game_id": game.game_id,
                        "player_id": player.player_id,
                        "reason": f"availability_{player.injury_status.value}",
                    })
                    continue
                for prop_type in requested:
                    self._last_feature_store[(player.player_id, prop_type.value)] = player
                results.extend(
                    self._evaluator.evaluate_all_props(
                        player,
                        game,
                        None,
                        all_odds,
                        is_home,
                        requested,
                    )
                )
        self._last_feature_store = {}
        return results

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
