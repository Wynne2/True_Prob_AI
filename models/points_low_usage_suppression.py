"""
Post-projection layer for NBA points: low-usage, low-line props.

Applied in PropEvaluator after PointsModel output (raw mean unchanged on
StatProjection). Adjusts effective mean and optional over-probability caps
for fragile role scorers only; primary buckets are untouched.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from domain.constants import (
    POINTS_BUCKET_PRIMARY_ALT_FGA,
    POINTS_BUCKET_PRIMARY_ALT_USAGE,
    POINTS_BUCKET_PRIMARY_MPG,
    POINTS_BUCKET_PRIMARY_MPG_USAGE,
    POINTS_BUCKET_PRIMARY_USAGE,
    POINTS_OVER_CAP_GAP_SECONDARY,
    POINTS_OVER_CAP_GAP_VOLATILE,
    POINTS_OVER_CAP_MAX_P_SECONDARY_NEAR,
    POINTS_OVER_CAP_MAX_P_VOLATILE_NEAR,
    POINTS_SUPPRESSION_BENCH_MULT,
    POINTS_SUPPRESSION_FGA_FLOOR_MAX_EXTRA,
    POINTS_SUPPRESSION_FGA_FLOOR_SLOPE,
    POINTS_SUPPRESSION_FGA_MAX,
    POINTS_SUPPRESSION_LINE_MAX,
    POINTS_SUPPRESSION_MEAN_MULT_SECONDARY,
    POINTS_SUPPRESSION_MEAN_MULT_VOLATILE,
    POINTS_SUPPRESSION_MINUTES_CV_THRESHOLD,
    POINTS_SUPPRESSION_PLAYOFF_MULT_SECONDARY,
    POINTS_SUPPRESSION_PLAYOFF_MULT_VOLATILE,
    POINTS_SUPPRESSION_QUESTIONABLE_MULT,
    POINTS_SUPPRESSION_USAGE_MAX,
    POINTS_VOLATILE_3PA_SHARE_OF_FGA,
    POINTS_VOLATILE_FTA_PER_FGA_MAX,
    POINTS_VOLATILE_MIN_SIGNALS,
    POINTS_VOLATILE_POINTS_CV,
    POINTS_VOLATILE_SIGNAL_FGA,
    POINTS_VOLATILE_SIGNAL_USAGE,
)
from domain.entities import Game, Player, StatProjection
from domain.enums import InjuryStatus, PlayerRole, PropSide


class PointsScorerBucket(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    LOW_USAGE_VOLATILE = "low_usage_volatile"


def _norm_usage(player: Player) -> float:
    u = float(getattr(player, "usage_rate", 0.0) or 0.0)
    if u > 1.0:
        u /= 100.0
    return u


def suppression_triggers(line: float, usage: float, proj_fga: float) -> bool:
    """All three must hold (per product spec)."""
    return (
        line <= POINTS_SUPPRESSION_LINE_MAX
        and usage < POINTS_SUPPRESSION_USAGE_MAX
        and proj_fga < POINTS_SUPPRESSION_FGA_MAX
    )


def _recent_points_cv(player: Player) -> Optional[float]:
    pts = [float(x) for x in (player.last5_points or []) if x is not None]
    if len(pts) < 4:
        return None
    m = statistics.mean(pts)
    if m < 0.25:
        return None
    return statistics.stdev(pts) / m


def _volatile_signal_count(player: Player, projection: StatProjection) -> int:
    """Signals toward low_usage_volatile bucket (need >= POINTS_VOLATILE_MIN_SIGNALS)."""
    n = 0
    usg = _norm_usage(player)
    if usg < POINTS_VOLATILE_SIGNAL_USAGE:
        n += 1
    fga = float(projection.expected_field_goal_attempts_proxy or 0.0)
    if fga < POINTS_VOLATILE_SIGNAL_FGA:
        n += 1
    if fga > 0.5:
        tpa = float(projection.expected_three_point_attempts_proxy or player.three_point_attempts or 0.0)
        if tpa / fga >= POINTS_VOLATILE_3PA_SHARE_OF_FGA:
            n += 1
    if not player.is_starter or player.role in (PlayerRole.BENCH, PlayerRole.RESERVE):
        n += 1
    fta = float(player.free_throw_attempts or 0.0)
    fga_season = max(float(player.field_goal_attempts or 0.0), 0.5)
    if fta / fga_season < POINTS_VOLATILE_FTA_PER_FGA_MAX:
        n += 1
    cv = _recent_points_cv(player)
    if cv is not None and cv >= POINTS_VOLATILE_POINTS_CV:
        n += 1
    return n


def classify_scorer_bucket(
    player: Player,
    projection: StatProjection,
    game: Game,
) -> tuple[PointsScorerBucket, list[str]]:
    """
    Primary: high usage / proven volume / star minutes heuristic.
    Low_usage_volatile: two+ fragility signals.
    Secondary: remaining non-primary.
    """
    reasons: list[str] = []
    usg = _norm_usage(player)
    fga = float(projection.expected_field_goal_attempts_proxy or 0.0)
    mpg = max(float(player.minutes_per_game or 0.0), 0.0)

    if (
        usg >= POINTS_BUCKET_PRIMARY_USAGE
        or (usg >= POINTS_BUCKET_PRIMARY_ALT_USAGE and fga >= POINTS_BUCKET_PRIMARY_ALT_FGA)
        or (mpg >= POINTS_BUCKET_PRIMARY_MPG and usg >= POINTS_BUCKET_PRIMARY_MPG_USAGE)
    ):
        reasons.append("primary_usage_or_volume")
        return PointsScorerBucket.PRIMARY, reasons

    if _volatile_signal_count(player, projection) >= POINTS_VOLATILE_MIN_SIGNALS:
        reasons.append("volatile_role_two_plus_signals")
        return PointsScorerBucket.LOW_USAGE_VOLATILE, reasons

    reasons.append("secondary_role")
    return PointsScorerBucket.SECONDARY, reasons


def floor_risk_fga_multiplier(proj_fga: float) -> float:
    """
    Extra multiplicative haircut when FGA proxy is low (shot floor risk).
    Returns factor in (0, 1], 1.0 = no extra penalty beyond trigger band.
    """
    if proj_fga >= POINTS_SUPPRESSION_FGA_MAX:
        return 1.0
    shortfall = POINTS_SUPPRESSION_FGA_MAX - proj_fga
    extra = min(POINTS_SUPPRESSION_FGA_FLOOR_MAX_EXTRA, shortfall * POINTS_SUPPRESSION_FGA_FLOOR_SLOPE)
    return max(0.88, 1.0 - extra)


def playoff_role_stability_multiplier(
    player: Player,
    game: Game,
    bucket: PointsScorerBucket,
) -> float:
    """Playoff-specific fragility for non-primary buckets."""
    if not getattr(game, "is_playoff", False):
        return 1.0
    if bucket == PointsScorerBucket.PRIMARY:
        return 1.0
    if bucket == PointsScorerBucket.LOW_USAGE_VOLATILE:
        return POINTS_SUPPRESSION_PLAYOFF_MULT_VOLATILE
    return POINTS_SUPPRESSION_PLAYOFF_MULT_SECONDARY


def role_stability_multiplier(player: Player, projection: StatProjection) -> float:
    """Bench / minute volatility / questionable — minutes alone ≠ scoring stability."""
    m = 1.0
    if not player.is_starter or player.role in (PlayerRole.BENCH, PlayerRole.RESERVE):
        m *= POINTS_SUPPRESSION_BENCH_MULT
    last5m = [float(x) for x in (player.last5_minutes or []) if x is not None]
    mpg = max(float(player.minutes_per_game or 0.0), 1e-6)
    if len(last5m) >= 4:
        cv_m = statistics.stdev(last5m) / mpg
        if cv_m >= POINTS_SUPPRESSION_MINUTES_CV_THRESHOLD:
            m *= 0.988
    if player.injury_status == InjuryStatus.QUESTIONABLE:
        m *= POINTS_SUPPRESSION_QUESTIONABLE_MULT
    return m


def cap_over_probability(
    p_over_uncapped: float,
    adjusted_mean: float,
    line: float,
    bucket: PointsScorerBucket,
    side: PropSide,
    active: bool,
) -> tuple[float, Optional[float]]:
    """
    Cap fragile low-usage overs when adjusted mean is only modestly above the line.
    Returns (capped_p_over, uncapped_p_over); for non-OVER or inactive, uncapped only.
    """
    if not active or side != PropSide.OVER or bucket == PointsScorerBucket.PRIMARY:
        return p_over_uncapped, None
    gap = adjusted_mean - line
    unc = p_over_uncapped
    if bucket == PointsScorerBucket.LOW_USAGE_VOLATILE:
        if gap <= POINTS_OVER_CAP_GAP_VOLATILE:
            cap = POINTS_OVER_CAP_MAX_P_VOLATILE_NEAR
            return min(unc, cap), unc
    else:
        if gap <= POINTS_OVER_CAP_GAP_SECONDARY:
            cap = POINTS_OVER_CAP_MAX_P_SECONDARY_NEAR
            return min(unc, cap), unc
    return unc, unc


@dataclass
class PointsLowUsageSuppressionResult:
    active: bool
    bucket: PointsScorerBucket
    raw_mean: float
    adjusted_mean: float
    flags: list[str] = field(default_factory=list)
    """Max P(over) after tail calc, or None if no cap applied."""
    over_probability_ceiling: Optional[float] = None


def apply_low_usage_points_suppression(
    player: Player,
    game: Game,
    projection: StatProjection,
    line: float,
    bucket: PointsScorerBucket,
) -> PointsLowUsageSuppressionResult:
    """
    Reduce effective mean for qualifying low-line / low-usage / low-FGA props.
    Does not re-read season PPG — only scales the model's final mean.
    """
    raw_mean = max(0.0, float(projection.dist_mean))
    usg = _norm_usage(player)
    fga = float(projection.expected_field_goal_attempts_proxy or 0.0)
    flags: list[str] = []

    if bucket == PointsScorerBucket.PRIMARY:
        return PointsLowUsageSuppressionResult(
            active=False,
            bucket=bucket,
            raw_mean=raw_mean,
            adjusted_mean=raw_mean,
            flags=[],
            over_probability_ceiling=None,
        )

    if not suppression_triggers(line, usg, fga):
        return PointsLowUsageSuppressionResult(
            active=False,
            bucket=bucket,
            raw_mean=raw_mean,
            adjusted_mean=raw_mean,
            flags=[],
            over_probability_ceiling=None,
        )

    flags.append("points_low_usage_suppression_active")
    base_mult = (
        POINTS_SUPPRESSION_MEAN_MULT_VOLATILE
        if bucket == PointsScorerBucket.LOW_USAGE_VOLATILE
        else POINTS_SUPPRESSION_MEAN_MULT_SECONDARY
    )
    m = (
        base_mult
        * floor_risk_fga_multiplier(fga)
        * playoff_role_stability_multiplier(player, game, bucket)
        * role_stability_multiplier(player, projection)
    )
    adjusted = max(0.0, raw_mean * m)
    flags.append(f"mean_mult_total={m:.4f}")

    return PointsLowUsageSuppressionResult(
        active=True,
        bucket=bucket,
        raw_mean=raw_mean,
        adjusted_mean=adjusted,
        flags=flags,
        over_probability_ceiling=None,
    )
