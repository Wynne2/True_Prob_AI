"""
FeatureVector  –  unified per-player-prop feature object.

The feature vector is assembled by data/builders/player_feature_builder.py
from all service layer outputs (nba_api + SportsDataIO) and consumed by
engine/prop_evaluator.py.

Every field is documented with its source of truth so the algorithm
ownership is explicit throughout the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FeatureVector:
    """
    Complete feature set for evaluating a single player's prop.

    Fields are grouped by category matching the architecture spec.
    Source annotations are in the comments:
      [nba_api]      -> pulled via data/loaders/nba_api_loader.py
      [SportsDataIO] -> pulled via data/loaders/sportsdataio_loader.py
      [Odds API]     -> provided externally when prop is evaluated
      [derived]      -> computed inside a service or builder
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
    # BASE PRODUCTION  [SportsDataIO primary, nba_api supplement]
    # ------------------------------------------------------------------
    season_avg: float = 0.0          # season average for the prop stat
    season_per_minute: float = 0.0   # season stat per minute played
    projected_minutes: float = 0.0   # [SportsDataIO primary]
    recent_5_avg: float = 0.0        # last-5 games rolling average
    recent_10_avg: float = 0.0       # last-10 games rolling average
    recent_std_dev: float = 0.0      # rolling std dev over last 10

    # ------------------------------------------------------------------
    # USAGE / TRACKING  [nba_api PRIMARY]
    # ------------------------------------------------------------------
    usage_rate: float = 0.0           # [nba_api] LeagueDashPlayerStats advanced
    touches_per_game: float = 0.0     # [nba_api] LeagueDashPtStats Possessions
    time_of_possession: float = 0.0   # [nba_api] seconds per game
    possessions_per_game: float = 0.0 # [nba_api] LeagueDashPlayerStats advanced
    pace_context: float = 0.0         # [nba_api] team pace (possessions per 48)
    potential_assists: float = 0.0    # [nba_api] LeagueDashPtStats Passing
    rebound_chances: float = 0.0      # [nba_api] LeagueDashPtStats Rebounding

    # ------------------------------------------------------------------
    # SPLITS  [nba_api PRIMARY]
    # ------------------------------------------------------------------
    home_away_split_factor: float = 1.0   # [nba_api] home/road split ratio
    opponent_split_factor: float = 1.0    # [nba_api] vs this opponent historically
    last_n_split_factor: float = 1.0      # [nba_api] last-N adjustment vs season avg

    # ------------------------------------------------------------------
    # INJURY / ROLE  [SportsDataIO PRIMARY]
    # ------------------------------------------------------------------
    player_injury_status: str = "active"    # [SportsDataIO]
    teammates_out_count: int = 0            # [SportsDataIO] key teammates ruled out
    teammate_usage_vacuum_factor: float = 1.0  # [derived] usage boost from absences
    role_stability_factor: float = 1.0      # [derived] starter/bench consistency
    starter_flag: bool = True               # [SportsDataIO] depth chart position

    # ------------------------------------------------------------------
    # MATCHUP CONTEXT  [SportsDataIO + nba_api blended]
    # ------------------------------------------------------------------
    opponent_defense_factor: float = 1.0         # [SportsDataIO] team def rating
    opponent_recent_defense_factor: float = 1.0  # [SportsDataIO] last-10 def trend
    opp_pace: float = 0.0                        # [nba_api] opponent pace
    opp_def_rating: float = 0.0                  # [nba_api/SportsDataIO]
    opp_pts_allowed: float = 0.0                 # [SportsDataIO] season pts allowed

    # ------------------------------------------------------------------
    # DvP (DEFENSE vs POSITION)  [derived from nba_api + SportsDataIO]
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
