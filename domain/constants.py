"""
Shared numeric constants and weight tables for the NBA Prop AI platform.

All tunable parameters live here so they can be adjusted in one place
without touching model logic.
"""

from domain.enums import Position, PropType

# ---------------------------------------------------------------------------
# Season context
# ---------------------------------------------------------------------------

NBA_SEASON: str = "2025-26"             # nba_api season string (YYYY-YY format)
SDIO_SEASON: str = "2026"               # SportsDataIO season string (end-year format)
LEAGUE_AVG_PACE: float = 100.3          # possessions per 48 min, 2025-26 estimate

# Prop types where a season average of exactly 0.0 can still be valid real data
# (e.g. non-shooting bigs for threes, low-usage players for secondary stats).
# Feature validation treats season_avg<=0 as "missing" for other props (points, etc.).
PROP_STATS_ALLOWING_ZERO_SEASON_AVG: frozenset[str] = frozenset(
    {"threes", "blocks", "steals", "turnovers", "assists"}
)
LEAGUE_AVG_DEF_EFF: float = 113.1       # pts per 100 possessions allowed, 2025-26

# FPA standard deviation used to normalize FPA z-scores (league-empirical estimate)
FPA_LEAGUE_STD: float = 4.5

# ---------------------------------------------------------------------------
# Injury redistribution — position similarity weights.
# When teammate X is OUT, active player Y inherits a fraction of X's vacancy
# weighted by positional overlap.  Values in [0, 1].
# ---------------------------------------------------------------------------
POSITION_SIMILARITY: dict[str, dict[str, float]] = {
    "PG": {"PG": 1.0, "SG": 0.6, "SF": 0.2, "PF": 0.1, "C": 0.0, "G": 0.8, "F": 0.2, "GF": 0.3, "FC": 0.1},
    "SG": {"PG": 0.6, "SG": 1.0, "SF": 0.4, "PF": 0.1, "C": 0.0, "G": 0.8, "F": 0.3, "GF": 0.4, "FC": 0.1},
    "SF": {"PG": 0.2, "SG": 0.4, "SF": 1.0, "PF": 0.5, "C": 0.1, "G": 0.3, "F": 0.8, "GF": 0.6, "FC": 0.3},
    "PF": {"PG": 0.1, "SG": 0.1, "SF": 0.5, "PF": 1.0, "C": 0.5, "G": 0.1, "F": 0.7, "GF": 0.3, "FC": 0.7},
    "C":  {"PG": 0.0, "SG": 0.0, "SF": 0.1, "PF": 0.5, "C": 1.0, "G": 0.0, "F": 0.3, "GF": 0.1, "FC": 0.8},
    "G":  {"PG": 0.8, "SG": 0.8, "SF": 0.3, "PF": 0.1, "C": 0.0, "G": 1.0, "F": 0.2, "GF": 0.4, "FC": 0.1},
    "F":  {"PG": 0.2, "SG": 0.3, "SF": 0.8, "PF": 0.7, "C": 0.3, "G": 0.2, "F": 1.0, "GF": 0.7, "FC": 0.5},
    "GF": {"PG": 0.3, "SG": 0.4, "SF": 0.6, "PF": 0.3, "C": 0.1, "G": 0.4, "F": 0.7, "GF": 1.0, "FC": 0.3},
    "FC": {"PG": 0.1, "SG": 0.1, "SF": 0.3, "PF": 0.7, "C": 0.8, "G": 0.1, "F": 0.5, "GF": 0.3, "FC": 1.0},
}

# ---------------------------------------------------------------------------
# Model blend weights
# (must sum to 1.0 per category; used in weighted projection averages)
# ---------------------------------------------------------------------------

PROJECTION_BLEND_WEIGHTS: dict[str, float] = {
    "season_avg": 0.35,
    "last10_avg": 0.30,
    "last5_avg": 0.20,
    "matchup_adj": 0.10,
    "recent_trend": 0.05,
}

# Stable per-game baseline before matchup/pace (anti fake-edge). Sums to 1.0.
BASELINE_BLEND_WEIGHTS: dict[str, float] = {
    "season": 0.50,
    "recent": 0.25,
    "per_minute_expected": 0.25,
}

# High-usage starters (assists/points): anchor to season first; recent noise must not collapse baseline.
BASELINE_BLEND_ELITE_ASSISTS: dict[str, float] = {
    "season": 0.68,
    "recent": 0.14,
    "per_minute_expected": 0.18,
}
BASELINE_BLEND_ELITE_POINTS: dict[str, float] = {
    "season": 0.62,
    "recent": 0.18,
    "per_minute_expected": 0.20,
}
ELITE_ASSISTS_SEASON_THRESHOLD: float = 7.25   # APG — primary playmaker tier
ELITE_POINTS_SEASON_THRESHOLD: float = 21.0
ELITE_REBOUNDS_SEASON_THRESHOLD: float = 9.0   # RPG — elite glass / rim bigs
BASELINE_BLEND_ELITE_REBOUNDS: dict[str, float] = {
    "season": 0.65,
    "recent": 0.17,
    "per_minute_expected": 0.18,
}

# Blended baseline vs season (ACTIVE only) — all prop types; starters get tighter anchors than bench.
BASELINE_SEASON_SOFT_FLOOR_RATIO: float = 0.91
BASELINE_HARD_ANCHOR_RATIO: float = 0.85
BASELINE_SEASON_SOFT_FLOOR_RATIO_BENCH: float = 0.83
BASELINE_HARD_ANCHOR_RATIO_BENCH: float = 0.75
# Recent game avg cannot drag blend below season × this without injury (slump dampener).
RECENT_VS_SEASON_FLOOR_RATIO: float = 0.88

# Single environment modifier: geometric mean of pace × matchup is clamped to this band.
ENVIRONMENT_MODIFIER_MIN: float = 0.92
ENVIRONMENT_MODIFIER_MAX: float = 1.08

# Projection guards vs season anchor (starters / bench) — tune in backtests.
PROJECTION_FLOOR_RATIO_STARTER: float = 0.88
PROJECTION_FLOOR_RATIO_BENCH: float = 0.68
PROJECTION_CEILING_RATIO_STARTER: float = 1.28
PROJECTION_CEILING_RATIO_BENCH: float = 1.35
# Extra floor for elite counting-stat seasons (e.g. 9+ APG) — guards use real season anchor.
PROJECTION_FLOOR_ELITE_ASSISTS: float = 0.90
ELITE_ASSISTS_FLOOR_THRESHOLD: float = 8.5
MINUTES_FLOOR_RELAX_THRESHOLD: float = 0.85  # if exp_min < this × season mpg, allow lower floor

# Threes: matchup/pace must not inflate *makes* above a minute-scaled anchor when role minutes are down.
THREES_ENV_DAMPEN_WHEN_MINUTES_DOWN: float = 0.42   # shrink env toward 1.0 when exp_mpg < season mpg
THREES_MAX_ABOVE_MINUTE_SCALED_ANCHOR: float = 1.14  # cap vs season_3pm × (exp_min / season_mpg) when minutes down

# Points: low-usage players are opportunity-limited — do not let matchup/form stack above minute-scaled PPG.
LOW_USAGE_RATE_THRESHOLD: float = 0.18            # USG% fraction; below = role / spacer archetype
POINTS_ENV_DAMPEN_LOW_USAGE_AND_MINUTES_DOWN: float = 0.45
LOW_USAGE_POINTS_MAX_ABOVE_MINUTE_ANCHOR: float = 1.15  # vs season_ppg × (exp_min / mpg) when minutes down
LOW_USAGE_MINUTES_VACUUM_RELAX: float = 2.0          # extra minutes from absences → allow slightly higher bump

# --- Low-usage points suppression (post-projection layer in PropEvaluator; see models/points_low_usage_suppression.py)
# Hard gate: all must hold to apply mean suppression / over caps (non-primary buckets only).
POINTS_SUPPRESSION_LINE_MAX: float = 10.5
POINTS_SUPPRESSION_USAGE_MAX: float = 0.19   # strictly below (fraction 0–1)
POINTS_SUPPRESSION_FGA_MAX: float = 7.5      # strictly below projected FGA proxy

# Primary scorer heuristics — bypass suppression entirely.
POINTS_BUCKET_PRIMARY_USAGE: float = 0.22
POINTS_BUCKET_PRIMARY_ALT_USAGE: float = 0.19
POINTS_BUCKET_PRIMARY_ALT_FGA: float = 7.5
POINTS_BUCKET_PRIMARY_MPG: float = 30.0
POINTS_BUCKET_PRIMARY_MPG_USAGE: float = 0.20

# Low-usage volatile signals (count toward volatile if >= POINTS_VOLATILE_MIN_SIGNALS).
POINTS_VOLATILE_SIGNAL_USAGE: float = 0.18
POINTS_VOLATILE_SIGNAL_FGA: float = 7.0
POINTS_VOLATILE_3PA_SHARE_OF_FGA: float = 0.52
POINTS_VOLATILE_FTA_PER_FGA_MAX: float = 0.14
POINTS_VOLATILE_POINTS_CV: float = 0.34
POINTS_VOLATILE_MIN_SIGNALS: int = 2

# Mean multipliers applied to raw projected points when suppression active.
POINTS_SUPPRESSION_MEAN_MULT_SECONDARY: float = 0.94
POINTS_SUPPRESSION_MEAN_MULT_VOLATILE: float = 0.86

# Extra multiplicative haircut from FGA floor-risk (scaled when FGA below POINTS_SUPPRESSION_FGA_MAX).
POINTS_SUPPRESSION_FGA_FLOOR_SLOPE: float = 0.018   # per FGA below 7.5, capped by max
POINTS_SUPPRESSION_FGA_FLOOR_MAX_EXTRA: float = 0.08

# Playoff fragility (low-touch players): extra haircut when game.is_playoff.
POINTS_SUPPRESSION_PLAYOFF_MULT_SECONDARY: float = 0.97
POINTS_SUPPRESSION_PLAYOFF_MULT_VOLATILE: float = 0.94

# Role stability (bench / minute volatility / questionable).
POINTS_SUPPRESSION_BENCH_MULT: float = 0.985
POINTS_SUPPRESSION_MINUTES_CV_THRESHOLD: float = 0.22
POINTS_SUPPRESSION_QUESTIONABLE_MULT: float = 0.98

# Over-probability cap after Normal tail (before global shrink): mean-to-line gap → max P(over).
POINTS_OVER_CAP_GAP_VOLATILE: float = 1.15
POINTS_OVER_CAP_MAX_P_VOLATILE_NEAR: float = 0.54
POINTS_OVER_CAP_GAP_SECONDARY: float = 1.35
POINTS_OVER_CAP_MAX_P_SECONDARY_NEAR: float = 0.58

# When expected_minutes < season MPG: cap projection vs minute-scaled season anchor (all usages).
MINUTES_DOWN_SCALED_CEILING_BASE: float = 1.10
MINUTES_DOWN_SCALED_CEILING_HIGH_USG: float = 1.15   # usage_rate >= this fraction
HIGH_USAGE_RATE_FLOOR: float = 0.22
MINUTES_DOWN_SCALED_CEILING_VACUUM: float = 1.18     # large teammate-out vacuum

# Points: implied scoring load vs FGA proxy (volume sanity).
POINTS_PER_FGA_EFFICIENCY_CEILING: float = 2.35      # max plausible pts / FGA for role validation

# Minutes engine: blend season MPG with recent windows (see MinutesModel).
MINUTES_BLEND_WEIGHTS: dict[str, float] = {
    "season_mpg": 0.52,
    "last5_mpg": 0.28,
    "last10_mpg": 0.20,
}
# High-minute starters: projected minutes should not sit far below season without injury/B2B.
STAR_MPG_SEASON_THRESHOLD: float = 28.0
STAR_MINUTES_VS_SEASON_FLOOR: float = 0.92

# Low prop lines — extra variance multiplier (applied in VarianceModel).
LOW_LINE_THRESHOLD: float = 2.0
LOW_LINE_VARIANCE_BOOST: float = 1.35

# Final gate: flag when projection deviates from season this much (structural sanity).
PROJECTION_VS_SEASON_OUTLIER_RATIO: float = 0.25

# Each projection audit flag shrinks distance of true_prob from 50% (compound, capped).
AUDIT_FLAG_TRUE_PROB_SHRINK_STEP: float = 0.97
AUDIT_FLAG_SHRINK_MAX_STEPS: int = 5

# Market calibration haircuts (edge vs implied gap)
MARKET_DISAGREE_SOFT: float = 0.12   # beyond this: mild haircut
MARKET_DISAGREE_HARD: float = 0.22   # beyond this: stronger haircut
CALIBRATION_HAIRCUT_SOFT: float = 0.92
CALIBRATION_HAIRCUT_HARD: float = 0.82

# Parlay true-prob independence discount
PARLAY_CORRELATION_PENALTY_K: float = 0.45
PARLAY_CALIBRATION_FACTOR_DEFAULT: float = 0.92
PARLAY_ALL_UNDER_PENALTY: float = 0.88
PARLAY_LONG_ODDS_AMERICAN: int = 600
PARLAY_LONG_ODDS_TRUE_CAP: float = 0.28

# Final self-check before treating a prop as high-edge (prefer conservative).
FINAL_GATE_HIGH_EDGE_EDGE: float = 0.055
FINAL_GATE_TRUE_SHRINK: float = 0.93
FINAL_GATE_IMPLIED_BLEND: float = 0.10

# ---------------------------------------------------------------------------
# Recent-form lookback windows
# ---------------------------------------------------------------------------

FORM_WINDOW_SHORT: int = 5
FORM_WINDOW_MEDIUM: int = 10
FORM_WINDOW_LONG: int = 20

# ---------------------------------------------------------------------------
# Distribution variance overrides (additive std adjustment per prop type)
# These inflate variance to account for game-to-game randomness beyond
# what the historical average captures.
# ---------------------------------------------------------------------------

VARIANCE_INFLATION: dict[PropType, float] = {
    PropType.POINTS:    1.55,   # was 1.15 — empirical NBA game-to-game scoring spread
    PropType.REBOUNDS:  2.05,   # rebounding is high-variance; overs need conservative std
    PropType.ASSISTS:   1.65,   # was 1.25
    PropType.THREES:    2.00,   # was 1.30 — shooting is highly volatile night-to-night
    PropType.PRA:       1.45,   # was 1.10
    PropType.BLOCKS:    2.20,   # was 1.40 — blocks are extremely high-variance
    PropType.STEALS:    2.20,   # was 1.40 — steals are extremely high-variance
    PropType.TURNOVERS: 1.70,   # was 1.25
}

# Minimum std as fraction of projected mean — prevents overconfident tight distributions
# when the projected mean is far from the prop line.
STD_MIN_FRACTION: dict[PropType, float] = {
    PropType.POINTS:    0.30,
    PropType.REBOUNDS:  0.35,
    PropType.ASSISTS:   0.40,
    PropType.THREES:    0.50,
    PropType.PRA:       0.25,
    PropType.BLOCKS:    0.55,
    PropType.STEALS:    0.55,
    PropType.TURNOVERS: 0.45,
}

# Absolute minimum std floor — prevents degenerate distributions for low-average players
STD_ABSOLUTE_MIN: dict[PropType, float] = {
    PropType.POINTS:    2.5,
    PropType.REBOUNDS:  1.2,
    PropType.ASSISTS:   1.0,
    PropType.THREES:    0.7,
    PropType.PRA:       4.0,
    PropType.BLOCKS:    0.4,
    PropType.STEALS:    0.4,
    PropType.TURNOVERS: 0.6,
}

# NegBin overdispersion — empirical NBA value (was hardcoded 1.3 in distributions.py)
# Var = mean × NEGBIN_VARIANCE_INFLATION.  2.0 matches observed rebound/assist spread.
NEGBIN_VARIANCE_INFLATION: float = 2.0

# ---------------------------------------------------------------------------
# Rebounds model — conservative rates + environment (see models/rebounds_support.py)
# ---------------------------------------------------------------------------
REBOUNDS_RATE_BLEND_SEASON: float = 0.70
REBOUNDS_RATE_BLEND_RECENT: float = 0.30
REBOUNDS_RECENT_RPM_MIN_RATIO: float = 0.92
REBOUNDS_RECENT_RPM_MAX_RATIO: float = 1.08
REBOUNDS_ENV_BAND_MIN: float = 0.96
REBOUNDS_ENV_BAND_MAX: float = 1.04
REBOUNDS_PACE_SENSITIVITY_MULT: float = 0.50   # scales PACE_SENSITIVITY[REBOUNDS]
REBOUNDS_POS_MATCHUP_CLAMP: tuple[float, float] = (0.90, 1.10)
REBOUNDS_NEGBIN_INFLATION_BASE: float = 2.45
REBOUNDS_NEGBIN_INFLATION_MINUTES_STRESS: float = 0.25
REBOUNDS_NEGBIN_INFLATION_HIGH_VOLATILITY: float = 3.15
# Prop evaluator: rebound overs at high lines / plus money
REBOUNDS_OVER_LINE_STRESS: float = 10.5
REBOUNDS_OVER_AMERICAN_LONGSHOT: int = 155
REBOUNDS_OVER_PROB_SHRINK_MINUTES: float = 0.82  # required_min vs exp min
REBOUNDS_OVER_PROB_SHRINK_LONGSHOT: float = 0.88

# ---------------------------------------------------------------------------
# Probability calibration pipeline constants
# These are applied in engine/prop_evaluator.py after the raw distribution
# tail probability is computed, to prevent unrealistic 95–100% outputs.
#
# Pipeline: raw_prob → shrinkage → completeness_penalty → ceiling_clamp
# ---------------------------------------------------------------------------

# Pulls all probabilities toward 50% before the completeness penalty.
# 0.80 means a raw 99.9% becomes 0.5 + 0.499 × 0.80 = 89.9%.
PROBABILITY_SHRINKAGE_FACTOR: float = 0.80

# Hard ceiling / floor on any single-game prop probability.
# Most realistic NBA props should fall well within this range.
MAX_PROBABILITY_CEILING: float = 0.93
MIN_PROBABILITY_FLOOR:   float = 0.07

# ---------------------------------------------------------------------------
# Positional fantasy points multiplier lookup
# FPA (fantasy points allowed) tables weight each stat contribution.
# Standard DraftKings scoring is used as the reference.
# ---------------------------------------------------------------------------

FANTASY_SCORING_WEIGHTS: dict[str, float] = {
    "points": 1.0,
    "rebounds": 1.25,
    "assists": 1.5,
    "steals": 2.0,
    "blocks": 2.0,
    "turnovers": -0.5,
    "threes": 0.5,   # bonus on top of the base point
    "double_double_bonus": 1.5,
    "triple_double_bonus": 3.0,
}

# FPA league-average baselines by position (DraftKings pts per game)
FPA_LEAGUE_AVG: dict[Position, float] = {
    Position.PG: 35.5,
    Position.SG: 32.0,
    Position.SF: 31.0,
    Position.PF: 32.5,
    Position.C: 36.0,
    Position.G: 33.5,
    Position.F: 31.5,
    Position.FC: 34.0,
    Position.GF: 32.0,
}

# ---------------------------------------------------------------------------
# Pace adjustment: how much pace affects each stat type
# 1.0 = fully pace-adjusted; 0.0 = not pace-sensitive
# ---------------------------------------------------------------------------

PACE_SENSITIVITY: dict[PropType, float] = {
    PropType.POINTS: 0.85,
    PropType.REBOUNDS: 0.70,
    PropType.ASSISTS: 0.80,
    PropType.THREES: 0.75,
    PropType.PRA: 0.82,
    PropType.BLOCKS: 0.60,
    PropType.STEALS: 0.65,
    PropType.TURNOVERS: 0.75,
}

# ---------------------------------------------------------------------------
# Home/away adjustment multipliers (league-average split)
# ---------------------------------------------------------------------------

HOME_ADVANTAGE_FACTOR: float = 1.02   # slight home boost to scoring
AWAY_PENALTY_FACTOR: float = 0.98

# ---------------------------------------------------------------------------
# Injury context adjustments
# When a key teammate is OUT, usage is redistributed.
# These are approximate league-average redistribution fractions.
# ---------------------------------------------------------------------------

TEAMMATE_OUT_USAGE_BOOST: float = 1.08    # ~8% usage boost per missing star
TEAMMATE_OUT_MINUTES_BOOST: float = 1.05

# ---------------------------------------------------------------------------
# Confidence tier thresholds (based on data quality and signal alignment)
# ---------------------------------------------------------------------------

CONFIDENCE_HIGH_THRESHOLD: float = 0.70
CONFIDENCE_MEDIUM_THRESHOLD: float = 0.50
CONFIDENCE_LOW_THRESHOLD: float = 0.30

# ---------------------------------------------------------------------------
# Edge filter defaults (user may override via CLI / UI)
# ---------------------------------------------------------------------------

DEFAULT_MIN_EDGE: float = 0.05          # 5% minimum edge
DEFAULT_MAX_LEGS: int = 3
DEFAULT_MIN_ODDS: int = -600
DEFAULT_MAX_ODDS: int = 400
DEFAULT_STAKE: float = 100.0

# Straight-bet favorite diagnostic band (American odds on the priced side).
# Evaluator attaches `favorite_band_audit` to each leg whose best price falls here.
FAVORITE_STRAIGHT_BET_AUDIT_BAND_LOW: int = -600
FAVORITE_STRAIGHT_BET_AUDIT_BAND_HIGH: int = -220

# ---------------------------------------------------------------------------
# Correlation engine thresholds
# ---------------------------------------------------------------------------

CORRELATION_BLOCK_THRESHOLD: float = 0.80    # above = auto-blocked leg pair
CORRELATION_PENALTY_THRESHOLD: float = 0.50  # above = penalised combo

# Same-player correlation priors (heuristic)
SAME_PLAYER_SAME_STAT_CORR: float = 1.00    # e.g. PRA + Points → blocked
SAME_PLAYER_DIFF_STAT_CORR: float = 0.55    # e.g. Points + Assists → penalised
SAME_GAME_SAME_TEAM_CORR: float = 0.40
SAME_GAME_DIFF_TEAM_CORR: float = 0.15
DIFF_GAME_CORR: float = 0.05

# ---------------------------------------------------------------------------
# Parlay ranking weights
# ---------------------------------------------------------------------------

PARLAY_RANK_WEIGHT_EDGE: float = 0.50
PARLAY_RANK_WEIGHT_CONFIDENCE: float = 0.30
PARLAY_RANK_WEIGHT_CORR_RISK: float = 0.20

# ---------------------------------------------------------------------------
# Vig removal - Shin method default parameter
# ---------------------------------------------------------------------------

SHIN_Z_DEFAULT: float = 0.02   # market overround fraction; calibrate per book

# ---------------------------------------------------------------------------
# NBA position aliases (handles alternate position strings from providers)
# ---------------------------------------------------------------------------

POSITION_ALIAS_MAP: dict[str, str] = {
    "point guard": "PG",
    "shooting guard": "SG",
    "small forward": "SF",
    "power forward": "PF",
    "center": "C",
    "guard": "G",
    "forward": "F",
    "forward-center": "FC",
    "guard-forward": "GF",
    "sf/pf": "SF",
    "pg/sg": "PG",
}

# Prop type alias map (normalise provider market names to PropType values)
PROP_ALIAS_MAP: dict[str, PropType] = {
    "player_points": PropType.POINTS,
    "pts": PropType.POINTS,
    "points": PropType.POINTS,
    "player_rebounds": PropType.REBOUNDS,
    "reb": PropType.REBOUNDS,
    "rebounds": PropType.REBOUNDS,
    "player_assists": PropType.ASSISTS,
    "ast": PropType.ASSISTS,
    "assists": PropType.ASSISTS,
    "player_threes": PropType.THREES,
    "three_pointers_made": PropType.THREES,
    "threes": PropType.THREES,
    "3pm": PropType.THREES,
    "player_pra": PropType.PRA,
    "pts_reb_ast": PropType.PRA,
    "pra": PropType.PRA,
    "player_blocks": PropType.BLOCKS,
    "blk": PropType.BLOCKS,
    "blocks": PropType.BLOCKS,
    "player_steals": PropType.STEALS,
    "stl": PropType.STEALS,
    "steals": PropType.STEALS,
    "player_turnovers": PropType.TURNOVERS,
    "tov": PropType.TURNOVERS,
    "turnovers": PropType.TURNOVERS,
}
