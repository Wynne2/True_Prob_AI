"""
Explanation engine.

Generates human-readable explanations for each evaluated prop.
These are shown in the UI and CLI output to give context for every edge.
"""

from __future__ import annotations

from typing import Optional

from domain.entities import Player, StatProjection, TeamDefense
from domain.enums import PropSide, PropType
from utils.formatting import format_edge, format_stat


def build_explanation(
    player: Player,
    prop_type: PropType,
    side: PropSide,
    projection: StatProjection,
    defense: Optional[TeamDefense],
) -> str:
    """
    Build a concise explanation string for a prop evaluation.

    Example:
      "Jokic projects 26.2 pts (line 25.5 OVER). Strong pace (101.5),
       ATL allows 38.5 FPA to C (league avg 36.0). Recent form: [28,25,30].
       Usage boost: +8% (teammate out)."
    """
    parts: list[str] = []

    proj_val = format_stat(projection.projected_value)
    parts.append(f"Projected {prop_type.value}: {proj_val} (model mean).")

    # Pace context
    if defense and defense.pace > 0:
        pace_note = "high" if defense.pace > 102 else "low" if defense.pace < 98 else "average"
        parts.append(f"Opponent pace: {defense.pace:.1f} ({pace_note}).")

    # Defensive context
    if defense:
        if defense.defensive_efficiency > 114:
            parts.append(f"Weak opponent defense (DEff {defense.defensive_efficiency:.1f}).")
        elif defense.defensive_efficiency < 108:
            parts.append(f"Elite opponent defense (DEff {defense.defensive_efficiency:.1f}).")

    # FPA context
    if defense and projection.fpa_factor != 1.0:
        direction = "favourable" if projection.fpa_factor > 1.0 else "tough"
        parts.append(f"FPA matchup is {direction} (factor {projection.fpa_factor:.2f}).")

    # Recent form
    form_log = _recent_form(player, prop_type)
    if form_log:
        log_str = ", ".join(str(v) for v in form_log[-5:])
        parts.append(f"Recent L5: [{log_str}].")

    # Adjustments applied
    if projection.injury_factor < 1.0:
        parts.append(f"Injury adjustment applied ({projection.injury_factor:.0%} of baseline).")
    if abs(projection.home_away_factor - 1.0) > 0.02:
        direction = "home" if projection.home_away_factor > 1.0 else "away"
        parts.append(f"Historical {direction} split applied.")

    return " ".join(parts)


def _recent_form(player: Player, prop_type: PropType) -> list[float]:
    if prop_type == PropType.POINTS:
        return player.last5_points
    if prop_type == PropType.REBOUNDS:
        return player.last5_rebounds
    if prop_type == PropType.ASSISTS:
        return player.last5_assists
    if prop_type == PropType.THREES:
        return player.last5_threes
    if prop_type == PropType.PRA:
        pts = player.last5_points
        reb = player.last5_rebounds
        ast = player.last5_assists
        if pts and reb and ast and len(pts) == len(reb) == len(ast):
            return [p + r + a for p, r, a in zip(pts, reb, ast)]
    return []
