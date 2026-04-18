"""
Shared numeric constants and weight tables for the NBA Prop AI platform.

All tunable parameters live here so they can be adjusted in one place
without touching model logic.
"""

from domain.enums import Position, PropType

# ---------------------------------------------------------------------------
# Season context
# ---------------------------------------------------------------------------

NBA_SEASON: str = "2024-25"
LEAGUE_AVG_PACE: float = 100.3          # possessions per 48 min, 2024-25 estimate
LEAGUE_AVG_DEF_EFF: float = 113.1       # pts per 100 possessions allowed, 2024-25

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
    PropType.REBOUNDS:  1.80,   # was 1.20 — high overdispersion in rebounding
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
DEFAULT_MIN_ODDS: int = -200
DEFAULT_MAX_ODDS: int = 400
DEFAULT_STAKE: float = 100.0

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
