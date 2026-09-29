"""
Prop Evaluator  –  core evaluation pipeline.

For each (player, prop_type, line, side) combination, the evaluator:
1. Receives a provider-normalized Player built from completed historical games.
2. Optionally hydrates an immutable replay FeatureVector without changing the
   source Player or replacing season MPG with game-specific minutes.
3. Runs the appropriate stat model to get a projection.
4. Converts the projection to a true probability using the correct distribution.
5. Selects the best provider-normalized sportsbook quote.
6. Computes raw (vig-included) implied probability for the priced side.
7. Calculates edge (true probability minus that raw implied).
8. Attaches confidence tier and explanation.

The evaluator never performs provider I/O. The scanner/provider boundary owns
all BALLDONTLIE access and the prediction ledger preserves the exact inputs.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Optional

from domain.constants import (
    AUDIT_FLAG_SHRINK_MAX_STEPS,
    AUDIT_FLAG_TRUE_PROB_SHRINK_STEP,
    FAVORITE_STRAIGHT_BET_AUDIT_BAND_HIGH,
    FAVORITE_STRAIGHT_BET_AUDIT_BAND_LOW,
    FPA_LEAGUE_AVG,
    LEAGUE_AVG_DEF_EFF,
    LOW_LINE_THRESHOLD,
    MAX_PROBABILITY_CEILING,
    MIN_PROBABILITY_FLOOR,
    NEGBIN_VARIANCE_INFLATION,
    PROBABILITY_SHRINKAGE_FACTOR,
    REBOUNDS_OVER_AMERICAN_LONGSHOT,
    REBOUNDS_OVER_LINE_STRESS,
    REBOUNDS_OVER_PROB_SHRINK_LONGSHOT,
    REBOUNDS_OVER_PROB_SHRINK_MINUTES,
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
from engine.final_calibration_gate import apply_final_calibration_gate
from engine.market_calibration import calibrate_true_probability
from models.points_low_usage_suppression import (
    apply_low_usage_points_suppression,
    cap_over_probability,
    classify_scorer_bucket,
    PointsLowUsageSuppressionResult,
)
from models.variance_model import VarianceModel
from odds.fair_odds import calculate_edge, true_prob_to_american_odds
from odds.implied_probability import raw_implied_prob_for_side
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


def _sync_season_stat_from_feature_vector(player: Player, fv: FeatureVector) -> None:
    """
    Align per-game season stats on Player with FeatureVector.season_avg.

    A replay feature snapshot can override a matching historical season value;
    models read Player.*_per_game, so the override remains evaluation-local.
    """
    sa = fv.season_avg
    if sa <= 0:
        return
    pt = (fv.prop_type or "").strip().lower()
    if pt == "points":
        player.points_per_game = sa
    elif pt == "rebounds":
        player.rebounds_per_game = sa
    elif pt == "assists":
        player.assists_per_game = sa
    elif pt == "threes":
        player.threes_per_game = sa
    elif pt == "blocks":
        player.blocks_per_game = sa
    elif pt == "steals":
        player.steals_per_game = sa
    elif pt == "turnovers":
        player.turnovers_per_game = sa
    # pra: composite — sub-models use roster pts/reb/ast; do not overwrite from sum here.


def _normalize_name(name: str) -> str:
    """
    Normalize a player name for fuzzy cross-source matching.

    Handles provider IDs that cannot be matched directly but have an exact
    normalized player name.
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


def _negbinom_inflation(projection: StatProjection) -> float:
    vi = float(getattr(projection, "negbinom_variance_inflation", 0) or 0)
    return vi if vi > 0 else NEGBIN_VARIANCE_INFLATION


def _rebound_over_adjust_raw_prob(
    raw_prob: float,
    projection: StatProjection,
    line: float,
    american_odds: int,
) -> float:
    """
    Pull rebound OVER tail probability toward 50% when minutes are insufficient
    vs implied rate, or when the line/price is a longshot profile.
    """
    ctx = getattr(projection, "model_context", None) or {}
    rpm = float(ctx.get("blended_rebounds_per_minute") or 0.0)
    if rpm <= 0:
        rpm = max(float(projection.season_rate_per_minute or 0.0), 0.06)
    exp_m = float(projection.expected_minutes or 0.0)
    req_min = line / max(rpm, 1e-6)
    p = raw_prob
    if exp_m > 1.0 and req_min > exp_m * REBOUNDS_OVER_PROB_SHRINK_MINUTES:
        p = 0.5 + (p - 0.5) * 0.82
    if (
        line >= REBOUNDS_OVER_LINE_STRESS
        and american_odds >= REBOUNDS_OVER_AMERICAN_LONGSHOT
        and exp_m > 1.0
        and req_min > exp_m * 0.93
    ):
        p = 0.5 + (p - 0.5) * REBOUNDS_OVER_PROB_SHRINK_LONGSHOT
    return clamp(p, 0.001, 0.999)


def _true_prob_with_mean(
    projection: StatProjection,
    line: float,
    side: PropSide,
    mean: float,
) -> float:
    """Like ``_true_prob`` but uses *mean* for the distribution location (points Normal tails)."""
    dist = projection.distribution_type
    std = projection.dist_std
    if side == PropSide.OVER:
        if dist == DistributionType.NORMAL:
            return normal_prob_over(mean, std, line)
    else:
        if dist == DistributionType.NORMAL:
            return normal_prob_under(mean, std, line)
    # Non-Normal: fall back to projection.dist_mean path
    saved = projection.dist_mean
    projection.dist_mean = mean
    try:
        return _true_prob(projection, line, side)
    finally:
        projection.dist_mean = saved


def _true_prob(projection: StatProjection, line: float, side: PropSide) -> float:
    """Convert a StatProjection to P(stat OP line) for the given side."""
    dist = projection.distribution_type
    mean = projection.dist_mean
    std = projection.dist_std
    lam = projection.dist_lambda or mean
    n = projection.dist_n or 1
    p_binom = projection.dist_p or 0.35
    vi = _negbinom_inflation(projection)

    if side == PropSide.OVER:
        if dist == DistributionType.NORMAL:
            return normal_prob_over(mean, std, line)
        elif dist == DistributionType.POISSON:
            return poisson_prob_over(lam, line)
        elif dist == DistributionType.NEGATIVE_BINOMIAL:
            return negbinom_prob_over(mean, variance_inflation=vi, line=line)
        elif dist == DistributionType.BINOMIAL:
            return binomial_prob_over(n, p_binom, line)
    else:
        if dist == DistributionType.NORMAL:
            return normal_prob_under(mean, std, line)
        elif dist == DistributionType.POISSON:
            return poisson_prob_under(lam, line)
        elif dist == DistributionType.NEGATIVE_BINOMIAL:
            return negbinom_prob_under(mean, variance_inflation=vi, line=line)
        elif dist == DistributionType.BINOMIAL:
            return binomial_prob_under(n, p_binom, line)
    return 0.5


def _hydrate_player_from_feature_vector(player: Player, fv: FeatureVector) -> Player:
    """
    Enrich a Player entity with FeatureVector fields.

    Replay FeatureVector fields override an evaluation-local Player clone where
    they are present. The initial live BDL pipeline currently passes Players
    directly and does not require this compatibility path.
    """
    player = deepcopy(player)
    _sync_season_stat_from_feature_vector(player, fv)

    # Optional replay tracking fields
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

    _apply_observations_to_player(player, fv)
    player.expected_minutes_override = fv.projected_minutes

    return player


def _apply_observations_to_player(player: Player, fv: FeatureVector) -> None:
    """Copy aligned evidence to an evaluation-local Player clone."""
    observations = tuple(getattr(fv, "recent_observations", ()) or ())
    if len(observations) < 3:
        return
    minutes = [observation.minutes for observation in observations]
    player.last10_minutes = minutes
    player.last5_minutes = minutes[:5]
    field = {
        "points": "points",
        "rebounds": "rebounds",
        "assists": "assists",
        "threes": "threes",
        "blocks": "blocks",
        "steals": "steals",
        "turnovers": "turnovers",
    }.get(fv.prop_type)
    if field:
        values = [float(getattr(observation, field)) for observation in observations]
        setattr(player, f"last10_{field}", values)
        setattr(player, f"last5_{field}", values[:5])


class PropEvaluator:
    """
    Evaluates a single player prop against all available sportsbook lines.

    Feature vectors are consumed from the pre-built store (populated by
    SlateScanner before the evaluation loop starts).
    """

    def __init__(self, debug_mode: bool = False) -> None:
        self._confidence_model = ConfidenceModel()
        self._variance_model = VarianceModel()
        self._debug_mode = debug_mode

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

        If *feature_vector* is provided, a replay Player clone is enriched
        before the stat model runs.

        Returns one PropProbability per (line, side) with best book selected.

        SOURCE (data consumed here):
          - Stat model inputs: Player entity built from BDL history
          - FeatureVector: immutable replay snapshot when supplied
          - Odds: normalized BALLDONTLIE quote observations
        """
        if player.injury_status in {
            InjuryStatus.UNKNOWN,
            InjuryStatus.QUESTIONABLE,
            InjuryStatus.DOUBTFUL,
            InjuryStatus.DAY_TO_DAY,
            InjuryStatus.OUT,
        }:
            return []

        model = _MODELS.get(prop_type)
        if model is None:
            logger.warning("No model for prop type: %s", prop_type)
            return []

        # --- Feature validation ---
        from utils.feature_validator import validate_feature_vector
        effective_completeness: Optional[float] = None
        if feature_vector is not None:
            validation = validate_feature_vector(feature_vector, prop_type.value)
            if not validation.is_valid:
                logger.warning(
                    "PropEvaluator: skipping %s %s — no season_avg data",
                    player.name, prop_type.value,
                )
                return []
            if validation.data_completeness_override is not None:
                effective_completeness = validation.data_completeness_override

        # Enrich player with feature vector data before passing to model
        if feature_vector is not None:
            player = _hydrate_player_from_feature_vector(player, feature_vector)

        # Run projection once per prop type
        try:
            projection: StatProjection = model.project(player, game, defense, is_home)  # type: ignore[attr-defined]
        except Exception as exc:
            raise RuntimeError(
                f"Projection failed for {player.name} {prop_type.value}"
            ) from exc

        if projection.projected_value <= 0 and prop_type not in (PropType.BLOCKS, PropType.STEALS):
            return []

        # Match the provider's player ID first; retain the legacy name match
        # only for replay records with different provider identifiers.
        player_lines = [
            ol for ol in all_odds
            if ol.game_id == game.game_id
            and ol.player_id == player.player_id
            and ol.prop_type == prop_type
        ]
        if not player_lines:
            norm_name = _normalize_name(player.name)
            player_lines = [
                ol for ol in all_odds
                if ol.game_id == game.game_id
                and _normalize_name(ol.player_name) == norm_name
                and ol.prop_type == prop_type
            ]
        if not player_lines:
            logger.debug("No odds found for %s %s", player.name, prop_type)
            return []

        # Shop for best line per (line_value, side)
        from config import get_settings
        settings = get_settings()
        min_books = 1 if settings.app_mode == "demo" else 2
        best_lines = shop_lines(
            player_lines,
            min_books=min_books,
            max_quote_age_seconds=settings.max_quote_age_seconds,
        )
        results: list[PropProbability] = []

        volatile_low_line = {PropType.THREES, PropType.BLOCKS, PropType.STEALS}

        for best in best_lines:
            numeric_line = best.line
            side = PropSide(best.side)
            if float(numeric_line).is_integer():
                logger.info(
                    "Skipping %s %s %.1f: whole-number lines require market-specific push settlement rules",
                    player.name,
                    prop_type.value,
                    numeric_line,
                )
                continue
            in_favorite_audit_band = (
                FAVORITE_STRAIGHT_BET_AUDIT_BAND_LOW
                <= best.best_odds
                <= FAVORITE_STRAIGHT_BET_AUDIT_BAND_HIGH
            )
            hf_log = in_favorite_audit_band
            hf: dict = {}

            points_sup: Optional[PointsLowUsageSuppressionResult] = None
            effective_mean = float(projection.dist_mean)
            if prop_type == PropType.POINTS:
                _bucket, _ = classify_scorer_bucket(player, projection, game)
                points_sup = apply_low_usage_points_suppression(
                    player, game, projection, numeric_line, _bucket,
                )
                effective_mean = float(points_sup.adjusted_mean)

            std_input_mean = (
                effective_mean
                if prop_type == PropType.POINTS and points_sup and points_sup.active
                else projection.projected_value
            )
            base_std = self._variance_model.std(
                player, prop_type, std_input_mean, prop_line=numeric_line
            )
            projection.dist_std = base_std
            if hf_log:
                hf["raw_dist_mean"] = round(float(projection.dist_mean), 4)
                hf["effective_mean"] = round(float(effective_mean), 4)
                hf["dist_std"] = round(float(base_std), 4)
                hf["prop_type"] = prop_type.value
                hf["side"] = side.value

            # --- 3-step probability calibration pipeline ---
            # Step 1: raw tail probability from the chosen distribution
            mean_for_tail = (
                effective_mean if prop_type == PropType.POINTS else projection.dist_mean
            )
            if prop_type == PropType.POINTS:
                raw_prob = clamp(
                    _true_prob_with_mean(projection, numeric_line, side, mean_for_tail),
                    0.001,
                    0.999,
                )
            else:
                raw_prob = clamp(_true_prob(projection, numeric_line, side), 0.001, 0.999)

            uncapped_over_for_cap: Optional[float] = None
            if (
                prop_type == PropType.POINTS
                and points_sup
                and points_sup.active
                and side == PropSide.OVER
            ):
                uncapped_over_for_cap = raw_prob
                raw_prob, _ceiling_hit = cap_over_probability(
                    raw_prob,
                    effective_mean,
                    numeric_line,
                    points_sup.bucket,
                    side,
                    points_sup.active,
                )

            tail_after_distribution_and_points_cap = float(raw_prob)
            if prop_type == PropType.REBOUNDS and side == PropSide.OVER:
                raw_prob = _rebound_over_adjust_raw_prob(
                    raw_prob, projection, numeric_line, best.best_odds,
                )
            tail_prob_pre_probability_shrink = float(raw_prob)
            if hf_log:
                hf["tail_after_step1"] = round(float(raw_prob), 5)

            # Step 2: shrinkage toward 50% — accounts for inherent single-game
            # unpredictability that distributions cannot fully capture.
            # Example: raw=99.9% → shrunk=89.9%  |  raw=80% → shrunk=74%
            shrunk_prob = 0.5 + (raw_prob - 0.5) * PROBABILITY_SHRINKAGE_FACTOR
            if hf_log:
                hf["shrunk"] = round(float(shrunk_prob), 5)

            # Step 3: data-completeness penalty — blend toward 50% when feature
            # inputs are sparse / unreliable (fv.data_completeness from builder,
            # potentially reduced further by the feature validator).
            base_completeness = (
                getattr(feature_vector, "data_completeness", 1.0)
                if feature_vector else 1.0
            )
            # Use validator override if it lowers completeness further
            if effective_completeness is not None:
                base_completeness = min(base_completeness, effective_completeness)
            completeness = clamp(base_completeness, 0.20, 1.0)
            calibrated_prob = shrunk_prob * completeness + 0.5 * (1.0 - completeness)
            if hf_log:
                hf["completeness"] = round(float(completeness), 4)
                hf["after_completeness"] = round(float(calibrated_prob), 5)

            # Step 4: hard ceiling / floor — no single-game prop should ever
            # reach near certainty without extraordinary evidence.
            true_prob = clamp(calibrated_prob, MIN_PROBABILITY_FLOOR, MAX_PROBABILITY_CEILING)
            if hf_log:
                hf["after_step4_clamp"] = round(float(true_prob), 5)

            audit_flags = list(getattr(projection, "projection_audit_flags", None) or [])
            if points_sup and points_sup.flags:
                audit_flags.extend(points_sup.flags)
            if audit_flags:
                n_audit = min(len(audit_flags), AUDIT_FLAG_SHRINK_MAX_STEPS)
                shrink = AUDIT_FLAG_TRUE_PROB_SHRINK_STEP ** n_audit
                true_prob = 0.5 + (true_prob - 0.5) * shrink
                true_prob = clamp(true_prob, MIN_PROBABILITY_FLOOR, MAX_PROBABILITY_CEILING)
            if hf_log:
                hf["after_audit_flags"] = round(float(true_prob), 5)
                hf["audit_flags"] = list(audit_flags)

            if numeric_line <= LOW_LINE_THRESHOLD and prop_type in volatile_low_line:
                true_prob = 0.5 + (true_prob - 0.5) * 0.90
            if hf_log:
                hf["after_volatile_low_line"] = round(float(true_prob), 5)

            # The line-shopping result carries its exact immutable selected
            # quote. Never reconstruct quote identity from a shared enum.
            ref = best.selected_quote
            quality_flags: list[str] = []
            if ref.source_updated_at is None:
                logger.info(
                    "Quote %s/%s has no provider update timestamp; using retrieval freshness only.",
                    ref.game_id,
                    ref.provider_quote_id or ref.book_key,
                )
                quality_flags.append("source_quote_time_unknown")
            if player.injury_status == InjuryStatus.UNREPORTED:
                quality_flags.append("availability_unreported")
            implied = raw_implied_prob_for_side(best.side, ref.over_odds, ref.under_odds)
            if hf_log:
                from odds.implied_probability import implied_prob_for_side as _fair_side_hf
                from odds.normalizer import american_to_raw_implied_prob as _raw_imp_hf

                raw_side_odds = ref.over_odds if side == PropSide.OVER else ref.under_odds
                hf["american_best"] = int(best.best_odds)
                hf["ref_over_amer"] = int(ref.over_odds)
                hf["ref_under_amer"] = int(ref.under_odds)
                hf["raw_implied_side"] = round(float(_raw_imp_hf(raw_side_odds)), 5)
                hf["fair_implied_side"] = round(
                    float(_fair_side_hf(best.side, ref.over_odds, ref.under_odds)), 5
                )
                hf["line"] = float(numeric_line)

            true_prob_pre_market = float(true_prob)
            true_prob, cal_warnings = calibrate_true_probability(
                true_prob, implied, best.best_odds,
            )
            market_calibration_warnings = list(cal_warnings)
            true_prob = clamp(true_prob, MIN_PROBABILITY_FLOOR, MAX_PROBABILITY_CEILING)
            true_prob_after_market_calibration = float(true_prob)
            if hf_log:
                hf["true_pre_market_cal"] = round(true_prob_pre_market, 5)
                hf["after_market_cal"] = round(float(true_prob), 5)
                hf["market_cal_warnings"] = list(market_calibration_warnings)

            cal_warnings = list(cal_warnings)
            cal_warnings.extend(audit_flags)

            edge = calculate_edge(true_prob, implied)
            if hf_log:
                hf["edge_pre_gate"] = round(float(edge), 5)

            consistency = self._variance_model.consistency_score(
                player, prop_type, projection.projected_value
            )
            confidence, _ = self._confidence_model.score(
                player, prop_type, projection.projected_value,
                consistency, edge, has_defense_data=defense is not None,
            )

            true_prob, confidence, gate_warnings = apply_final_calibration_gate(
                player,
                game,
                is_home,
                prop_type,
                side,
                numeric_line,
                projection,
                true_prob,
                implied,
                edge,
                confidence,
                completeness,
                best.best_odds,
            )
            true_prob = clamp(true_prob, MIN_PROBABILITY_FLOOR, MAX_PROBABILITY_CEILING)
            edge = calculate_edge(true_prob, implied)
            if hf_log:
                hf["after_final_gate"] = round(float(true_prob), 5)
                hf["edge_final"] = round(float(edge), 5)
                hf["gate_warnings"] = list(gate_warnings)
                hf["would_fail_default_min_edge_5pct"] = bool(edge < 0.05)
                hf["prob_drop_tail_to_pre_market"] = round(
                    true_prob_pre_market - float(hf.get("tail_after_step1", true_prob_pre_market)),
                    5,
                )
                hf["prob_drop_pre_market_to_final"] = round(
                    true_prob_pre_market - float(true_prob), 5,
                )
                if points_sup and points_sup.flags:
                    hf["points_suppression_flags"] = list(points_sup.flags)

            fair_odds_american = true_prob_to_american_odds(true_prob)
            cal_warnings = list(cal_warnings) + gate_warnings

            from engine.explanation_engine import build_explanation
            explanation = build_explanation(player, prop_type, side, projection, defense)
            if gate_warnings:
                explanation = (
                    f"{explanation} Conservative final check applied "
                    f"({len(gate_warnings)} signal(s): {', '.join(gate_warnings[:6])})."
                )
            if points_sup and points_sup.active and points_sup.flags:
                explanation = (
                    f"{explanation} Low-usage points layer: "
                    f"{', '.join(points_sup.flags[:5])}"
                    f"{'…' if len(points_sup.flags) > 5 else ''}."
                )

            # --- Points diagnostics (always for POINTS) + verbose debug ---
            import os
            from odds.implied_probability import get_fair_implied_probabilities
            from odds.normalizer import american_to_raw_implied_prob

            _verbose = os.environ.get("VERBOSE", "0") in ("1", "true", "yes")
            _debug = getattr(self, "_debug_mode", False) or _verbose

            fair_over_f, fair_under_f = get_fair_implied_probabilities(
                ref.over_odds, ref.under_odds
            )
            raw_imp_over = american_to_raw_implied_prob(ref.over_odds)
            raw_imp_under = american_to_raw_implied_prob(ref.under_odds)
            fair_side = fair_over_f if side == PropSide.OVER else fair_under_f
            raw_side = raw_imp_over if side == PropSide.OVER else raw_imp_under

            debug_payload: Optional[dict] = None
            if prop_type == PropType.POINTS and points_sup is not None:
                usg_n = float(player.usage_rate or 0.0)
                if usg_n > 1.0:
                    usg_n /= 100.0
                uncapped_over_diag = clamp(
                    _true_prob_with_mean(
                        projection, numeric_line, PropSide.OVER, effective_mean
                    ),
                    0.001,
                    0.999,
                )
                capped_over_diag: Optional[float] = None
                if (
                    points_sup.active
                    and side == PropSide.OVER
                    and uncapped_over_for_cap is not None
                ):
                    capped_over_diag, _ = cap_over_probability(
                        uncapped_over_for_cap,
                        effective_mean,
                        numeric_line,
                        points_sup.bucket,
                        PropSide.OVER,
                        True,
                    )
                debug_payload = {
                    "points_diagnostics": {
                        "raw_projected_points": round(points_sup.raw_mean, 3),
                        "adjusted_mean_points": round(effective_mean, 3),
                        "usage_rate": round(usg_n, 4),
                        "projected_fga": round(
                            float(projection.expected_field_goal_attempts_proxy or 0.0), 3
                        ),
                        "projected_playoff_minutes": round(float(projection.expected_minutes or 0.0), 2),
                        "scorer_bucket": points_sup.bucket.value,
                        "uncapped_over_probability": round(uncapped_over_diag, 4),
                        "capped_over_probability": (
                            round(capped_over_diag, 4) if capped_over_diag is not None else None
                        ),
                        "implied_raw_over": round(raw_imp_over, 4),
                        "implied_raw_under": round(raw_imp_under, 4),
                        "implied_fair_over": round(fair_over_f, 4),
                        "implied_fair_under": round(fair_under_f, 4),
                        "edge_vs_raw_implied": round(float(true_prob) - raw_side, 4),
                        "edge_vs_fair_implied": round(float(true_prob) - fair_side, 4),
                        "suppression_flags": list(points_sup.flags),
                    },
                }
            if _debug:
                # Compute both sides' raw probability for visibility (model mean)
                over_raw = clamp(_true_prob(projection, numeric_line, PropSide.OVER), 0.001, 0.999)
                under_raw = clamp(_true_prob(projection, numeric_line, PropSide.UNDER), 0.001, 0.999)
                verbose_block = {
                    "season_avg": getattr(feature_vector, "season_avg", player.points_per_game) if feature_vector else player.points_per_game,
                    "recent_10_avg": getattr(feature_vector, "recent_10_avg", 0.0) if feature_vector else 0.0,
                    "recent_5_avg": getattr(feature_vector, "recent_5_avg", 0.0) if feature_vector else 0.0,
                    "season_rate_per_minute": round(projection.season_rate_per_minute, 4),
                    "recent_rate_per_minute": round(projection.recent_rate_per_minute, 4),
                    "raw_minute_scaled_mean": round(projection.raw_minute_scaled_mean, 4),
                    "expected_minutes_model": round(projection.expected_minutes, 2),
                    "projected_minutes": player.minutes_per_game,
                    "usage_rate": player.usage_rate,
                    "minutes_factor": round(projection.minutes_factor, 4),
                    "usage_adjustment": round(projection.usage_factor, 4),
                    "injury_adjustment": round(projection.injury_factor, 4),
                    "pace_adjustment": round(projection.pace_factor, 4),
                    "matchup_adjustment": round(projection.matchup_factor, 4),
                    "dvp_adjustment": round(getattr(feature_vector, "dvp_points_factor", 1.0) if feature_vector else 1.0, 4),
                    "final_projection": round(projection.projected_value, 3),
                    "expected_fga_proxy": round(projection.expected_field_goal_attempts_proxy, 3),
                    "expected_3pa_proxy": round(projection.expected_three_point_attempts_proxy, 3),
                    "projection_audit_flags": list(projection.projection_audit_flags or []),
                    "negbinom_variance_inflation": round(_negbinom_inflation(projection), 4),
                    "rebound_model_context": dict(getattr(projection, "model_context", None) or {}),
                    "required_minutes_to_clear_line": (
                        round(
                            numeric_line
                            / max(
                                float(
                                    (getattr(projection, "model_context", None) or {}).get(
                                        "blended_rebounds_per_minute"
                                    )
                                    or projection.season_rate_per_minute
                                    or 0.08
                                ),
                                0.08,
                            ),
                            2,
                        )
                        if prop_type == PropType.REBOUNDS
                        else None
                    ),
                    "raw_over_prob": round(over_raw, 4),
                    "raw_under_prob": round(under_raw, 4),
                    "raw_prob": round(raw_prob, 4),
                    "shrunk_prob": round(shrunk_prob, 4),
                    "calibrated_prob": round(calibrated_prob, 4),
                    "over_probability": round(0.5 + (over_raw - 0.5) * PROBABILITY_SHRINKAGE_FACTOR * completeness, 4),
                    "under_probability": round(0.5 + (under_raw - 0.5) * PROBABILITY_SHRINKAGE_FACTOR * completeness, 4),
                    "over_implied": round(raw_imp_over, 4),
                    "under_implied": round(raw_imp_under, 4),
                    "data_completeness": round(completeness, 4),
                    "selected_side": side.value,
                    "reason_selected": (
                        f"edge={edge:.3f} (true_prob={true_prob:.3f} vs implied={implied:.3f})"
                    ),
                }
                if debug_payload is not None:
                    debug_payload = {**debug_payload, **verbose_block}
                else:
                    debug_payload = verbose_block

            bp = projection.baseline_projection or projection.projected_value
            adj_display = (
                effective_mean if prop_type == PropType.POINTS else projection.projected_value
            )

            favorite_band_audit = None
            if in_favorite_audit_band:
                uncapped_user = (
                    float(uncapped_over_for_cap)
                    if uncapped_over_for_cap is not None
                    else tail_after_distribution_and_points_cap
                )
                favorite_band_audit = {
                    "american_odds": int(best.best_odds),
                    "raw_implied_probability": round(float(implied), 6),
                    "fair_implied_probability": round(float(fair_side), 6),
                    "raw_projected_mean": round(float(projection.dist_mean), 4),
                    "adjusted_projected_mean": round(float(adj_display), 4),
                    "uncapped_true_probability": round(float(uncapped_user), 5),
                    "step1_tail_before_probability_shrink": round(
                        float(tail_prob_pre_probability_shrink), 5,
                    ),
                    "tail_after_distribution_and_points_cap": round(
                        float(tail_after_distribution_and_points_cap), 5,
                    ),
                    "probability_shrinkage_factor": float(PROBABILITY_SHRINKAGE_FACTOR),
                    "after_shrink_probability": round(float(shrunk_prob), 5),
                    "after_completeness_probability": round(float(calibrated_prob), 5),
                    "true_probability_before_market_calibration": round(
                        float(true_prob_pre_market), 5,
                    ),
                    "true_probability_after_market_calibration": round(
                        float(true_prob_after_market_calibration), 5,
                    ),
                    "final_true_probability": round(float(true_prob), 5),
                    "final_edge": round(float(edge), 5),
                    "confidence_tier": confidence.value,
                    "market_calibration_warnings": list(market_calibration_warnings),
                    "gate_warnings": list(gate_warnings),
                    "projection_audit_flags": list(projection.projection_audit_flags or []),
                    "points_suppression_flags": list(points_sup.flags) if points_sup else [],
                    "points_suppression_active": bool(points_sup and points_sup.active),
                }

            results.append(PropProbability(
                player_id=player.player_id,
                player_name=player.name,
                team_abbr=player.team_abbr,
                # Opponent: if player is on home team → away is opponent, and vice versa.
                opponent_abbr=best.opponent_abbr or (
                    game.away_team_abbr
                    if player.team_abbr == game.home_team_abbr
                    else game.home_team_abbr
                ),
                game_id=game.game_id,
                prop_type=prop_type,
                line=numeric_line,
                side=side,
                projected_value=projection.projected_value,
                baseline_projection=bp,
                adjusted_projection=adj_display,
                expected_minutes=projection.expected_minutes,
                calibration_warnings=cal_warnings,
                data_quality_flags=quality_flags,
                true_probability=true_prob,
                implied_probability=implied,
                edge=edge,
                fair_odds=fair_odds_american,
                sportsbook_odds=best.best_odds,
                best_book=best.best_book,
                best_book_key=best.best_book_key,
                selected_quote=ref,
                confidence=confidence,
                distribution_type=projection.distribution_type,
                explanation=explanation,
                all_lines=player_lines,
                debug_payload=debug_payload,
                favorite_band_audit=favorite_band_audit,
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
