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
    all_books: dict[BookName, int]   # book → American odds for this side
    game_id: str = ""
    team_abbr: str = ""
    opponent_abbr: str = ""


def _group_key(odds_line: OddsLine) -> tuple:
    """Grouping key: same player + prop type + line."""
    return (odds_line.player_id, odds_line.prop_type, odds_line.line)


def shop_lines(
    all_lines: list[OddsLine],
    player_id: Optional[str] = None,
    prop_type: Optional[PropType] = None,
    min_books: int = 2,
) -> list[BestLine]:
    """
    Aggregate odds across all books and return the best over/under price
    for each unique player/prop/line combination.

    Only returns lines that at least *min_books* sportsbooks agree on.
    This prevents alternate lines posted by a single book (e.g. Bovada's
    21.5–30.5 ladder for a player whose consensus line is 25.5 everywhere
    else) from generating fake edges.  If no line has min_books coverage,
    falls back to the line with the most book coverage.

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

    # Group by (player_id, prop_type, line)
    groups: dict[tuple, list[OddsLine]] = defaultdict(list)
    for line in lines:
        groups[_group_key(line)].append(line)

    # --- Consensus filter ---
    # For each player+prop, only keep lines that have >= min_books coverage.
    # If nothing meets the threshold, keep the line(s) with the most books.
    # Group the groups by (player_id, prop_type) to do per-player filtering.
    by_player_prop: dict[tuple, list[tuple]] = defaultdict(list)
    for key in groups:
        pid, ptype, _ = key
        by_player_prop[(pid, ptype)].append(key)

    allowed_keys: set[tuple] = set()
    for (pid, ptype), keys in by_player_prop.items():
        book_counts = {k: len({ol.book for ol in groups[k]}) for k in keys}
        max_count = max(book_counts.values())
        threshold = min(min_books, max_count)  # fall back if no line has min_books
        for k, cnt in book_counts.items():
            if cnt >= threshold:
                allowed_keys.add(k)

    results: list[BestLine] = []

    for (pid, ptype, numeric_line), group in groups.items():
        if (pid, ptype, numeric_line) not in allowed_keys:
            continue
        if not group:
            continue

        # Reference record for metadata
        ref = group[0]

        over_books: dict[BookName, int] = {}
        under_books: dict[BookName, int] = {}

        for ol in group:
            # For American odds: higher is always better for the bettor
            if ol.book not in over_books or ol.over_odds > over_books[ol.book]:
                over_books[ol.book] = ol.over_odds
            if ol.book not in under_books or ol.under_odds > under_books[ol.book]:
                under_books[ol.book] = ol.under_odds

        num_books = len(over_books)

        # Best over
        if over_books:
            best_over_book = max(over_books, key=lambda b: over_books[b])
            results.append(BestLine(
                player_id=pid,
                player_name=ref.player_name,
                prop_type=ptype,
                line=numeric_line,
                side="over",
                best_odds=over_books[best_over_book],
                best_book=best_over_book,
                all_books=over_books,
                game_id=ref.game_id,
                team_abbr=ref.team_abbr,
                opponent_abbr=ref.opponent_abbr,
            ))

        # Best under
        if under_books:
            best_under_book = max(under_books, key=lambda b: under_books[b])
            results.append(BestLine(
                player_id=pid,
                player_name=ref.player_name,
                prop_type=ptype,
                line=numeric_line,
                side="under",
                best_odds=under_books[best_under_book],
                best_book=best_under_book,
                all_books=under_books,
                game_id=ref.game_id,
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
