"""
Line shopping: aggregate odds from multiple sportsbooks and identify the
best available price for each prop side.

The best line for a bettor is the highest odds (least negative / most
positive American odds) available for the side they want to take.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from domain.entities import OddsLine
from domain.enums import BookName, PropType

logger = logging.getLogger(__name__)


@dataclass
class BestLine:
    """Best available price for one player/prop/side combination."""
    player_id: str
    player_name: str
    prop_type: PropType
    line: float
    side: str                   # 'over' or 'under'
    best_odds: int              # American odds
    best_book: BookName
    best_book_key: str          # Raw provider bookmaker key for the selected quote.
    selected_quote: OddsLine    # Exact immutable quote selected for this result.
    all_books: dict[str, int]   # Canonical bookmaker key → American odds for this side.
    book_count: int
    meets_minimum_coverage: bool
    game_id: str = ""
    team_abbr: str = ""
    opponent_abbr: str = ""


def _group_key(odds_line: OddsLine) -> tuple:
    """Grouping key: same event + player + prop type + line."""
    return (
        odds_line.game_id,
        odds_line.player_id,
        odds_line.prop_type,
        odds_line.line,
    )


def bookmaker_key(odds_line: OddsLine) -> str:
    """Return the provider's unique bookmaker identity for a quote."""
    return odds_line.book_key or odds_line.book.value


def _observed_at(odds_line: OddsLine) -> float:
    """Use the oldest possible timestamp for quotes with no update time."""
    return odds_line.timestamp.timestamp() if odds_line.timestamp else float("-inf")


def _latest_quotes_by_book(group: list[OddsLine]) -> dict[str, OddsLine]:
    """Keep only the freshest quote from each distinct provider bookmaker."""
    latest: dict[str, OddsLine] = {}
    for quote in group:
        key = bookmaker_key(quote)
        current = latest.get(key)
        if current is None or _observed_at(quote) >= _observed_at(current):
            latest[key] = quote
    return latest


def shop_lines(
    all_lines: list[OddsLine],
    player_id: Optional[str] = None,
    prop_type: Optional[PropType] = None,
    min_books: int = 2,
    max_quote_age_seconds: Optional[int] = None,
) -> list[BestLine]:
    """
    Aggregate odds across all books and return the best over/under price
    for each unique player/prop/line combination.

    Only returns lines that at least *min_books* distinct sportsbooks agree
    on. Sparse lines are excluded rather than presented as consensus.

    Args:
        all_lines: All available OddsLine objects (across all books).
        player_id: If provided, restrict to one player.
        prop_type: If provided, restrict to one prop type.
        min_books: Minimum distinct books required to include a line.

    Returns:
        List of BestLine objects (one per side per unique prop/line).
    """
    # Optional filters
    lines = all_lines
    if player_id:
        lines = [l for l in lines if l.player_id == player_id]
    if prop_type:
        lines = [l for l in lines if l.prop_type == prop_type]
    if max_quote_age_seconds is not None:
        now = datetime.now().timestamp()
        lines = [
            line for line in lines
            if line.timestamp is None
            or now - _observed_at(line) <= max_quote_age_seconds
        ]

    # Group by (event, player_id, prop_type, line)
    groups: dict[tuple, list[OddsLine]] = defaultdict(list)
    for line in lines:
        groups[_group_key(line)].append(line)

    # --- Consensus filter ---
    # For each player+prop, retain only lines with explicit consensus coverage.
    by_player_prop: dict[tuple, list[tuple]] = defaultdict(list)
    for key in groups:
        event_id, pid, ptype, _ = key
        by_player_prop[(event_id, pid, ptype)].append(key)

    allowed_keys: set[tuple] = set()
    for (_event_id, _pid, _ptype), keys in by_player_prop.items():
        book_counts = {
            k: len(_latest_quotes_by_book(groups[k]))
            for k in keys
        }
        for k, cnt in book_counts.items():
            if cnt >= min_books:
                allowed_keys.add(k)

    results: list[BestLine] = []

    for (event_id, pid, ptype, numeric_line), group in groups.items():
        if (event_id, pid, ptype, numeric_line) not in allowed_keys:
            continue
        if not group:
            continue

        latest_by_book = _latest_quotes_by_book(group)
        ref = next(iter(latest_by_book.values()))
        over_books = {
            book_key: quote.over_odds
            for book_key, quote in latest_by_book.items()
        }
        under_books = {
            book_key: quote.under_odds
            for book_key, quote in latest_by_book.items()
        }

        # Best over
        if over_books:
            best_over_key = max(over_books, key=over_books.get)
            best_over_quote = latest_by_book[best_over_key]
            results.append(BestLine(
                player_id=pid,
                player_name=ref.player_name,
                prop_type=ptype,
                line=numeric_line,
                side="over",
                best_odds=best_over_quote.over_odds,
                best_book=best_over_quote.book,
                best_book_key=best_over_key,
                selected_quote=best_over_quote,
                all_books=over_books,
                book_count=len(latest_by_book),
                meets_minimum_coverage=True,
                game_id=event_id,
                team_abbr=ref.team_abbr,
                opponent_abbr=ref.opponent_abbr,
            ))

        # Best under
        if under_books:
            best_under_key = max(under_books, key=under_books.get)
            best_under_quote = latest_by_book[best_under_key]
            results.append(BestLine(
                player_id=pid,
                player_name=ref.player_name,
                prop_type=ptype,
                line=numeric_line,
                side="under",
                best_odds=best_under_quote.under_odds,
                best_book=best_under_quote.book,
                best_book_key=best_under_key,
                selected_quote=best_under_quote,
                all_books=under_books,
                book_count=len(latest_by_book),
                meets_minimum_coverage=True,
                game_id=event_id,
                team_abbr=ref.team_abbr,
                opponent_abbr=ref.opponent_abbr,
            ))

    logger.debug(
        "shop_lines: %d groups → %d allowed (min_books=%d) → %d BestLines",
        len(groups), len(allowed_keys), min_books, len(results),
    )
    return results


def get_best_over(
    all_lines: list[OddsLine],
    player_id: str,
    prop_type: PropType,
    line: float,
) -> Optional[BestLine]:
    """Return the best over price for a specific player/prop/line."""
    shopped = shop_lines(all_lines, player_id=player_id, prop_type=prop_type)
    candidates = [b for b in shopped if b.side == "over" and b.line == line]
    return max(candidates, key=lambda b: b.best_odds) if candidates else None


def get_best_under(
    all_lines: list[OddsLine],
    player_id: str,
    prop_type: PropType,
    line: float,
) -> Optional[BestLine]:
    """Return the best under price for a specific player/prop/line."""
    shopped = shop_lines(all_lines, player_id=player_id, prop_type=prop_type)
    candidates = [b for b in shopped if b.side == "under" and b.line == line]
    return max(candidates, key=lambda b: b.best_odds) if candidates else None


def lines_for_player(
    all_lines: list[OddsLine], player_id: str
) -> dict[PropType, list[OddsLine]]:
    """Return all lines for one player, grouped by prop type."""
    result: dict[PropType, list[OddsLine]] = defaultdict(list)
    for line in all_lines:
        if line.player_id == player_id:
            result[line.prop_type].append(line)
    return dict(result)
