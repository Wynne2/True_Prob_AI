"""
Candidate selection for the AI Advisor.

Filters the evaluated prop universe in a fixed, auditable order and keeps
per-stage counts so an empty result can be explained:

1. edge strictly greater than ``min_edge`` (default 0.0 → positive edge only)
2. American odds inside the band (inclusive; default -600 … -200)
3. optional minimum confidence tier
4. the model itself backs the edge (engine/model_agreement.py; on by default)
5. dedupe to one row per (player, prop type, side), keeping the best-priced line
6. sort by edge ↓, confidence ↓, true probability ↓ (stable tie-breaks)
7. take the top N
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Optional

from domain.entities import PropProbability
from domain.enums import ConfidenceTier
from engine.model_agreement import model_agrees
from engine.parlay_builder import leg_odds_match_constraints
from odds.normalizer import american_to_decimal

logger = logging.getLogger(__name__)

_CONFIDENCE_RANK: dict[str, int] = {"very_low": 0, "low": 1, "medium": 2, "high": 3}


@dataclass
class CandidateStats:
    """How many props survived each filter stage."""
    total_props: int = 0
    positive_edge: int = 0
    in_odds_band: int = 0
    confidence_ok: int = 0
    model_agrees: int = 0
    deduped: int = 0
    selected: int = 0
    pool: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def confidence_rank(tier: ConfidenceTier | str | None) -> int:
    """Numeric rank for a confidence tier (higher = more confident)."""
    value = tier.value if isinstance(tier, ConfidenceTier) else (tier or "")
    return _CONFIDENCE_RANK.get(str(value).lower(), 0)


def _player_key(prop: PropProbability) -> str:
    return str(prop.player_id or prop.player_name).strip().lower()


def dedupe_key(prop: PropProbability) -> tuple[str, str, str]:
    """One recommendation per player / prop type / side."""
    return (_player_key(prop), prop.prop_type.value, prop.side.value)


def _price_key(prop: PropProbability) -> tuple:
    # Best price = highest decimal odds (largest payout); ties → higher edge.
    return (american_to_decimal(prop.sportsbook_odds), prop.edge, prop.true_probability)


def sort_key(prop: PropProbability) -> tuple:
    """Deterministic ranking: edge ↓, confidence ↓, true probability ↓, then identity."""
    return (
        -round(prop.edge, 10),
        -confidence_rank(prop.confidence),
        -round(prop.true_probability, 10),
        prop.player_name,
        prop.prop_type.value,
        prop.line,
        prop.side.value,
    )


def select_candidates(
    props: list[PropProbability],
    *,
    min_odds: int = -600,
    max_odds: int = -200,
    min_edge: float = 0.0,
    min_confidence: Optional[str] = None,
    top_n: int = 5,
    require_model_agreement: bool = True,
) -> tuple[list[PropProbability], list[PropProbability], CandidateStats]:
    """
    Return ``(top_n_candidates, all_ranked_candidates, stats)``.

    ``all_ranked_candidates`` is the full deduped + sorted list (useful for
    reporting how many qualified beyond the top N).
    """
    stats = CandidateStats(total_props=len(props))

    stage = [p for p in props if p.edge > min_edge]
    stats.positive_edge = len(stage)

    stage = [p for p in stage if leg_odds_match_constraints(p.sportsbook_odds, min_odds, max_odds)]
    stats.in_odds_band = len(stage)

    if min_confidence:
        floor = confidence_rank(min_confidence)
        stage = [p for p in stage if confidence_rank(p.confidence) >= floor]
    stats.confidence_ok = len(stage)

    if require_model_agreement:
        stage = [p for p in stage if model_agrees(p)]
    stats.model_agrees = len(stage)

    best: dict[tuple[str, str, str], PropProbability] = {}
    for prop in stage:
        key = dedupe_key(prop)
        current = best.get(key)
        if current is None or _price_key(prop) > _price_key(current):
            best[key] = prop
    ranked = sorted(best.values(), key=sort_key)
    stats.deduped = len(ranked)

    top = ranked[: max(0, top_n)]
    stats.selected = len(top)

    logger.info(
        "Advisor candidates: total=%d +edge=%d band=%d conf=%d agrees=%d deduped=%d selected=%d",
        stats.total_props, stats.positive_edge, stats.in_odds_band,
        stats.confidence_ok, stats.model_agrees, stats.deduped, stats.selected,
    )
    return top, ranked, stats
