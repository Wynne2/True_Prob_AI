"""
Prop Evaluator  –  core pipeline.

For each (player, prop_type, line, side) combination, the evaluator:
1. Runs the appropriate stat model to get a projection.
2. Converts the projection to a true probability using the correct distribution.
3. Retrieves the best available sportsbook odds.
4. Computes vig-removed implied probability.
5. Calculates edge.
6. Attaches confidence tier and explanation.

Returns a list of PropProbability objects ready for the parlay builder.
"""

from __future__ import annotations

import logging
from typing import Optional

from domain.constants import FPA_LEAGUE_AVG, LEAGUE_AVG_DEF_EFF
from domain.entities import Game, OddsLine, Player, PropProbability, StatProjection, TeamDefense
from domain.enums import (
    BookName,
    ConfidenceTier,
    DistributionType,
    InjuryStatus,
    PropSide,
    PropType,
)
from models.assists_model import AssistsModel
from models.blocks_model import BlocksModel
from models.confidence_model import ConfidenceModel
from models.points_model import PointsModel
from models.pra_model import PRAModel
from models.rebounds_model import ReboundsModel
from models.steals_model import StealsModel
from models.threes_model import ThreesModel
from models.turnovers_model import TurnoversModel
from models.variance_model import VarianceModel
from odds.fair_odds import calculate_edge, true_prob_to_american_odds
from odds.implied_probability import implied_prob_for_side
from odds.line_shopping import shop_lines
from odds.normalizer import american_to_decimal
from utils.distributions import (
    binomial_prob_over,
    binomial_prob_under,
    negbinom_prob_over,
    negbinom_prob_under,
    normal_prob_over,
    normal_prob_under,
    poisson_prob_over,
    poisson_prob_under,
)
from utils.math_helpers import clamp

logger = logging.getLogger(__name__)

# Model registry: PropType → model instance
_MODELS: dict[PropType, object] = {
    PropType.POINTS: PointsModel(),
    PropType.REBOUNDS: ReboundsModel(),
    PropType.ASSISTS: AssistsModel(),
    PropType.THREES: ThreesModel(),
    PropType.PRA: PRAModel(),
    PropType.BLOCKS: BlocksModel(),
    PropType.STEALS: StealsModel(),
    PropType.TURNOVERS: TurnoversModel(),
}


def _true_prob(projection: StatProjection, line: float, side: PropSide) -> float:
    """Convert a StatProjection to P(stat OP line) for the given side."""
    dist = projection.distribution_type
    mean = projection.dist_mean
    std = projection.dist_std
    lam = projection.dist_lambda or mean
    n = projection.dist_n or 1
    p_binom = projection.dist_p or 0.35

    if side == PropSide.OVER:
        if dist == DistributionType.NORMAL:
            return normal_prob_over(mean, std, line)
        elif dist == DistributionType.POISSON:
            return poisson_prob_over(lam, line)
        elif dist == DistributionType.NEGATIVE_BINOMIAL:
            return negbinom_prob_over(mean, line=line)
        elif dist == DistributionType.BINOMIAL:
            return binomial_prob_over(n, p_binom, line)
    else:
        if dist == DistributionType.NORMAL:
            return normal_prob_under(mean, std, line)
        elif dist == DistributionType.POISSON:
            return poisson_prob_under(lam, line)
        elif dist == DistributionType.NEGATIVE_BINOMIAL:
            return negbinom_prob_under(mean, line=line)
        elif dist == DistributionType.BINOMIAL:
            return binomial_prob_under(n, p_binom, line)
    return 0.5


class PropEvaluator:
    """
    Evaluates a single player prop against all available sportsbook lines.
    """

    def __init__(self) -> None:
        self._confidence_model = ConfidenceModel()
        self._variance_model = VarianceModel()

    def evaluate(
        self,
        player: Player,
        game: Game,
        defense: Optional[TeamDefense],
        all_odds: list[OddsLine],
        prop_type: PropType,
        is_home: bool = True,
    ) -> list[PropProbability]:
        """
        Evaluate all available lines for *player* / *prop_type*.

        Returns one PropProbability per (line, side, book) combination,
        but deduplicates to the best book per (line, side).
        """
        if player.injury_status == InjuryStatus.OUT:
            return []

        model = _MODELS.get(prop_type)
        if model is None:
            logger.warning("No model for prop type: %s", prop_type)
            return []

        # Run projection once per prop type
        try:
            projection: StatProjection = model.project(player, game, defense, is_home)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Projection failed for %s %s: %s", player.name, prop_type, exc)
            return []

        if projection.projected_value <= 0 and prop_type not in (PropType.BLOCKS, PropType.STEALS):
            return []

        # Find all lines for this player + prop from the odds feed
        player_lines = [
            ol for ol in all_odds
            if ol.player_id == player.player_id and ol.prop_type == prop_type
        ]
        if not player_lines:
            logger.debug("No odds found for %s %s", player.name, prop_type)
            return []

        # Shop for best line per (line_value, side)
        best_lines = shop_lines(player_lines)
        results: list[PropProbability] = []

        for best in best_lines:
            numeric_line = best.line
            side = PropSide(best.side)

            # True probability from distribution
            true_prob = clamp(_true_prob(projection, numeric_line, side), 0.001, 0.999)

            # Implied probability (vig-removed from the best-book odds)
            over_odds = best.best_odds if best.side == "over" else min(best.all_books.values())
            under_odds = best.best_odds if best.side == "under" else min(best.all_books.values())

            # Get both sides for vig removal
            all_over = {b: o for b, o in best.all_books.items()} if best.side == "over" else {}
            all_under = {b: o for b, o in best.all_books.items()} if best.side == "under" else {}

            # We need both over and under odds for vig removal; look them up
            ref_line_odds = [ol for ol in player_lines if ol.line == numeric_line]
            if not ref_line_odds:
                continue

            ref = ref_line_odds[0]
            implied = implied_prob_for_side(best.side, ref.over_odds, ref.under_odds)

            edge = calculate_edge(true_prob, implied)
            fair_odds_american = true_prob_to_american_odds(true_prob)

            # Confidence
            consistency = self._variance_model.consistency_score(player, prop_type, projection.projected_value)
            confidence, _ = self._confidence_model.score(
                player, prop_type, projection.projected_value,
                consistency, edge, has_defense_data=defense is not None
            )

            from engine.explanation_engine import build_explanation
            explanation = build_explanation(player, prop_type, side, projection, defense)

            results.append(PropProbability(
                player_id=player.player_id,
                player_name=player.name,
                team_abbr=player.team_abbr,
                opponent_abbr=best.opponent_abbr or game.away_team_abbr,
                game_id=game.game_id,
                prop_type=prop_type,
                line=numeric_line,
                side=side,
                projected_value=projection.projected_value,
                true_probability=true_prob,
                implied_probability=implied,
                edge=edge,
                fair_odds=fair_odds_american,
                sportsbook_odds=best.best_odds,
                best_book=best.best_book,
                confidence=confidence,
                distribution_type=projection.distribution_type,
                explanation=explanation,
                all_lines=player_lines,
            ))

        return results

    def evaluate_all_props(
        self,
        player: Player,
        game: Game,
        defense: Optional[TeamDefense],
        all_odds: list[OddsLine],
        is_home: bool = True,
        prop_types: Optional[list[PropType]] = None,
    ) -> list[PropProbability]:
        """Evaluate every supported prop type for *player*."""
        targets = prop_types or list(PropType)
        results = []
        for pt in targets:
            results.extend(self.evaluate(player, game, defense, all_odds, pt, is_home))
        return results
