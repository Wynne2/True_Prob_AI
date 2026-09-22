"""
FeatureVector – optional immutable snapshot of a player-prop evaluation.

The initial BALLDONTLIE live pipeline supplies Player history directly. This
type remains the ledger/replay contract for pre-built snapshots and must never
be populated from a different provider silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class GameObservation:
    """One dated player game observation used for rate and variance estimates."""

    game_id: str
    game_date: str
    minutes: float
    points: float = 0.0
    rebounds: float = 0.0
    assists: float = 0.0
    threes: float = 0.0
    blocks: float = 0.0
    steals: float = 0.0
    turnovers: float = 0.0


@dataclass
class FeatureVector:
    """
    Complete feature set for evaluating a single player's prop.

    Fields are grouped by category matching the architecture spec.
    Source annotations are in the comments:
      [BALLDONTLIE] -> normalized provider observation
      [derived]     -> local calculation from completed observations
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    player_id: str = ""
    player_name: str = ""
    team_id: str = ""
    team_abbr: str = ""
    opponent_team_id: str = ""
    opponent_team_abbr: str = ""
    position: str = ""           # normalised: PG / SG / SF / PF / C
    game_id: str = ""

    # Prop being evaluated
    prop_type: str = ""          # matches domain.enums.PropType value
    market_line: float = 0.0     # [Odds API]

    # ------------------------------------------------------------------
    # BASE PRODUCTION  [BALLDONTLIE completed-game observations]
    # ------------------------------------------------------------------
    season_avg: float = 0.0          # season average for the prop stat
    season_per_minute: float = 0.0   # season stat per minute played
    projected_minutes: float = 0.0   # [SportsDataIO primary]
    recent_5_avg: float = 0.0        # last-5 games rolling average
    recent_10_avg: float = 0.0       # last-10 games rolling average
    recent_std_dev: float = 0.0      # rolling std dev over last 10
    # Immutable game-level evidence. Window averages never replace this data.
    recent_observations: tuple[GameObservation, ...] = ()

    # ------------------------------------------------------------------
    # Optional advanced/tracking inputs (not enabled in initial live scope)
    # ------------------------------------------------------------------
    usage_rate: float = 0.0           # optional observed fraction
    touches_per_game: float = 0.0     # optional observed tracking metric
    time_of_possession: float = 0.0   # optional observed seconds/game
    possessions_per_game: float = 0.0 # optional observed possessions/game
    pace_context: float = 0.0         # optional observed possessions/48
    potential_assists: float = 0.0    # optional observed tracking metric
    rebound_chances: float = 0.0      # optional observed tracking metric

    # ------------------------------------------------------------------
    # SPLITS [derived locally from completed observations]
    # ------------------------------------------------------------------
    home_away_split_factor: float = 1.0   # [derived] home/road split ratio
    opponent_split_factor: float = 1.0    # [derived] opponent-history ratio
    last_n_split_factor: float = 1.0      # [derived] recent-history ratio

    # ------------------------------------------------------------------
    # INJURY / ROLE  [BALLDONTLIE availability observation]
    # ------------------------------------------------------------------
    player_injury_status: str = "active"    # [SportsDataIO]
    teammates_out_count: int = 0            # [SportsDataIO] key teammates ruled out
    teammate_usage_vacuum_factor: float = 1.0  # [derived] usage boost from absences
    role_stability_factor: float = 1.0      # [derived] starter/bench consistency
    starter_flag: bool = True               # [SportsDataIO] depth chart position

    # ------------------------------------------------------------------
    # MATCHUP CONTEXT [disabled/neutral unless validated BDL evidence exists]
    # ------------------------------------------------------------------
    opponent_defense_factor: float = 1.0         # [SportsDataIO] team def rating
    opponent_recent_defense_factor: float = 1.0  # [SportsDataIO] last-10 def trend
    opp_pace: float = 0.0                        # optional validated context
    opp_def_rating: float = 0.0                  # optional validated context
    opp_pts_allowed: float = 0.0                 # [SportsDataIO] season pts allowed

    # ------------------------------------------------------------------
    # DvP (disabled/neutral in the initial BALLDONTLIE rollout)
    # ------------------------------------------------------------------
    dvp_points_factor: float = 1.0    # normalised; >1.0 = weak def vs pts
    dvp_rebounds_factor: float = 1.0
    dvp_assists_factor: float = 1.0
    dvp_fantasy_factor: float = 1.0   # fantasy pts allowed vs this position

    # Raw DvP values (absolute, not normalised) for reference
    dvp_pts_allowed: float = 0.0
    dvp_reb_allowed: float = 0.0
    dvp_ast_allowed: float = 0.0
    dvp_fantasy_allowed: float = 0.0

    # ------------------------------------------------------------------
    # MARKET  [Odds API only]
    # ------------------------------------------------------------------
    over_odds: int = -110
    under_odds: int = -110
    implied_probability_over: float = 0.5
    implied_probability_under: float = 0.5
    best_book: str = "sample"
    consensus_line: float = 0.0

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    data_completeness: float = 0.0   # 0-1 fraction of fields populated
    low_confidence_flags: list[str] = field(default_factory=list)
