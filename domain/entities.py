"""
Core domain entities for the NBA Prop AI platform.

All dataclasses are intentionally plain Python (no ORM coupling) so they
can be serialised, cached, and passed between any layer without friction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from domain.enums import (
    BookName,
    ConfidenceTier,
    DataSource,
    DistributionType,
    InjuryStatus,
    OddsFormat,
    PlayerRole,
    Position,
    PropSide,
    PropType,
)


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

@dataclass
class Player:
    """NBA player with all modelling-relevant fields."""
    player_id: str
    name: str
    team_id: str
    team_abbr: str
    position: Position
    role: PlayerRole = PlayerRole.STARTER
    injury_status: InjuryStatus = InjuryStatus.ACTIVE

    # Per-game season averages
    minutes_per_game: float = 0.0
    points_per_game: float = 0.0
    rebounds_per_game: float = 0.0
    assists_per_game: float = 0.0
    threes_per_game: float = 0.0
    blocks_per_game: float = 0.0
    steals_per_game: float = 0.0
    turnovers_per_game: float = 0.0

    # Advanced / tracking
    usage_rate: float = 0.0           # 0-1 fraction
    field_goal_attempts: float = 0.0
    free_throw_attempts: float = 0.0
    three_point_attempts: float = 0.0
    three_point_pct: float = 0.0
    touches: float = 0.0
    time_of_possession: float = 0.0   # seconds per game
    rebound_chances: float = 0.0
    potential_assists: float = 0.0

    # Contextual splits
    home_ppg: float = 0.0
    away_ppg: float = 0.0
    is_starter: bool = True

    # Recent form (last N games) — per-game values or single-element window averages.
    # Minutes lists are paired with each stat list so models can compute per-36 rates.
    last5_points: list[float] = field(default_factory=list)
    last5_rebounds: list[float] = field(default_factory=list)
    last5_assists: list[float] = field(default_factory=list)
    last5_minutes: list[float] = field(default_factory=list)
    last5_threes: list[float] = field(default_factory=list)
    last5_blocks: list[float] = field(default_factory=list)
    last5_steals: list[float] = field(default_factory=list)
    last5_turnovers: list[float] = field(default_factory=list)

    last10_points: list[float] = field(default_factory=list)
    last10_rebounds: list[float] = field(default_factory=list)
    last10_assists: list[float] = field(default_factory=list)
    last10_minutes: list[float] = field(default_factory=list)
    last10_threes: list[float] = field(default_factory=list)
    last10_blocks: list[float] = field(default_factory=list)
    last10_steals: list[float] = field(default_factory=list)
    last10_turnovers: list[float] = field(default_factory=list)

    # Injury redistribution context (set by PlayerContextService)
    minutes_vacuum: float = 0.0    # extra minutes available due to teammate absences

    data_source: DataSource = DataSource.SAMPLE


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------

@dataclass
class TeamDefense:
    """Defensive profile for a single team, broken out by opposing position."""
    team_id: str
    team_abbr: str

    # Points allowed per game to each position
    pts_allowed_pg: float = 0.0
    pts_allowed_sg: float = 0.0
    pts_allowed_sf: float = 0.0
    pts_allowed_pf: float = 0.0
    pts_allowed_c: float = 0.0

    # Rebounds allowed per game to each position
    reb_allowed_pg: float = 0.0
    reb_allowed_sg: float = 0.0
    reb_allowed_sf: float = 0.0
    reb_allowed_pf: float = 0.0
    reb_allowed_c: float = 0.0

    # Assists allowed per game to each position
    ast_allowed_pg: float = 0.0
    ast_allowed_sg: float = 0.0
    ast_allowed_sf: float = 0.0
    ast_allowed_pf: float = 0.0
    ast_allowed_c: float = 0.0

    # 3-pointers allowed per game to each position
    threes_allowed_pg: float = 0.0
    threes_allowed_sg: float = 0.0
    threes_allowed_sf: float = 0.0
    threes_allowed_pf: float = 0.0
    threes_allowed_c: float = 0.0

    # Blocks / steals forced
    blocks_allowed_per_game: float = 0.0
    steals_forced_per_game: float = 0.0
    turnovers_forced_per_game: float = 0.0

    # Team-level context
    defensive_efficiency: float = 110.0  # points per 100 possessions allowed
    pace: float = 100.0                  # possessions per 48 min
    paint_pts_allowed: float = 0.0
    perimeter_pts_allowed: float = 0.0
    fast_break_pts_allowed: float = 0.0
    second_chance_pts_allowed: float = 0.0

    # Fantasy points allowed by position (critical cross-model factor)
    fpa_pg: float = 0.0
    fpa_sg: float = 0.0
    fpa_sf: float = 0.0
    fpa_pf: float = 0.0
    fpa_c: float = 0.0

    data_source: DataSource = DataSource.SAMPLE


@dataclass
class Team:
    """Basic team entity."""
    team_id: str
    team_abbr: str
    city: str
    name: str
    conference: str
    division: str
    wins: int = 0
    losses: int = 0
    implied_total: float = 0.0
    data_source: DataSource = DataSource.SAMPLE


# ---------------------------------------------------------------------------
# Game
# ---------------------------------------------------------------------------

@dataclass
class Game:
    """Single NBA game on today's slate."""
    game_id: str
    home_team_id: str
    home_team_abbr: str
    away_team_id: str
    away_team_abbr: str
    game_date: date = field(default_factory=date.today)
    tip_off_time: Optional[datetime] = None
    # SportsDataIO / schedule: Scheduled, InProgress, Final, Postponed, etc.
    status: str = ""
    arena: str = ""
    city: str = ""

    # Betting context
    game_total: float = 0.0          # projected combined score
    home_spread: float = 0.0         # negative = home is favourite
    home_implied_total: float = 0.0
    away_implied_total: float = 0.0
    blowout_risk: float = 0.0        # 0-1; high = game may be lopsided early

    # Game context flags
    is_back_to_back_home: bool = False
    is_back_to_back_away: bool = False
    is_playoff: bool = False          # NBA playoffs — tighter rotations, star minutes ↑

    data_source: DataSource = DataSource.SAMPLE


# ---------------------------------------------------------------------------
# Odds
# ---------------------------------------------------------------------------

@dataclass
class OddsLine:
    """A single sportsbook's price for one player prop."""
    book: BookName
    player_id: str
    player_name: str
    prop_type: PropType
    line: float                      # the numeric spread (e.g. 24.5 points)
    over_odds: int                   # American odds for the over
    under_odds: int                  # American odds for the under
    game_id: str = ""
    team_abbr: str = ""
    opponent_abbr: str = ""
    timestamp: Optional[datetime] = None
    is_alternate_line: bool = False
    data_source: DataSource = DataSource.SAMPLE
    book_key: str = ""             # raw Odds API bookmaker key (for display when book == OTHER)


# ---------------------------------------------------------------------------
# Projection + Probability results
# ---------------------------------------------------------------------------

@dataclass
class StatProjection:
    """Raw output from a stat model before probability conversion."""
    player_id: str
    player_name: str
    prop_type: PropType
    projected_value: float
    distribution_type: DistributionType

    # Distribution parameters (vary by distribution type)
    dist_mean: float = 0.0
    dist_std: float = 0.0           # for Normal
    dist_n: int = 0                 # for Binomial / NegBin
    dist_p: float = 0.0             # for Binomial / NegBin / Poisson rate
    dist_lambda: float = 0.0        # for Poisson

    # Contributing factor weights (for explanation)
    minutes_factor: float = 1.0
    usage_factor: float = 1.0
    pace_factor: float = 1.0
    matchup_factor: float = 1.0
    defense_factor: float = 1.0
    fpa_factor: float = 1.0
    recent_form_factor: float = 1.0
    injury_factor: float = 1.0
    home_away_factor: float = 1.0

    confidence: ConfidenceTier = ConfidenceTier.MEDIUM

    # Anti–fake-edge pipeline (baseline vs game context)
    baseline_projection: float = 0.0
    expected_minutes: float = 0.0
    environment_multiplier: float = 1.0

    # Rate-based audit trail (skill estimate before pace/matchup stack)
    season_rate_per_minute: float = 0.0
    recent_rate_per_minute: float = 0.0
    raw_minute_scaled_mean: float = 0.0  # rate × expected_minutes before env / usage
    expected_field_goal_attempts_proxy: float = 0.0
    expected_three_point_attempts_proxy: float = 0.0
    projection_audit_flags: list[str] = field(default_factory=list)

    # Negative binomial tail probs (rebounds / assists / turnovers): if <= 0, evaluator uses global NEGBIN inflation.
    negbinom_variance_inflation: float = 0.0
    # Prop-type-specific diagnostics (e.g. rebounds: rates, factors, required minutes).
    model_context: dict = field(default_factory=dict)


@dataclass
class PropProbability:
    """True probability result for a specific prop + line combination."""
    player_id: str
    player_name: str
    team_abbr: str
    opponent_abbr: str
    game_id: str
    prop_type: PropType
    line: float
    side: PropSide

    projected_value: float
    true_probability: float    # model-derived probability this leg hits
    implied_probability: float # sportsbook implied probability (vig-removed)
    edge: float                # true_prob - implied_prob
    fair_odds: int             # American odds reflecting true probability

    sportsbook_odds: int       # American odds from best book
    best_book: BookName = BookName.SAMPLE
    best_book_key: str = ""    # Odds API bookmaker key for the priced line (display)

    confidence: ConfidenceTier = ConfidenceTier.MEDIUM
    distribution_type: DistributionType = DistributionType.NORMAL
    explanation: str = ""

    # All available lines across books (for line shopping display)
    all_lines: list[OddsLine] = field(default_factory=list)

    # Debug payload — populated in PropEvaluator when verbose mode is enabled.
    # Contains intermediate projection factors for inspection.
    debug_payload: Optional[dict] = None

    baseline_projection: float = 0.0
    adjusted_projection: float = 0.0
    expected_minutes: float = 0.0
    calibration_warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parlay
# ---------------------------------------------------------------------------

@dataclass
class ParlayLeg:
    """One leg of a parlay, derived from a PropProbability."""
    player_id: str
    player_name: str
    team_abbr: str
    opponent_abbr: str
    game_id: str
    prop_type: PropType
    line: float
    side: PropSide
    sportsbook: BookName
    sportsbook_odds: int       # American odds for this leg

    projected_value: float
    true_probability: float
    implied_probability: float
    edge: float
    fair_odds: int

    confidence: ConfidenceTier = ConfidenceTier.MEDIUM
    explanation: str = ""
    best_book_key: str = ""    # Odds API key for display (when sportsbook == OTHER)


@dataclass
class Parlay:
    """A complete parlay constructed by the parlay builder engine."""
    parlay_id: str
    legs: list[ParlayLeg]

    combined_american_odds: int = 0
    combined_decimal_odds: float = 0.0
    combined_implied_probability: float = 0.0
    combined_true_probability: float = 0.0
    combined_edge: float = 0.0

    confidence_tier: ConfidenceTier = ConfidenceTier.MEDIUM
    correlation_risk_score: float = 0.0   # 0 = no risk, 1 = fully correlated
    diversification_bonus: float = 0.0

    # Populated by bankroll engine at query time
    stake: float = 0.0
    total_return: float = 0.0
    net_profit: float = 0.0

    # Ranking scores
    edge_rank: int = 0
    balanced_score: float = 0.0
    risk_profile_tags: list[str] = field(default_factory=list)

    @property
    def num_legs(self) -> int:
        return len(self.legs)
