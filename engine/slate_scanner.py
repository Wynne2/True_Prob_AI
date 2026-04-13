"""
Slate Scanner.

Orchestrates the full daily scan:
1. Load today's games.
2. Load all player props / odds.
3. For each game, load players + defensive context.
4. Run the prop evaluator for every player × prop type combination.
5. Return a flat list of all PropProbability objects with edge computed.

This is the single entry point called by the CLI and Streamlit app
to populate the prop universe for a given date.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from domain.entities import Game, OddsLine, Player, PropProbability
from domain.enums import PropType
from engine.prop_evaluator import PropEvaluator

logger = logging.getLogger(__name__)


class SlateScanner:
    """Scans every NBA game on a given date and evaluates all props."""

    def __init__(self) -> None:
        self._evaluator = PropEvaluator()

    def scan(
        self,
        game_date: date,
        prop_types: Optional[list[PropType]] = None,
    ) -> list[PropProbability]:
        """
        Scan all games for *game_date* and return all evaluated props.

        Args:
            game_date: The date to scan (defaults to today).
            prop_types: Optional whitelist of prop types to evaluate.
                        If None, all supported prop types are evaluated.

        Returns:
            List of PropProbability objects, one per (player, line, side, book).
        """
        from data.loaders import (
            load_defense_by_abbr,
            load_games,
            load_odds,
            load_players_for_game,
        )

        logger.info("Scanning slate for %s...", game_date)
        games = load_games(game_date)
        if not games:
            logger.warning("No games found for %s", game_date)
            return []
        logger.info("Found %d games", len(games))

        all_odds = load_odds(game_date)
        logger.info("Loaded %d odds lines across all books", len(all_odds))

        all_props: list[PropProbability] = []

        for game in games:
            logger.debug("Processing game: %s vs %s", game.away_team_abbr, game.home_team_abbr)
            players = load_players_for_game(game.game_id)
            logger.debug("  %d players loaded", len(players))

            home_defense = load_defense_by_abbr(game.home_team_abbr)
            away_defense = load_defense_by_abbr(game.away_team_abbr)

            for player in players:
                is_home = player.team_abbr == game.home_team_abbr
                # The defense the player faces is the opponent's defense
                opp_defense = away_defense if is_home else home_defense

                props = self._evaluator.evaluate_all_props(
                    player=player,
                    game=game,
                    defense=opp_defense,
                    all_odds=all_odds,
                    is_home=is_home,
                    prop_types=prop_types,
                )
                all_props.extend(props)

        logger.info(
            "Slate scan complete: %d prop evaluations across %d games",
            len(all_props),
            len(games),
        )
        return all_props

    def scan_with_filter(
        self,
        game_date: date,
        min_edge: float = 0.0,
        prop_types: Optional[list[PropType]] = None,
        min_confidence: Optional[str] = None,
    ) -> list[PropProbability]:
        """
        Scan and immediately apply basic filters.

        Args:
            game_date: Date to scan.
            min_edge: Minimum edge fraction (e.g. 0.05 = 5%).
            prop_types: Optional prop type whitelist.
            min_confidence: Minimum confidence tier name ('high', 'medium', etc.).
        """
        from domain.enums import ConfidenceTier
        all_props = self.scan(game_date, prop_types=prop_types)

        filtered = [p for p in all_props if p.edge >= min_edge]

        if min_confidence:
            tier_order = {
                "very_low": 0,
                "low": 1,
                "medium": 2,
                "high": 3,
            }
            min_level = tier_order.get(min_confidence.lower(), 0)
            filtered = [
                p for p in filtered
                if tier_order.get(p.confidence.value, 0) >= min_level
            ]

        return filtered
