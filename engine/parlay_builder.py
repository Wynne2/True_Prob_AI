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
from domain.entities import Parlay, ParlayLeg, PropProbability
from domain.enums import BookName, ConfidenceTier, PropType
from engine.correlation_engine import (
    correlation_risk_score,
    diversification_bonus,
    is_blocked,
)
from odds.normalizer import american_to_decimal, decimal_to_american, is_valid_american
from odds.parlay_math import (
    parlay_combined_american,
    parlay_combined_decimal,
    parlay_combined_edge,
    parlay_combined_implied_probability,
    parlay_combined_true_probability,
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


def _prop_to_leg(prop: PropProbability) -> ParlayLeg:
    """Convert a PropProbability to a ParlayLeg."""
    return ParlayLeg(
        player_id=prop.player_id,
        player_name=prop.player_name,
        team_abbr=prop.team_abbr,
        opponent_abbr=prop.opponent_abbr,
        game_id=prop.game_id,
        prop_type=prop.prop_type,
        line=prop.line,
        side=prop.side,
        sportsbook=prop.best_book,
        sportsbook_odds=prop.sportsbook_odds,
        projected_value=prop.projected_value,
        true_probability=prop.true_probability,
        implied_probability=prop.implied_probability,
        edge=prop.edge,
        fair_odds=prop.fair_odds,
        confidence=prop.confidence,
        explanation=prop.explanation,
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


def _leg_odds_ok(odds: int, min_odds: int, max_odds: int) -> bool:
    """Return True if *odds* is within the user's per-leg range."""
    if not is_valid_american(odds):
        return False
    if odds < 0:
        # Negative odds: the number must be between min and max
        # e.g. min=-200 means odds like -110 are OK but -250 is not
        return min_odds <= odds <= -100 or 100 <= odds <= max_odds
    return 100 <= odds <= max_odds


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
        if not _leg_odds_ok(prop.sportsbook_odds, constraints.min_leg_odds, constraints.max_leg_odds):
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

            # Step 4: Compute combined metrics
            leg_odds = [l.sportsbook_odds for l in legs]
            leg_true_probs = [l.true_probability for l in legs]
            leg_implied_probs = [l.implied_probability for l in legs]

            combined_decimal = parlay_combined_decimal(leg_odds)
            combined_american = decimal_to_american(combined_decimal)
            combined_true = parlay_combined_true_probability(leg_true_probs)
            combined_implied = parlay_combined_implied_probability(leg_implied_probs)
            combined_edge = parlay_combined_edge(combined_true, combined_implied)

            # Step 5: Filter by combined parlay odds
            if not (constraints.min_parlay_odds <= combined_american <= constraints.max_parlay_odds):
                if combined_american < constraints.min_parlay_odds or combined_american > constraints.max_parlay_odds:
                    continue

            # Build Parlay entity
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
                legs=[_prop_to_leg(p) for p in legs],
                combined_american_odds=combined_american,
                combined_decimal_odds=combined_decimal,
                combined_implied_probability=combined_implied,
                combined_true_probability=combined_true,
                combined_edge=combined_edge,
                confidence_tier=dominant_confidence,
                correlation_risk_score=corr_risk,
                diversification_bonus=div_bonus,
                balanced_score=balanced,
            )
            parlays.append(parlay)

            if len(parlays) >= constraints.max_results * 10:
                # Safety valve: stop early, we'll trim in ranking
                break

        if len(parlays) >= constraints.max_results * 10:
            break

    logger.info(
        "Generated %d parlays from %d combinations (%d qualifying legs)",
        len(parlays),
        combo_count,
        len(qualifying),
    )

    return parlays[:constraints.max_results * 10]
