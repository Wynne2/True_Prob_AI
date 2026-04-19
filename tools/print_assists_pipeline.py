"""
Print assists pipeline: baseline → environment → form/injury → final projection.

Run from repository root:
  python tools/print_assists_pipeline.py
  python tools/print_assists_pipeline.py --questionable

Uses a Cade-shaped elite PG and a slightly tough-on-PG defense profile (not league-worst).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Repo root on path when executed as script
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from domain.entities import Game, Player, TeamDefense
from domain.enums import InjuryStatus, PlayerRole, Position
from models.assists_model import AssistsModel


def _elite_pg(status: InjuryStatus) -> Player:
    return Player(
        player_id="cade_like",
        name="Elite PG (Cade-shaped)",
        team_id="det",
        team_abbr="DET",
        position=Position.PG,
        role=PlayerRole.STARTER,
        injury_status=status,
        minutes_per_game=33.9,
        points_per_game=24.0,
        rebounds_per_game=6.0,
        assists_per_game=9.9,
        last10_assists=[8.0, 9.0, 10.0, 11.0, 9.0, 10.0, 9.0, 10.0, 11.0, 9.0],
        last10_minutes=[34.0] * 10,
        is_starter=True,
    )


def _orlando_like_defense() -> TeamDefense:
    """Slightly below-league PG assists allowed (~6.4) — modest matchup drag, not a collapse."""
    return TeamDefense(
        team_id="orl",
        team_abbr="ORL",
        pace=99.0,
        defensive_efficiency=112.0,
        ast_allowed_pg=6.0,
        pts_allowed_pg=21.5,
        turnovers_forced_per_game=14.0,
        fpa_pg=44.0,
        fpa_sg=41.0,
        fpa_sf=39.0,
        fpa_pf=42.0,
        fpa_c=45.0,
    )


def _game() -> Game:
    return Game(
        game_id="det_orl",
        home_team_id="orl",
        home_team_abbr="ORL",
        away_team_id="det",
        away_team_abbr="DET",
        blowout_risk=0.08,
        is_back_to_back_away=False,
        is_back_to_back_home=False,
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--questionable",
        action="store_true",
        help="Set injury to QUESTIONABLE (no elite baseline anchor / elite guard floor).",
    )
    args = p.parse_args()
    status = InjuryStatus.QUESTIONABLE if args.questionable else InjuryStatus.ACTIVE

    player = _elite_pg(status)
    defense = _orlando_like_defense()
    game = _game()
    m = AssistsModel()
    sp = m.project(player, game, defense, is_home=False)

    print(f"Injury: {player.injury_status.value}")
    print(f"Season APG: {player.assists_per_game:.1f} | Season MPG: {player.minutes_per_game:.1f}")
    print()
    print(f"  blended baseline:     {sp.baseline_projection:.3f}")
    print(f"  expected_minutes:     {sp.expected_minutes:.1f}")
    print(f"  pace_factor:          {sp.pace_factor:.4f}")
    print(f"  matchup (def_factor): {sp.defense_factor:.4f}  (raw pos DvP before elite dampen)")
    print(f"  matchup (combined):   {sp.matchup_factor:.4f}  (context: pos * FPA * tov clamp)")
    print(f"  fpa_factor:           {sp.fpa_factor:.4f}")
    print(f"  environment_mult:     {sp.environment_multiplier:.4f}  (geom pace * context, capped)")
    print(f"  form_factor:          {sp.recent_form_factor:.4f}")
    print(f"  injury_factor:        {sp.injury_factor:.4f}")
    print()
    print(f"  ** projected assists: {sp.projected_value:.3f} **")


if __name__ == "__main__":
    main()
