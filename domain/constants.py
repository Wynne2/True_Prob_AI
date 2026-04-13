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
    PropType.POINTS: 1.15,
    PropType.REBOUNDS: 1.20,
    PropType.ASSISTS: 1.25,
    PropType.THREES: 1.30,
    PropType.PRA: 1.10,
    PropType.BLOCKS: 1.40,
    PropType.STEALS: 1.40,
    PropType.TURNOVERS: 1.25,
}

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
