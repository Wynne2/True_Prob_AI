"""
Enumerations for the NBA Prop AI platform.

All shared enum types are centralised here so domain entities, models,
and providers share a single source of truth.
"""

from enum import Enum, auto


class PropType(str, Enum):
    """Supported NBA player prop markets."""
    POINTS = "points"
    REBOUNDS = "rebounds"
    ASSISTS = "assists"
    THREES = "threes"          # 3-pointers made
    PRA = "pra"                # points + rebounds + assists
    BLOCKS = "blocks"
    STEALS = "steals"
    TURNOVERS = "turnovers"


class PropSide(str, Enum):
    """Which side of the line is being evaluated."""
    OVER = "over"
    UNDER = "under"


class ConfidenceTier(str, Enum):
    """Qualitative confidence tier attached to each evaluated prop."""
    HIGH = "high"          # multiple strong signals align
    MEDIUM = "medium"      # moderate signal alignment
    LOW = "low"            # weak or conflicting signals
    VERY_LOW = "very_low"  # insufficient data or high variance


class PlayerRole(str, Enum):
    """Player role on the depth chart."""
    STARTER = "starter"
    BENCH = "bench"
    RESERVE = "reserve"
    INACTIVE = "inactive"
    GAME_TIME_DECISION = "game_time_decision"
    OUT = "out"


class InjuryStatus(str, Enum):
    """Standard NBA injury designations."""
    ACTIVE = "active"
    QUESTIONABLE = "questionable"
    DOUBTFUL = "doubtful"
    OUT = "out"
    DAY_TO_DAY = "day_to_day"
    SUSPENDED = "suspended"
    NOT_WITH_TEAM = "not_with_team"


class BookName(str, Enum):
    """Supported sportsbooks for odds ingestion."""
    FANDUEL = "fanduel"
    DRAFTKINGS = "draftkings"
    BETMGM = "betmgm"
    CAESARS = "caesars"
    POINTSBET = "pointsbet"
    BETRIVERS = "betrivers"
    BOVADA = "bovada"
    BET365 = "bet365"
    PINNACLE = "pinnacle"
    SAMPLE = "sample"          # internal sample data book


class OddsFormat(str, Enum):
    """Odds representation format."""
    AMERICAN = "american"   # e.g. -110, +150
    DECIMAL = "decimal"     # e.g. 1.909, 2.5
    FRACTIONAL = "fractional"  # e.g. 10/11


class Position(str, Enum):
    """NBA player positions."""
    PG = "PG"
    SG = "SG"
    SF = "SF"
    PF = "PF"
    C = "C"
    G = "G"    # generic guard
    F = "F"    # generic forward
    FC = "FC"  # forward-center
    GF = "GF"  # guard-forward


class DistributionType(str, Enum):
    """Statistical distribution used for a given prop projection."""
    NORMAL = "normal"
    POISSON = "poisson"
    NEGATIVE_BINOMIAL = "negative_binomial"
    BINOMIAL = "binomial"


class SortField(str, Enum):
    """Fields available for parlay ranking."""
    EDGE = "edge"
    CONFIDENCE = "confidence"
    COMBINED_ODDS = "combined_odds"
    CORRELATION_RISK = "correlation_risk"
    BALANCED_SCORE = "balanced_score"


class ParlayRiskProfile(str, Enum):
    """Named parlay risk profiles for the output summary."""
    HIGHEST_EDGE = "highest_edge"
    SAFEST = "safest"
    BEST_BALANCED = "best_balanced"
    BEST_ODDS = "best_odds"


class DataSource(str, Enum):
    """Tracks which provider supplied a given data record."""
    SPORTSDATAIO = "sportsdataio"
    SPORTRADAR = "sportradar"
    ODDS_API = "odds_api"
    FANTASYPROS = "fantasypros"
    NBA_OFFICIAL = "nba_official"
    STATMUSE = "statmuse"
    ROTOGRINDERS = "rotogrinders"
    ROTOWIRE = "rotowire"
    CSV_IMPORT = "csv_import"
    SAMPLE = "sample"
