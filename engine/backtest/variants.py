"""
Controlled copies of the same player and defense.

The live projection functions are not modified. Each variant only changes
which inputs those functions see.
"""

from __future__ import annotations

from dataclasses import replace

from domain.constants import LEAGUE_AVG_DEF_EFF, LEAGUE_AVG_PACE
from domain.entities import Player, TeamDefense

VARIANT_A = "A_opportunity"
VARIANT_B = "B_matchup"
VARIANT_C = "C_full"
VARIANT_C_NO_POS = "C_no_positional"
VARIANT_D = "D_quarter_strength"
VARIANT_E = "E_interaction"


def _neutral_defense(defense: TeamDefense) -> TeamDefense:
    return TeamDefense(
        team_id=defense.team_id,
        team_abbr=defense.team_abbr,
        pace=LEAGUE_AVG_PACE,
        pace_is_fallback=False,
        pace_source="neutral_for_model_a",
        defensive_efficiency=LEAGUE_AVG_DEF_EFF,
        data_source=defense.data_source,
    )


def _without_position(defense: TeamDefense) -> TeamDefense:
    return replace(
        defense,
        pts_allowed_pg=0.0, pts_allowed_sg=0.0, pts_allowed_sf=0.0,
        pts_allowed_pf=0.0, pts_allowed_c=0.0,
        reb_allowed_pg=0.0, reb_allowed_sg=0.0, reb_allowed_sf=0.0,
        reb_allowed_pf=0.0, reb_allowed_c=0.0,
        ast_allowed_pg=0.0, ast_allowed_sg=0.0, ast_allowed_sf=0.0,
        ast_allowed_pf=0.0, ast_allowed_c=0.0,
        threes_allowed_pg=0.0, threes_allowed_sg=0.0, threes_allowed_sf=0.0,
        threes_allowed_pf=0.0, threes_allowed_c=0.0,
        fpa_pg=0.0, fpa_sg=0.0, fpa_sf=0.0, fpa_pf=0.0, fpa_c=0.0,
    )


_POSITION_FIELDS = (
    "pts_allowed_pg", "pts_allowed_sg", "pts_allowed_sf", "pts_allowed_pf", "pts_allowed_c",
    "reb_allowed_pg", "reb_allowed_sg", "reb_allowed_sf", "reb_allowed_pf", "reb_allowed_c",
    "ast_allowed_pg", "ast_allowed_sg", "ast_allowed_sf", "ast_allowed_pf", "ast_allowed_c",
    "threes_allowed_pg", "threes_allowed_sg", "threes_allowed_sf",
    "threes_allowed_pf", "threes_allowed_c",
)


def _positional_only(defense: TeamDefense) -> TeamDefense:
    """League pace and defensive rating, plus the measured positional allowances.

    Pace stays at the league number so a B-versus-A comparison is the positional
    adjustment alone. Fantasy points allowed are left empty so they are not
    stacked on top of the same stats.
    """
    neutral = _neutral_defense(defense)
    values = {name: getattr(defense, name) for name in _POSITION_FIELDS}
    return replace(neutral, **values, pace_source="league_average_position_test")


def variant_inputs(
    player: Player,
    defense: TeamDefense,
    variant: str,
) -> tuple[Player, TeamDefense]:
    """
    A: the player's own minutes, shot volume, chances, and conversion. No opponent adjustment.
    B: A plus positional allowances only.
    C: A plus positional allowances, measured pace, and measured defensive rating.
       Injury redistribution stays neutral. Callers must label C incomplete when
       historical injuries were not available.
    C without position: C with the positional allowances removed.
    """
    quiet = replace(player, usage_vacuum=1.0, minutes_vacuum=0.0)
    if variant == VARIANT_A:
        return quiet, _neutral_defense(defense)
    if variant == VARIANT_B:
        return quiet, _positional_only(defense)
    if variant == VARIANT_C:
        return quiet, defense
    if variant == VARIANT_C_NO_POS:
        return quiet, _without_position(defense)
    raise ValueError(f"Unknown variant: {variant}")
