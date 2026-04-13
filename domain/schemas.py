"""
Pydantic schemas for cross-provider normalisation.

Raw API responses from any provider are validated and coerced into these
schemas before being converted into domain entities. This ensures the rest
of the system always works with well-typed, range-validated data regardless
of which upstream source delivered it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Shared validators
# ---------------------------------------------------------------------------

def _clamp_probability(v: float) -> float:
    """Ensure probability values stay in [0, 1]."""
    return max(0.0, min(1.0, v))


def _ensure_non_negative(v: float) -> float:
    return max(0.0, v)


# ---------------------------------------------------------------------------
# Provider response schemas (inbound - raw from API)
# ---------------------------------------------------------------------------

class RawGameSchema(BaseModel):
    """Normalised game record from any provider."""
    game_id: str
    home_team_id: str
    home_team_abbr: str
    away_team_id: str
    away_team_abbr: str
    game_date: date
    tip_off_utc: Optional[datetime] = None
    arena: Optional[str] = None
    city: Optional[str] = None
    game_total: float = Field(default=0.0, ge=0)
    home_spread: float = 0.0
    home_implied_total: float = Field(default=0.0, ge=0)
    away_implied_total: float = Field(default=0.0, ge=0)
    blowout_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    is_back_to_back_home: bool = False
    is_back_to_back_away: bool = False
    source: str = "sample"

    model_config = {"extra": "ignore"}


class RawPlayerSchema(BaseModel):
    """Normalised player record from any provider."""
    player_id: str
    name: str
    team_id: str
    team_abbr: str
    position: str = "G"
    role: str = "starter"
    injury_status: str = "active"

    minutes_per_game: float = Field(default=0.0, ge=0)
    points_per_game: float = Field(default=0.0, ge=0)
    rebounds_per_game: float = Field(default=0.0, ge=0)
    assists_per_game: float = Field(default=0.0, ge=0)
    threes_per_game: float = Field(default=0.0, ge=0)
    blocks_per_game: float = Field(default=0.0, ge=0)
    steals_per_game: float = Field(default=0.0, ge=0)
    turnovers_per_game: float = Field(default=0.0, ge=0)

    usage_rate: float = Field(default=0.0, ge=0, le=1)
    field_goal_attempts: float = Field(default=0.0, ge=0)
    free_throw_attempts: float = Field(default=0.0, ge=0)
    three_point_attempts: float = Field(default=0.0, ge=0)
    three_point_pct: float = Field(default=0.0, ge=0, le=1)
    touches: float = Field(default=0.0, ge=0)
    time_of_possession: float = Field(default=0.0, ge=0)
    rebound_chances: float = Field(default=0.0, ge=0)
    potential_assists: float = Field(default=0.0, ge=0)

    home_ppg: float = Field(default=0.0, ge=0)
    away_ppg: float = Field(default=0.0, ge=0)
    is_starter: bool = True

    last5_points: list[float] = Field(default_factory=list)
    last5_rebounds: list[float] = Field(default_factory=list)
    last5_assists: list[float] = Field(default_factory=list)
    last5_minutes: list[float] = Field(default_factory=list)
    last5_threes: list[float] = Field(default_factory=list)
    last10_points: list[float] = Field(default_factory=list)
    last10_rebounds: list[float] = Field(default_factory=list)
    last10_assists: list[float] = Field(default_factory=list)
    last10_minutes: list[float] = Field(default_factory=list)

    source: str = "sample"

    model_config = {"extra": "ignore"}

    @field_validator("usage_rate", mode="before")
    @classmethod
    def normalise_usage(cls, v: Any) -> float:
        """Convert percentage usage (e.g. 28.5) to fraction (0.285)."""
        v = float(v) if v is not None else 0.0
        return v / 100.0 if v > 1.0 else v

    @field_validator("three_point_pct", mode="before")
    @classmethod
    def normalise_3pt_pct(cls, v: Any) -> float:
        v = float(v) if v is not None else 0.0
        return v / 100.0 if v > 1.0 else v


class RawTeamDefenseSchema(BaseModel):
    """Normalised team defensive profile from any provider."""
    team_id: str
    team_abbr: str

    pts_allowed_pg: float = Field(default=0.0, ge=0)
    pts_allowed_sg: float = Field(default=0.0, ge=0)
    pts_allowed_sf: float = Field(default=0.0, ge=0)
    pts_allowed_pf: float = Field(default=0.0, ge=0)
    pts_allowed_c: float = Field(default=0.0, ge=0)

    reb_allowed_pg: float = Field(default=0.0, ge=0)
    reb_allowed_sg: float = Field(default=0.0, ge=0)
    reb_allowed_sf: float = Field(default=0.0, ge=0)
    reb_allowed_pf: float = Field(default=0.0, ge=0)
    reb_allowed_c: float = Field(default=0.0, ge=0)

    ast_allowed_pg: float = Field(default=0.0, ge=0)
    ast_allowed_sg: float = Field(default=0.0, ge=0)
    ast_allowed_sf: float = Field(default=0.0, ge=0)
    ast_allowed_pf: float = Field(default=0.0, ge=0)
    ast_allowed_c: float = Field(default=0.0, ge=0)

    threes_allowed_pg: float = Field(default=0.0, ge=0)
    threes_allowed_sg: float = Field(default=0.0, ge=0)
    threes_allowed_sf: float = Field(default=0.0, ge=0)
    threes_allowed_pf: float = Field(default=0.0, ge=0)
    threes_allowed_c: float = Field(default=0.0, ge=0)

    blocks_allowed_per_game: float = Field(default=0.0, ge=0)
    steals_forced_per_game: float = Field(default=0.0, ge=0)
    turnovers_forced_per_game: float = Field(default=0.0, ge=0)

    defensive_efficiency: float = Field(default=110.0, ge=80, le=140)
    pace: float = Field(default=100.0, ge=80, le=120)
    paint_pts_allowed: float = Field(default=0.0, ge=0)
    perimeter_pts_allowed: float = Field(default=0.0, ge=0)
    fast_break_pts_allowed: float = Field(default=0.0, ge=0)
    second_chance_pts_allowed: float = Field(default=0.0, ge=0)

    fpa_pg: float = Field(default=0.0, ge=0)
    fpa_sg: float = Field(default=0.0, ge=0)
    fpa_sf: float = Field(default=0.0, ge=0)
    fpa_pf: float = Field(default=0.0, ge=0)
    fpa_c: float = Field(default=0.0, ge=0)

    source: str = "sample"

    model_config = {"extra": "ignore"}


class RawOddsLineSchema(BaseModel):
    """Normalised odds line from any sportsbook / odds provider."""
    book: str
    player_id: str
    player_name: str
    prop_type: str
    line: float
    over_odds: int
    under_odds: int
    game_id: str = ""
    team_abbr: str = ""
    opponent_abbr: str = ""
    timestamp: Optional[datetime] = None
    is_alternate_line: bool = False
    source: str = "sample"

    model_config = {"extra": "ignore"}

    @field_validator("over_odds", "under_odds", mode="before")
    @classmethod
    def coerce_odds(cls, v: Any) -> int:
        """Accept string American odds like '+150' and coerce to int."""
        if isinstance(v, str):
            v = v.replace("+", "").strip()
        return int(float(v))


# ---------------------------------------------------------------------------
# Output schemas (outbound - for API / Streamlit serialisation)
# ---------------------------------------------------------------------------

class PropResultSchema(BaseModel):
    """Serialisable output for a single evaluated prop."""
    player_name: str
    team_abbr: str
    opponent_abbr: str
    prop_type: str
    line: float
    side: str
    projected_value: float
    true_probability: float
    implied_probability: float
    edge: float
    fair_odds: int
    sportsbook_odds: int
    best_book: str
    confidence: str
    explanation: str

    @field_validator("true_probability", "implied_probability", mode="after")
    @classmethod
    def validate_prob(cls, v: float) -> float:
        return _clamp_probability(v)


class ParlayLegSchema(BaseModel):
    """Serialisable output for a single parlay leg."""
    player_name: str
    team_abbr: str
    opponent_abbr: str
    prop_type: str
    line: float
    side: str
    sportsbook: str
    sportsbook_odds: int
    projected_value: float
    true_probability: float
    implied_probability: float
    edge: float
    fair_odds: int
    confidence: str
    explanation: str


class ParlayResultSchema(BaseModel):
    """Serialisable output for a complete ranked parlay."""
    parlay_id: str
    num_legs: int
    legs: list[ParlayLegSchema]
    combined_american_odds: int
    combined_decimal_odds: float
    combined_implied_probability: float
    combined_true_probability: float
    combined_edge: float
    confidence_tier: str
    correlation_risk_score: float
    stake: float
    total_return: float
    net_profit: float
    edge_rank: int
    balanced_score: float
    risk_profile_tags: list[str]
