"""
The evaluation line is chosen from the sportsbook prices only.

Every model variant is scored on that same line. The projection is not an input,
so a variant cannot be handed the threshold that flatters it.
"""

from __future__ import annotations

from domain.entities import OddsLine
from odds.normalizer import american_to_raw_implied_prob

_BOOK_RANK = {
    "fanduel": 0,
    "draftkings": 1,
    "betmgm": 2,
    "williamhill_us": 3,
    "betrivers": 4,
    "bovada": 5,
    "betonlineag": 6,
    "fanatics": 7,
}


def no_vig_probability(over_odds: int, under_odds: int, side: str) -> float:
    """Proportional no-vig probability. Both sides are required."""
    over = american_to_raw_implied_prob(int(over_odds))
    under = american_to_raw_implied_prob(int(under_odds))
    total = over + under
    if total <= 0:
        return 0.5
    if str(side).lower() == "under":
        return under / total
    return over / total


def _distance_from_even(line: OddsLine) -> float:
    over = american_to_raw_implied_prob(int(line.over_odds))
    under = american_to_raw_implied_prob(int(line.under_odds))
    return abs(over - 0.5) + abs(under - 0.5)


def select_evaluation_line(lines: list[OddsLine]) -> OddsLine:
    """
    One two-sided offer for a single player and prop.

    Prefer the offer closest to a 50/50 market. Ties break toward a stable
    book order, then the lower line.
    """
    if not lines:
        raise ValueError("No lines to choose from")
    return min(
        lines,
        key=lambda line: (
            _distance_from_even(line),
            _BOOK_RANK.get(line.book_key, 99),
            float(line.line),
        ),
    )


def select_evaluation_lines(lines: list[OddsLine]) -> tuple[list[OddsLine], list[OddsLine]]:
    """Return (chosen, alternates). Alternates are stored and not scored as extra samples."""
    groups: dict[tuple, list[OddsLine]] = {}
    for line in lines:
        key = (line.player_name, line.prop_type)
        groups.setdefault(key, []).append(line)
    chosen: list[OddsLine] = []
    alternates: list[OddsLine] = []
    for group in groups.values():
        pick = select_evaluation_line(group)
        chosen.append(pick)
        for line in group:
            if line is not pick:
                alternates.append(line)
    return chosen, alternates
