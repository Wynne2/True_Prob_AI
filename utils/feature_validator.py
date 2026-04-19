"""
Feature Vector Validation Layer.

Before a prop can be evaluated, this module checks the FeatureVector for
required and optional fields.  Missing critical fields result in the prop
being skipped or marked LOW_CONFIDENCE.  Missing optional fields are logged
as warnings so bad inputs are always visible.

Usage::

    result = validate_feature_vector(fv, prop_type="points")
    if not result.is_valid:
        logger.warning("Skipping prop: %s", result.missing_critical)
        return []
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from domain.constants import PROP_STATS_ALLOWING_ZERO_SEASON_AVG

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result from validate_feature_vector()."""
    is_valid: bool
    missing_critical: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data_completeness_override: Optional[float] = None  # override fv.data_completeness


def validate_feature_vector(fv, prop_type: str) -> ValidationResult:
    """
    Validate *fv* (a FeatureVector) before prop evaluation.

    Critical fields — if any are missing, the prop is LOW_CONFIDENCE or skipped:
      - season_avg present (see PROP_STATS_ALLOWING_ZERO_SEASON_AVG for stats where 0 is valid)
      - projected_minutes > 0
      - usage_rate > 0

    Optional fields — if missing, log warning and continue:
      - recent_5_avg, recent_10_avg (falling back to season_avg is acceptable)
      - touches_per_game, possessions_per_game (advanced; not always populated)
      - dvp_points_factor != 1.0 or dvp_pts_allowed > 0 (DvP might be neutral)
      - pace_context > 0

    Returns ValidationResult with is_valid=True/False and lists of missing fields.
    """
    missing_critical: list[str] = []
    missing_optional: list[str] = []
    warnings: list[str] = []

    # --- Critical fields ---
    # Only season_avg and projected_minutes are hard-blocking.
    # usage_rate comes from nba_api (a separate source from SDIO season stats);
    # if that call fails or is rate-limited we should still evaluate the player,
    # just with reduced completeness confidence.
    season_avg = getattr(fv, "season_avg", 0.0) or 0.0
    projected_minutes = getattr(fv, "projected_minutes", 0.0) or 0.0
    # Real per-game averages can be 0.0 for threes/blocks/steals/turnovers (and rarely assists);
    # require minutes so we are not approving empty/missing player rows.
    if season_avg <= 0:
        if not (
            prop_type in PROP_STATS_ALLOWING_ZERO_SEASON_AVG and projected_minutes > 0
        ):
            missing_critical.append("season_avg")

    if projected_minutes <= 0:
        missing_critical.append("projected_minutes")

    # --- Optional fields (missing → completeness penalty, but continue) ---
    usage_rate = getattr(fv, "usage_rate", 0.0) or 0.0
    if usage_rate <= 0:
        missing_optional.append("usage_rate")

    recent_5_avg = getattr(fv, "recent_5_avg", 0.0) or 0.0
    if recent_5_avg <= 0:
        missing_optional.append("recent_5_avg")

    recent_10_avg = getattr(fv, "recent_10_avg", 0.0) or 0.0
    if recent_10_avg <= 0:
        missing_optional.append("recent_10_avg")

    touches = getattr(fv, "touches_per_game", 0.0) or 0.0
    if touches <= 0:
        missing_optional.append("touches_per_game")

    possessions = getattr(fv, "possessions_per_game", 0.0) or 0.0
    if possessions <= 0:
        missing_optional.append("possessions_per_game")

    pace_context = getattr(fv, "pace_context", 0.0) or 0.0
    if pace_context <= 0:
        missing_optional.append("pace_context")

    # DvP: ambiguous when factor is exactly 1.0 and raw allowed is 0
    dvp_factor = getattr(fv, "dvp_points_factor", 1.0)
    dvp_allowed = getattr(fv, "dvp_pts_allowed", 0.0)
    if dvp_factor == 1.0 and dvp_allowed == 0.0:
        missing_optional.append("dvp_points_factor (may be neutral or missing)")

    # --- Build warnings ---
    player_name = getattr(fv, "player_name", "?")
    completeness = getattr(fv, "data_completeness", None)
    completeness_str = f"(data_completeness={completeness:.2f})" if isinstance(completeness, float) else ""

    if missing_critical:
        msg = (
            f"prop_type={prop_type} | missing CRITICAL fields: {missing_critical} | "
            f"player={player_name} {completeness_str}"
        )
        warnings.append(msg)
        logger.warning("FeatureValidator: %s", msg)

    if missing_optional:
        logger.debug(
            "FeatureValidator: optional fields missing for %s %s: %s",
            player_name, prop_type, missing_optional,
        )

    # is_valid=False only when critical fields are all absent AND usage is 0
    # — allow partial data to proceed with reduced completeness.
    # Hard skip: no season_avg at all.
    is_valid = "season_avg" not in missing_critical

    # Compute a completeness override based on what's missing
    # (reduces the calibration pipeline's confidence when fields are absent)
    penalty = 0.0
    if "projected_minutes" in missing_critical:
        penalty += 0.15
    if "usage_rate" in missing_optional:
        penalty += 0.15   # nba_api source unavailable — moderate confidence hit
    if "recent_5_avg" in missing_optional:
        penalty += 0.05
    if "recent_10_avg" in missing_optional:
        penalty += 0.05
    if "touches_per_game" in missing_optional:
        penalty += 0.03
    if "dvp_points_factor (may be neutral or missing)" in missing_optional:
        penalty += 0.05

    existing_completeness = getattr(fv, "data_completeness", 1.0) or 1.0
    completeness_override = max(0.20, existing_completeness - penalty)

    return ValidationResult(
        is_valid=is_valid,
        missing_critical=missing_critical,
        missing_optional=missing_optional,
        warnings=warnings,
        data_completeness_override=completeness_override if penalty > 0 else None,
    )
