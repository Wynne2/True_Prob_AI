"""
Parlay Builder.

Generates all valid parlays from the qualifying prop universe subject to
user-supplied constraints.  Handles:
- Min-edge filtering per leg
- Odds range filtering per leg and for the combined parlay
- Max legs constraint
- Anti-correlation blocking
- Combined odds and combined edge calculation
- Correlation risk scoring
- Diversification bonus
"""

from __future__ import annotations

import itertools
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from domain.constants import (
    CORRELATION_BLOCK_THRESHOLD,
    DEFAULT_MAX_LEGS,
    DEFAULT_MAX_ODDS,
    DEFAULT_MIN_EDGE,
    DEFAULT_MIN_ODDS,
    PARLAY_RANK_WEIGHT_CONFIDENCE,
    PARLAY_RANK_WEIGHT_CORR_RISK,
    PARLAY_RANK_WEIGHT_EDGE,
)
from domain.entities import OddsLine, Parlay, ParlayLeg, PropProbability
from domain.enums import BookName, ConfidenceTier, PropType
from engine.correlation_engine import (
    combo_avg_correlation,
    correlation_risk_score,
    diversification_bonus,
    is_blocked,
)
from odds.normalizer import (
    american_to_decimal,
    american_to_raw_implied_prob,
    decimal_to_american,
    is_valid_american,
)
from odds.parlay_math import (
    parlay_combined_american,
    parlay_combined_decimal,
    parlay_combined_edge,
    parlay_combined_implied_probability,
    parlay_combined_true_probability_calibrated,
    parlay_expected_value,
)

logger = logging.getLogger(__name__)


@dataclass
class ParlayConstraints:
    """User-supplied constraints for parlay generation."""
    min_edge: float = DEFAULT_MIN_EDGE          # per-leg minimum
    max_legs: int = DEFAULT_MAX_LEGS
    min_legs: int = 2
    min_leg_odds: int = DEFAULT_MIN_ODDS        # American
    max_leg_odds: int = DEFAULT_MAX_ODDS        # American
    min_parlay_odds: int = -10000               # American (no floor by default)
    max_parlay_odds: int = 100000              # American (no ceiling by default)
    min_confidence: Optional[str] = None        # 'high', 'medium', 'low', 'very_low'
    allowed_prop_types: Optional[list[PropType]] = None
    max_results: int = 200                      # cap on parlays returned
    min_ticket_ev: float = 0.0                  # Expected profit per $1 stake after adjustments.


def _quote_key(quote: OddsLine) -> str:
    return quote.book_key or quote.book.value


def _quote_odds_for_prop(quote: OddsLine, prop: PropProbability) -> int:
    return quote.over_odds if prop.side.value == "over" else quote.under_odds


def _latest_quotes_for_prop(prop: PropProbability) -> dict[str, OddsLine]:
    """
    Return the freshest exact-line quote for each raw bookmaker key.

    Synthetic callers may not retain source quotes. In that case, the selected
    single-bet quote is the only available price and retains its own book key.
    """
    candidates = [
        quote for quote in prop.all_lines
        if quote.player_id == prop.player_id
        and quote.prop_type == prop.prop_type
        and quote.line == prop.line
        and (not quote.game_id or not prop.game_id or quote.game_id == prop.game_id)
    ]
    if prop.selected_quote is not None:
        candidates.append(prop.selected_quote)
    if not candidates:
        candidates.append(OddsLine(
            book=prop.best_book,
            player_id=prop.player_id,
            player_name=prop.player_name,
            prop_type=prop.prop_type,
            line=prop.line,
            over_odds=prop.sportsbook_odds,
            under_odds=prop.sportsbook_odds,
            game_id=prop.game_id,
            team_abbr=prop.team_abbr,
            opponent_abbr=prop.opponent_abbr,
            book_key=prop.best_book_key,
        ))

    latest: dict[str, OddsLine] = {}
    for quote in candidates:
        key = _quote_key(quote)
        current = latest.get(key)
        if current is None or _quote_timestamp(quote) >= _quote_timestamp(current):
            latest[key] = quote
    return latest


def _quote_timestamp(quote: OddsLine) -> float:
    """Return a comparable timestamp without mixing naive and aware datetimes."""
    return quote.timestamp.timestamp() if quote.timestamp else float("-inf")


def _prop_to_leg(prop: PropProbability, quote: Optional[OddsLine] = None) -> ParlayLeg:
    """Convert a PropProbability to a ParlayLeg using its exact book quote."""
    odds = _quote_odds_for_prop(quote, prop) if quote else prop.sportsbook_odds
    implied = american_to_raw_implied_prob(odds)
    return ParlayLeg(
        player_id=prop.player_id,
        player_name=prop.player_name,
        team_abbr=prop.team_abbr,
        opponent_abbr=prop.opponent_abbr,
        game_id=prop.game_id,
        prop_type=prop.prop_type,
        line=prop.line,
        side=prop.side,
        sportsbook=quote.book if quote else prop.best_book,
        sportsbook_odds=odds,
        projected_value=prop.projected_value,
        true_probability=prop.true_probability,
        implied_probability=implied,
        edge=prop.true_probability - implied,
        fair_odds=prop.fair_odds,
        confidence=prop.confidence,
        explanation=prop.explanation,
        best_book_key=(quote.book_key if quote else getattr(prop, "best_book_key", "")) or "",
    )


def _confidence_tier_to_num(tier: ConfidenceTier) -> float:
    """Convert confidence tier to numeric for scoring (0-1)."""
    return {
        ConfidenceTier.HIGH: 1.0,
        ConfidenceTier.MEDIUM: 0.67,
        ConfidenceTier.LOW: 0.33,
        ConfidenceTier.VERY_LOW: 0.10,
    }.get(tier, 0.5)


def _min_confidence_met(prop: PropProbability, min_conf: Optional[str]) -> bool:
    """Check if prop meets the minimum confidence threshold."""
    if not min_conf:
        return True
    tier_order = {"very_low": 0, "low": 1, "medium": 2, "high": 3}
    min_level = tier_order.get(min_conf.lower(), 0)
    prop_level = tier_order.get(prop.confidence.value, 0)
    return prop_level >= min_level


def leg_odds_match_constraints(odds: int, min_odds: int, max_odds: int) -> bool:
    """
    Return True if *odds* lies in the user's per-leg American odds band.

    Min/max are **order-independent** (swapping the two inputs is OK). Bounds are
    compared on the signed integer line, e.g. favorites -600 to -100 means
    ``-600 <= odds <= -100``; +200 does not match that band.
    """
    return _leg_odds_ok(odds, min_odds, max_odds)


def _leg_odds_ok(odds: int, min_odds: int, max_odds: int) -> bool:
    """Return True if *odds* is within the user's per-leg range."""
    if not is_valid_american(odds):
        return False
    lo, hi = min(min_odds, max_odds), max(min_odds, max_odds)
    return lo <= odds <= hi


def build_parlays(
    all_props: list[PropProbability],
    constraints: Optional[ParlayConstraints] = None,
) -> list[Parlay]:
    """
    Generate all valid parlays satisfying *constraints* from *all_props*.

    Steps:
    1. Filter props by per-leg constraints.
    2. Generate all combinations up to max_legs.
    3. Block correlated combos.
    4. Compute combined odds / edge.
    5. Filter by combined parlay odds range.
    6. Score and return.

    Args:
        all_props: All PropProbability objects from the slate scan.
        constraints: User constraints. Defaults to standard 5% edge / 3 legs.

    Returns:
        List of Parlay objects, unsorted (use ranking_engine to sort).
    """
    if constraints is None:
        constraints = ParlayConstraints()

    # Step 1: Filter qualifying legs
    qualifying: list[PropProbability] = []
    for prop in all_props:
        if prop.edge < constraints.min_edge:
            continue
        if not leg_odds_match_constraints(
            prop.sportsbook_odds, constraints.min_leg_odds, constraints.max_leg_odds
        ):
            continue
        if not _min_confidence_met(prop, constraints.min_confidence):
            continue
        if constraints.allowed_prop_types and prop.prop_type not in constraints.allowed_prop_types:
            continue
        qualifying.append(prop)

    logger.info("Qualifying legs for parlay: %d (from %d total)", len(qualifying), len(all_props))

    if len(qualifying) < constraints.min_legs:
        logger.warning("Not enough qualifying legs (%d) to build parlays", len(qualifying))
        return []

    # Step 2: Generate combinations
    parlays: list[Parlay] = []
    combo_count = 0

    for n_legs in range(constraints.min_legs, constraints.max_legs + 1):
        for combo in itertools.combinations(qualifying, n_legs):
            combo_count += 1
            legs = list(combo)

            # Step 3: Correlation check
            if is_blocked(legs):
                continue

            # A parlay cannot combine selected singles from different books.
            # Intersect raw provider keys rather than lossy BookName values.
            quotes_by_leg = [_latest_quotes_for_prop(prop) for prop in legs]
            common_books = set.intersection(*(set(quotes) for quotes in quotes_by_leg)) if quotes_by_leg else set()
            if not common_books:
                continue

            avg_corr = combo_avg_correlation(legs)
            for book_key in sorted(common_books):
                ticket_quotes = [quotes[book_key] for quotes in quotes_by_leg]
                ticket_legs = [_prop_to_leg(prop, quote) for prop, quote in zip(legs, ticket_quotes)]
                leg_odds = [leg.sportsbook_odds for leg in ticket_legs]
                leg_true_probs = [leg.true_probability for leg in ticket_legs]
                leg_implied_probs = [leg.implied_probability for leg in ticket_legs]

                combined_decimal = parlay_combined_decimal(leg_odds)
                combined_american = decimal_to_american(combined_decimal)
                combined_true = parlay_combined_true_probability_calibrated(
                    leg_true_probs,
                    legs=legs,
                    avg_pairwise_correlation=avg_corr,
                    combined_american_odds=combined_american,
                )
                combined_implied = parlay_combined_implied_probability(leg_implied_probs)
                combined_edge = parlay_combined_edge(combined_true, combined_implied)
                ticket_ev = parlay_expected_value(1.0, combined_decimal, combined_true)

                # Step 5: Filter by combined parlay odds and final expected
                # return. Per-leg edge does not guarantee a positive ticket.
                if not (constraints.min_parlay_odds <= combined_american <= constraints.max_parlay_odds):
                    continue
                if ticket_ev <= constraints.min_ticket_ev:
                    continue

                # Build Parlay entity. The provider does not return a priced
                # parlay ticket, so this same-book multiplication is explicit
                # theoretical pricing rather than a purchasable quote.
                corr_risk = correlation_risk_score(legs)
                div_bonus = diversification_bonus(legs)

                # Dominant confidence = worst among legs
                tier_order = [ConfidenceTier.HIGH, ConfidenceTier.MEDIUM, ConfidenceTier.LOW, ConfidenceTier.VERY_LOW]
                dominant_confidence = max(
                    [leg.confidence for leg in legs],
                    key=lambda t: tier_order.index(t),
                )

                # Balanced score (for ranking)
                avg_conf = sum(_confidence_tier_to_num(l.confidence) for l in legs) / len(legs)
                balanced = (
                    PARLAY_RANK_WEIGHT_EDGE * combined_edge
                    + PARLAY_RANK_WEIGHT_CONFIDENCE * avg_conf * 0.15  # scale to ~0-0.15
                    - PARLAY_RANK_WEIGHT_CORR_RISK * corr_risk * 0.10
                    + div_bonus * 0.05
                )

                parlay = Parlay(
                    parlay_id=str(uuid.uuid4())[:8],
                    legs=ticket_legs,
                    combined_american_odds=combined_american,
                    combined_decimal_odds=combined_decimal,
                    combined_implied_probability=combined_implied,
                    combined_true_probability=combined_true,
                    combined_edge=combined_edge,
                    expected_return=ticket_ev,
                    ticket_book=ticket_quotes[0].book,
                    ticket_book_key=book_key,
                    is_theoretical_price=True,
                    confidence_tier=dominant_confidence,
                    correlation_risk_score=corr_risk,
                    diversification_bonus=div_bonus,
                    balanced_score=balanced,
                )
                parlays.append(parlay)

    logger.info(
        "Generated %d parlays from %d combinations (%d qualifying legs)",
        len(parlays),
        combo_count,
        len(qualifying),
    )

    # Ranking happens after all requested leg lengths are evaluated. This keeps
    # results invariant to input order and avoids silently excluding later,
    # higher-ranked combinations.
    return parlays
