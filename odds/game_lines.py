"""
Game-line consensus from prediction-market ladders.

Exchanges list many strikes per game (e.g. totals 194.5 … 240.5).  The
market's main line is the strike whose no-vig probability is closest to 50%,
and the consensus across books is the median of those main lines.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from statistics import median
from typing import Iterable, Optional

from domain.entities import Game, GameLine

logger = logging.getLogger(__name__)


def _main_line(pairs: dict[float, tuple[float, float]]) -> Optional[float]:
    """Pick the line whose first-side no-vig probability is closest to 0.5."""
    best_line, best_gap = None, None
    for line, (first_cost, second_cost) in pairs.items():
        total = first_cost + second_cost
        if total <= 0:
            continue
        gap = abs(first_cost / total - 0.5)
        if best_gap is None or gap < best_gap:
            best_line, best_gap = line, gap
    return best_line


def consensus_game_lines(lines: Iterable[GameLine]) -> dict[tuple[str, str], dict[str, float]]:
    """
    Return {(away_abbr, home_abbr): {'total': x, 'home_spread': y}}.

    *home_spread* is from the home team's perspective (negative = favourite).
    """
    totals: dict[tuple, dict] = defaultdict(lambda: defaultdict(dict))
    spreads: dict[tuple, dict] = defaultdict(lambda: defaultdict(dict))

    for gl in lines:
        key = (gl.away_abbr, gl.home_abbr)
        if gl.line is None:
            continue
        if gl.market == "total" and gl.selection in ("over", "under"):
            totals[key][(gl.book, gl.line)][gl.selection] = gl.price
        elif gl.market == "spread" and gl.selection in key:
            home_line = gl.line if gl.selection == gl.home_abbr else -gl.line
            side = "home" if gl.selection == gl.home_abbr else "away"
            spreads[key][(gl.book, home_line)][side] = gl.price

    result: dict[tuple[str, str], dict[str, float]] = {}
    for key in set(totals) | set(spreads):
        entry: dict[str, float] = {}
        total_mains = []
        by_book: dict = defaultdict(dict)
        for (book, line), sides in totals.get(key, {}).items():
            if "over" in sides and "under" in sides:
                by_book[book][line] = (sides["over"], sides["under"])
        for pairs in by_book.values():
            main = _main_line(pairs)
            if main is not None:
                total_mains.append(main)
        if total_mains:
            entry["total"] = float(median(total_mains))

        spread_mains = []
        by_book = defaultdict(dict)
        for (book, home_line), sides in spreads.get(key, {}).items():
            if "home" in sides and "away" in sides:
                by_book[book][home_line] = (sides["home"], sides["away"])
        for pairs in by_book.values():
            main = _main_line(pairs)
            if main is not None:
                spread_mains.append(main)
        if spread_mains:
            entry["home_spread"] = float(median(spread_mains))

        if entry:
            result[key] = entry
    return result


def apply_game_lines(games: list[Game], lines: Iterable[GameLine]) -> int:
    """
    Fill game_total / home_spread / implied team totals on *games* from
    consensus market lines.  Existing non-zero values are kept.

    Returns the number of games enriched.
    """
    consensus = consensus_game_lines(lines)
    enriched = 0
    for game in games:
        entry = consensus.get((game.away_team_abbr, game.home_team_abbr))
        if entry is None:
            # Some slates list teams in the opposite order; flip the spread.
            flipped = consensus.get((game.home_team_abbr, game.away_team_abbr))
            if flipped is not None:
                entry = dict(flipped)
                if "home_spread" in entry:
                    entry["home_spread"] = -entry["home_spread"]
        if not entry:
            continue
        changed = False
        if not game.game_total and "total" in entry:
            game.game_total = entry["total"]
            changed = True
        if not game.home_spread and "home_spread" in entry:
            game.home_spread = entry["home_spread"]
            changed = True
        if game.game_total and not (game.home_implied_total or game.away_implied_total):
            game.home_implied_total = round(game.game_total / 2.0 - game.home_spread / 2.0, 1)
            game.away_implied_total = round(game.game_total / 2.0 + game.home_spread / 2.0, 1)
            changed = True
        if changed:
            enriched += 1
    if enriched:
        logger.info("Prediction-market game lines enriched %d/%d games", enriched, len(games))
    return enriched
