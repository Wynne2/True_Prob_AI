"""
Provider-level typed dataclasses used by the service layer.

These are intermediate representations that services produce before the
player_feature_builder assembles them into a FeatureVector.  Keeping them
separate prevents the domain entity layer from being polluted with
provider-specific schema concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InjuryContext:
    """
    Injury and availability context for a player.

    SOURCE: SportsDataIO primary (injury_context_service.py)
    """
    player_id: str
    player_name: str
    team_id: str
    status: str                    # active / questionable / doubtful / out
    injury_description: str = ""
    is_starter: bool = True
    projected_minutes: float = 0.0

    # Teammate vacancy analysis (derived from roster injury feed)
    teammates_out: list[str] = field(default_factory=list)    # player names
    teammates_out_count: int = 0
    teammate_usage_vacuum: float = 1.0  # >1.0 = extra usage available


@dataclass
class SplitContext:
    """
    Split-based stat context for a player.

    SOURCE: nba_api primary (splits_service.py)
    """
    player_id: str
    player_name: str
    prop_type: str

    # Season baseline
    season_avg: float = 0.0
    season_games: int = 0

    # Recent form
    last_5_avg: float = 0.0
    last_10_avg: float = 0.0
    last_10_std_dev: float = 0.0
    last_5_games: list[float] = field(default_factory=list)
    last_10_games: list[float] = field(default_factory=list)

    # Location splits
    home_avg: float = 0.0
    away_avg: float = 0.0
    home_games: int = 0
    away_games: int = 0

    # Opponent-specific (when available)
    vs_opp_avg: float = 0.0
    vs_opp_games: int = 0

    # Derived factors (split / season_avg)
    home_split_factor: float = 1.0
    away_split_factor: float = 1.0
    recent_trend_factor: float = 1.0   # last5 vs season
    vs_opp_factor: float = 1.0


@dataclass
class UsageTrackingContext:
    """
    Usage, touches, and possession context for a player.

    SOURCE: nba_api primary (usage_tracking_service.py)
    """
    player_id: str
    player_name: str
    team_id: str

    # Usage (SOURCE: nba_api LeagueDashPlayerStats Advanced)
    usage_rate: float = 0.0           # USG_PCT (0-1 fraction)
    possessions_per_game: float = 0.0 # POSS from advanced dashboard
    team_pace: float = 0.0            # PACE from team advanced dashboard

    # Tracking (SOURCE: nba_api LeagueDashPtStats)
    touches_per_game: float = 0.0
    time_of_possession: float = 0.0   # seconds per game
    front_ct_touches: float = 0.0
    paint_touches: float = 0.0
    elbow_touches: float = 0.0
    post_touches: float = 0.0
    potential_assists: float = 0.0
    rebound_chances: float = 0.0
    oreb_chances: float = 0.0
    dreb_chances: float = 0.0


@dataclass
class MatchupContext:
    """
    Opponent matchup context used for game-day adjustments.

    SOURCE: SportsDataIO (team defense stats) + nba_api (pace/possessions)
    """
    home_team_id: str
    away_team_id: str
    defense_team_id: str           # team the player is facing
    defense_team_abbr: str = ""

    # Defensive efficiency (SOURCE: nba_api advanced + SportsDataIO)
    def_rating: float = 0.0        # points per 100 possessions allowed
    opp_pace: float = 0.0          # possessions per 48 min
    pts_allowed_per_game: float = 0.0

    # Recent defensive form (SOURCE: SportsDataIO)
    last_10_pts_allowed: float = 0.0
    last_10_def_rating: float = 0.0

    # Blowout / game-script risk
    game_total: float = 0.0
    point_spread: float = 0.0

    # Normalised adjustment factors (derived; 1.0 = neutral)
    defense_factor: float = 1.0          # season def rating vs league avg
    recent_defense_factor: float = 1.0   # last-10 trend


@dataclass
class DvPEntry:
    """
    Defense vs Position entry for a single (team, position) combination.

    SOURCE: computed internally by data/builders/dvp_builder.py
    from nba_api + SportsDataIO raw game logs.
    """
    defense_team_id: str
    position: str                  # PG / SG / SF / PF / C

    # Absolute values (per game allowed to this position)
    pts_allowed: float = 0.0
    reb_allowed: float = 0.0
    ast_allowed: float = 0.0
    stl_forced: float = 0.0
    blk_forced: float = 0.0
    tov_forced: float = 0.0
    fantasy_allowed: float = 0.0   # DraftKings scoring formula

    # Windowed values
    last_10_pts: float = 0.0
    last_10_reb: float = 0.0
    last_10_ast: float = 0.0
    last_10_fantasy: float = 0.0
    last_5_pts: float = 0.0
    last_5_reb: float = 0.0
    last_5_ast: float = 0.0
    last_5_fantasy: float = 0.0

    # Normalised factors vs league average for this position (>1.0 = weaker D)
    norm_pts: float = 1.0
    norm_reb: float = 1.0
    norm_ast: float = 1.0
    norm_fantasy: float = 1.0

    # Sample size
    games_sample: int = 0
