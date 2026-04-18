"""
Prop Evaluator  –  core evaluation pipeline.

For each (player, prop_type, line, side) combination, the evaluator:
1. Retrieves the pre-built FeatureVector from the feature store (if available).
2. Hydrates the Player entity with enriched fields from the FeatureVector so
   existing stat models work without modification (backward compatible).
3. Runs the appropriate stat model to get a projection.
4. Converts the projection to a true probability using the correct distribution.
5. Retrieves the best available sportsbook odds (SOURCE: The Odds API).
6. Computes vig-removed implied probability.
7. Calculates edge.
8. Attaches confidence tier and explanation.

The FeatureVector is pre-built by the SlateScanner (STEP 5 in the pipeline)
and never fetched per-prop here.  This is the critical rule: no nba_api or
SportsDataIO calls happen inside this class.
"""

from __future__ import annotations

import logging
from typing import Optional

from domain.constants import (
    FPA_LEAGUE_AVG,
    LEAGUE_AVG_DEF_EFF,
    MAX_PROBABILITY_CEILING,
    MIN_PROBABILITY_FLOOR,
    PROBABILITY_SHRINKAGE_FACTOR,
)
from domain.entities import Game, OddsLine, Player, PropProbability, StatProjection, TeamDefense
from domain.enums import (
    BookName,
    ConfidenceTier,
    DistributionType,
    InjuryStatus,
    PropSide,
    PropType,
)
from domain.feature_vector import FeatureVector
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


def _normalize_name(name: str) -> str:
    """
    Normalize a player name for fuzzy cross-source matching.

    Handles the case where The Odds API uses slugs like 'nikola_jokic' while
    SportsDataIO / nba_api use 'Nikola Jokic'.  Both normalize to 'nikolajokic'.
    """
    import re
    return re.sub(r"[^a-z0-9]", "", name.lower())


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


def _hydrate_player_from_feature_vector(player: Player, fv: FeatureVector) -> Player:
    """
    Enrich a Player entity with FeatureVector fields.

    This allows existing stat models to benefit from the richer nba_api +
    SportsDataIO data without requiring model changes.  Fields from the
    FeatureVector override the Player defaults where non-zero.

    SOURCE: FeatureVector fields are sourced from nba_api (usage/tracking/splits)
            and SportsDataIO (season averages, injuries, lineups).
    """
    # Usage and tracking (SOURCE: nba_api primary)
    if fv.usage_rate:
        player.usage_rate = fv.usage_rate
    if fv.touches_per_game:
        player.touches = fv.touches_per_game
    if fv.time_of_possession:
        player.time_of_possession = fv.time_of_possession
    if fv.potential_assists:
        player.potential_assists = fv.potential_assists
    if fv.rebound_chances:
        player.rebound_chances = fv.rebound_chances

    # Splits (SOURCE: nba_api primary)
    if fv.recent_5_avg and fv.recent_5_avg != fv.season_avg:
        # Distribute recent form across stat arrays for the prop type
        _fill_recent_form_by_prop(player, fv)

    # Role (SOURCE: SportsDataIO primary)
    if fv.projected_minutes:
        player.minutes_per_game = fv.projected_minutes

    return player


def _fill_recent_form_by_prop(player: Player, fv: FeatureVector) -> None:
    """Populate the correct last5/last10 list on the Player from the FeatureVector."""
    pt = fv.prop_type
    val5 = [fv.recent_5_avg] if fv.recent_5_avg else []
    val10 = [fv.recent_10_avg] if fv.recent_10_avg else []

    if pt == "points":
        if val5:
            player.last5_points = val5
        if val10:
            player.last10_points = val10
    elif pt == "rebounds":
        if val5:
            player.last5_rebounds = val5
        if val10:
            player.last10_rebounds = val10
    elif pt == "assists":
        if val5:
            player.last5_assists = val5
        if val10:
            player.last10_assists = val10
    elif pt == "threes":
        if val5:
            player.last5_threes = val5
    elif pt == "pra":
        # PRA: distribute evenly as a composite
        pra5 = [fv.recent_5_avg]
        pra10 = [fv.recent_10_avg]
        if pra5:
            player.last5_points = pra5
        if pra10:
            player.last10_points = pra10


class PropEvaluator:
    """
    Evaluates a single player prop against all available sportsbook lines.

    Feature vectors are consumed from the pre-built store (populated by
    SlateScanner before the evaluation loop starts).
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
        feature_vector: Optional[FeatureVector] = None,
    ) -> list[PropProbability]:
        """
        Evaluate all available lines for *player* / *prop_type*.

        If *feature_vector* is provided, the Player entity is enriched with
        nba_api advanced context before the stat model runs.

        Returns one PropProbability per (line, side) with best book selected.

        SOURCE (data consumed here):
          - Stat model inputs:   Player entity (enriched from FeatureVector)
          - FeatureVector:       nba_api + SportsDataIO (pre-built by SlateScanner)
          - Odds:                The Odds API (via all_odds list)
          - DvP factors:         Derived internally (read from FeatureVector)
        """
        if player.injury_status == InjuryStatus.OUT:
            return []

        model = _MODELS.get(prop_type)
        if model is None:
            logger.warning("No model for prop type: %s", prop_type)
            return []

        # Enrich player with feature vector data before passing to model
        if feature_vector is not None:
            player = _hydrate_player_from_feature_vector(player, feature_vector)

        # Run projection once per prop type
        try:
            projection: StatProjection = model.project(player, game, defense, is_home)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Projection failed for %s %s: %s", player.name, prop_type, exc)
            return []

        if projection.projected_value <= 0 and prop_type not in (PropType.BLOCKS, PropType.STEALS):
            return []

        # Find all lines for this player + prop from the odds feed.
        # SOURCE: The Odds API (via all_odds list)
        # Primary match: exact player_id (works when both sources share the same ID format).
        # Fallback: normalized name match (needed when Odds API uses name-slugs like
        # "nikola_jokic" while SportsDataIO uses integer IDs).
        player_lines = [
            ol for ol in all_odds
            if ol.player_id == player.player_id and ol.prop_type == prop_type
        ]
        if not player_lines:
            norm_name = _normalize_name(player.name)
            player_lines = [
                ol for ol in all_odds
                if _normalize_name(ol.player_name) == norm_name and ol.prop_type == prop_type
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

            # --- 3-step probability calibration pipeline ---
            # Step 1: raw tail probability from the chosen distribution
            raw_prob = clamp(_true_prob(projection, numeric_line, side), 0.001, 0.999)

            # Step 2: shrinkage toward 50% — accounts for inherent single-game
            # unpredictability that distributions cannot fully capture.
            # Example: raw=99.9% → shrunk=89.9%  |  raw=80% → shrunk=74%
            shrunk_prob = 0.5 + (raw_prob - 0.5) * PROBABILITY_SHRINKAGE_FACTOR

            # Step 3: data-completeness penalty — blend toward 50% when feature
            # inputs are sparse / unreliable (fv.data_completeness from builder).
            completeness = clamp(
                getattr(feature_vector, "data_completeness", 1.0) if feature_vector else 1.0,
                0.20, 1.0,
            )
            calibrated_prob = shrunk_prob * completeness + 0.5 * (1.0 - completeness)

            # Step 4: hard ceiling / floor — no single-game prop should ever
            # reach near certainty without extraordinary evidence.
            true_prob = clamp(calibrated_prob, MIN_PROBABILITY_FLOOR, MAX_PROBABILITY_CEILING)

            # Vig-removed implied probability (SOURCE: The Odds API pricing)
            ref_line_odds = [ol for ol in player_lines if ol.line == numeric_line]
            if not ref_line_odds:
                continue

            ref = ref_line_odds[0]
            implied = implied_prob_for_side(best.side, ref.over_odds, ref.under_odds)

            edge = calculate_edge(true_prob, implied)
            fair_odds_american = true_prob_to_american_odds(true_prob)

            consistency = self._variance_model.consistency_score(
                player, prop_type, projection.projected_value
            )
            confidence, _ = self._confidence_model.score(
                player, prop_type, projection.projected_value,
                consistency, edge, has_defense_data=defense is not None,
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
        feature_store: Optional[dict] = None,
    ) -> list[PropProbability]:
        """
        Evaluate every supported prop type for *player*.

        *feature_store* is a dict keyed by (player_id, prop_type_value)
        mapping to pre-built FeatureVectors.
        """
        targets = prop_types or list(PropType)
        results = []
        for pt in targets:
            fv = None
            if feature_store is not None:
                fv = feature_store.get((player.player_id, pt.value))
            results.extend(
                self.evaluate(player, game, defense, all_odds, pt, is_home, fv)
            )
        return results
