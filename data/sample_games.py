"""
Sample game slate and odds data for today's NBA games.

Games, matchups, and multi-book odds are generated relative to the
current date so the pipeline always works immediately with sample data.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from domain.entities import Game, OddsLine
from domain.enums import BookName, DataSource, PropType


def _make_game(
    game_id: str,
    home: str,
    home_id: str,
    away: str,
    away_id: str,
    tip_hour: int,
    total: float,
    spread: float,
    home_impl: float,
    away_impl: float,
    blowout: float = 0.15,
    b2b_home: bool = False,
    b2b_away: bool = False,
) -> Game:
    today = date.today()
    tip_time = datetime(today.year, today.month, today.day, tip_hour, 0, tzinfo=timezone.utc)
    return Game(
        game_id=game_id,
        home_team_id=home_id,
        home_team_abbr=home,
        away_team_id=away_id,
        away_team_abbr=away,
        game_date=today,
        tip_off_time=tip_time,
        status="Scheduled",
        game_total=total,
        home_spread=spread,
        home_implied_total=home_impl,
        away_implied_total=away_impl,
        blowout_risk=blowout,
        is_back_to_back_home=b2b_home,
        is_back_to_back_away=b2b_away,
        data_source=DataSource.SAMPLE,
    )


def get_sample_games() -> list[Game]:
    """Return today's sample game slate (7 games)."""
    return [
        _make_game("g_bos_mia", "BOS", "t_bos", "MIA", "t_mia", 0,
                   total=218.5, spread=-6.5, home_impl=112.5, away_impl=106.0, blowout=0.20),
        _make_game("g_den_min", "DEN", "t_den", "MIN", "t_min", 1,
                   total=220.0, spread=-2.5, home_impl=111.5, away_impl=108.5, blowout=0.12),
        _make_game("g_nyk_phi", "NYK", "t_nyk", "PHI", "t_phi", 0,
                   total=215.5, spread=-3.5, home_impl=109.5, away_impl=106.0, blowout=0.15),
        _make_game("g_okc_gsw", "OKC", "t_okc", "GSW", "t_gsw", 3,
                   total=222.0, spread=-5.5, home_impl=113.5, away_impl=108.5, blowout=0.18),
        _make_game("g_lal_phx", "LAL", "t_lal", "PHX", "t_phx", 3,
                   total=225.0, spread=-1.5, home_impl=113.0, away_impl=112.0, blowout=0.10),
        _make_game("g_sac_dal", "SAC", "t_sac", "DAL", "t_dal", 3,
                   total=218.0, spread=1.5, home_impl=108.5, away_impl=109.5, blowout=0.12),
        _make_game("g_nop_atl", "NOP", "t_nop", "ATL", "t_atl", 0,
                   total=222.5, spread=-2.0, home_impl=112.5, away_impl=110.0, blowout=0.13),
    ]


# ---------------------------------------------------------------------------
# Sample multi-book odds
# ---------------------------------------------------------------------------

def _line(book: str, pid: str, pname: str, prop: PropType,
          line: float, over: int, under: int,
          game_id: str, team: str, opp: str) -> OddsLine:
    return OddsLine(
        book=BookName(book),
        player_id=pid,
        player_name=pname,
        prop_type=prop,
        line=line,
        over_odds=over,
        under_odds=under,
        game_id=game_id,
        team_abbr=team,
        opponent_abbr=opp,
        timestamp=datetime.now(timezone.utc),
        data_source=DataSource.SAMPLE,
    )


def get_sample_odds() -> list[OddsLine]:  # noqa: C901
    """Return a realistic multi-book odds set for all players on today's slate."""
    lines: list[OddsLine] = []

    # ── Jayson Tatum – BOS vs MIA ───────────────────────────────────────────
    for book, over, under in [
        ("fanduel", -115, -105), ("draftkings", -118, -102),
        ("betmgm", -112, -108), ("caesars", -115, -105),
    ]:
        lines.append(_line(book, "p_tatum", "Jayson Tatum", PropType.POINTS,
                           27.5, over, under, "g_bos_mia", "BOS", "MIA"))
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -112, -108),
        ("betmgm", -108, -112), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_tatum", "Jayson Tatum", PropType.REBOUNDS,
                           7.5, over, under, "g_bos_mia", "BOS", "MIA"))
    for book, over, under in [
        ("fanduel", -115, -105), ("draftkings", -110, -110),
        ("betmgm", -112, -108), ("caesars", -115, -105),
    ]:
        lines.append(_line(book, "p_tatum", "Jayson Tatum", PropType.ASSISTS,
                           4.5, over, under, "g_bos_mia", "BOS", "MIA"))
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -108, -112),
        ("betmgm", -110, -110), ("caesars", -112, -108),
    ]:
        lines.append(_line(book, "p_tatum", "Jayson Tatum", PropType.THREES,
                           2.5, over, under, "g_bos_mia", "BOS", "MIA"))

    # ── Jaylen Brown – BOS vs MIA ──────────────────────────────────────────
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -112, -108),
        ("betmgm", -108, -112), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_brown", "Jaylen Brown", PropType.POINTS,
                           22.5, over, under, "g_bos_mia", "BOS", "MIA"))
    for book, over, under in [
        ("fanduel", -115, -105), ("draftkings", -110, -110),
        ("betmgm", -112, -108), ("caesars", -115, -105),
    ]:
        lines.append(_line(book, "p_brown", "Jaylen Brown", PropType.THREES,
                           2.5, over, under, "g_bos_mia", "BOS", "MIA"))

    # ── Bam Adebayo – MIA vs BOS ───────────────────────────────────────────
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -112, -108),
        ("betmgm", -108, -112), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_adebayo", "Bam Adebayo", PropType.POINTS,
                           18.5, over, under, "g_bos_mia", "MIA", "BOS"))
    for book, over, under in [
        ("fanduel", -115, -105), ("draftkings", -110, -110),
        ("betmgm", -112, -108), ("caesars", -118, -102),
    ]:
        lines.append(_line(book, "p_adebayo", "Bam Adebayo", PropType.REBOUNDS,
                           9.5, over, under, "g_bos_mia", "MIA", "BOS"))
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -108, -112),
        ("betmgm", -110, -110), ("caesars", -112, -108),
    ]:
        lines.append(_line(book, "p_adebayo", "Bam Adebayo", PropType.ASSISTS,
                           3.5, over, under, "g_bos_mia", "MIA", "BOS"))

    # ── Nikola Jokic – DEN vs MIN ──────────────────────────────────────────
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -112, -108),
        ("betmgm", -108, -112), ("caesars", -110, -110),
        ("pointsbet", -112, -108), ("betrivers", -110, -110),
    ]:
        lines.append(_line(book, "p_jokic", "Nikola Jokic", PropType.POINTS,
                           25.5, over, under, "g_den_min", "DEN", "MIN"))
    for book, over, under in [
        ("fanduel", -115, -105), ("draftkings", -110, -110),
        ("betmgm", -118, -102), ("caesars", -112, -108),
        ("pointsbet", -115, -105), ("betrivers", -112, -108),
    ]:
        lines.append(_line(book, "p_jokic", "Nikola Jokic", PropType.REBOUNDS,
                           11.5, over, under, "g_den_min", "DEN", "MIN"))
    for book, over, under in [
        ("fanduel", -108, -112), ("draftkings", -110, -110),
        ("betmgm", -108, -112), ("caesars", -110, -110),
        ("pointsbet", -108, -112), ("betrivers", -110, -110),
    ]:
        lines.append(_line(book, "p_jokic", "Nikola Jokic", PropType.ASSISTS,
                           8.5, over, under, "g_den_min", "DEN", "MIN"))

    # ── Jamal Murray – DEN vs MIN ─────────────────────────────────────────
    for book, over, under in [
        ("fanduel", -112, -108), ("draftkings", -110, -110),
        ("betmgm", -112, -108), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_murray", "Jamal Murray", PropType.POINTS,
                           21.5, over, under, "g_den_min", "DEN", "MIN"))
    for book, over, under in [
        ("fanduel", -108, -112), ("draftkings", -110, -110),
        ("betmgm", -108, -112), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_murray", "Jamal Murray", PropType.ASSISTS,
                           6.5, over, under, "g_den_min", "DEN", "MIN"))
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -112, -108),
        ("betmgm", -108, -112), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_murray", "Jamal Murray", PropType.THREES,
                           2.5, over, under, "g_den_min", "DEN", "MIN"))

    # ── Anthony Edwards – MIN vs DEN ──────────────────────────────────────
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -108, -112),
        ("betmgm", -110, -110), ("caesars", -112, -108),
        ("pointsbet", -108, -112), ("betrivers", -110, -110),
    ]:
        lines.append(_line(book, "p_edwards", "Anthony Edwards", PropType.POINTS,
                           25.5, over, under, "g_den_min", "MIN", "DEN"))
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -108, -112),
        ("betmgm", -110, -110), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_edwards", "Anthony Edwards", PropType.THREES,
                           3.5, over, under, "g_den_min", "MIN", "DEN"))
    for book, over, under in [
        ("fanduel", -108, -112), ("draftkings", -110, -110),
        ("betmgm", -108, -112), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_edwards", "Anthony Edwards", PropType.REBOUNDS,
                           5.5, over, under, "g_den_min", "MIN", "DEN"))

    # ── Shai Gilgeous-Alexander – OKC vs GSW ──────────────────────────────
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -112, -108),
        ("betmgm", -108, -112), ("caesars", -110, -110),
        ("pointsbet", -110, -110), ("betrivers", -112, -108),
    ]:
        lines.append(_line(book, "p_gilgeous", "Shai Gilgeous-Alexander",
                           PropType.POINTS, 29.5, over, under, "g_okc_gsw", "OKC", "GSW"))
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -108, -112),
        ("betmgm", -112, -108), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_gilgeous", "Shai Gilgeous-Alexander",
                           PropType.ASSISTS, 5.5, over, under, "g_okc_gsw", "OKC", "GSW"))
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -112, -108),
        ("betmgm", -108, -112), ("caesars", -112, -108),
    ]:
        lines.append(_line(book, "p_gilgeous", "Shai Gilgeous-Alexander",
                           PropType.STEALS, 1.5, over, under, "g_okc_gsw", "OKC", "GSW"))

    # ── Stephen Curry – GSW vs OKC ──────────────────────────────────────
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -108, -112),
        ("betmgm", -110, -110), ("caesars", -108, -112),
    ]:
        lines.append(_line(book, "p_curry", "Stephen Curry",
                           PropType.POINTS, 25.5, over, under, "g_okc_gsw", "GSW", "OKC"))
    for book, over, under in [
        ("fanduel", -108, -112), ("draftkings", -110, -110),
        ("betmgm", -108, -112), ("caesars", -110, -110),
        ("pointsbet", -105, -115), ("betrivers", -108, -112),
    ]:
        lines.append(_line(book, "p_curry", "Stephen Curry",
                           PropType.THREES, 4.5, over, under, "g_okc_gsw", "GSW", "OKC"))

    # ── LeBron James – LAL vs PHX ─────────────────────────────────────────
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -112, -108),
        ("betmgm", -108, -112), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_lebron", "LeBron James",
                           PropType.POINTS, 25.5, over, under, "g_lal_phx", "LAL", "PHX"))
    for book, over, under in [
        ("fanduel", -112, -108), ("draftkings", -110, -110),
        ("betmgm", -112, -108), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_lebron", "LeBron James",
                           PropType.ASSISTS, 7.5, over, under, "g_lal_phx", "LAL", "PHX"))

    # ── Anthony Davis – LAL vs PHX ────────────────────────────────────────
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -108, -112),
        ("betmgm", -110, -110), ("caesars", -112, -108),
        ("pointsbet", -110, -110), ("betrivers", -108, -112),
    ]:
        lines.append(_line(book, "p_davis", "Anthony Davis",
                           PropType.POINTS, 23.5, over, under, "g_lal_phx", "LAL", "PHX"))
    for book, over, under in [
        ("fanduel", -115, -105), ("draftkings", -110, -110),
        ("betmgm", -118, -102), ("caesars", -112, -108),
    ]:
        lines.append(_line(book, "p_davis", "Anthony Davis",
                           PropType.REBOUNDS, 11.5, over, under, "g_lal_phx", "LAL", "PHX"))
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -112, -108),
        ("betmgm", -108, -112), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_davis", "Anthony Davis",
                           PropType.BLOCKS, 1.5, over, under, "g_lal_phx", "LAL", "PHX"))

    # ── Devin Booker – PHX vs LAL ─────────────────────────────────────────
    for book, over, under in [
        ("fanduel", -112, -108), ("draftkings", -110, -110),
        ("betmgm", -112, -108), ("caesars", -115, -105),
    ]:
        lines.append(_line(book, "p_booker", "Devin Booker",
                           PropType.POINTS, 24.5, over, under, "g_lal_phx", "PHX", "LAL"))
    for book, over, under in [
        ("fanduel", -108, -112), ("draftkings", -110, -110),
        ("betmgm", -108, -112), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_booker", "Devin Booker",
                           PropType.ASSISTS, 6.5, over, under, "g_lal_phx", "PHX", "LAL"))

    # ── Joel Embiid – PHI vs NYK ──────────────────────────────────────────
    for book, over, under in [
        ("fanduel", -108, -112), ("draftkings", -110, -110),
        ("betmgm", -108, -112), ("caesars", -110, -110),
        ("pointsbet", -105, -115), ("betrivers", -108, -112),
    ]:
        lines.append(_line(book, "p_embiid", "Joel Embiid",
                           PropType.POINTS, 33.5, over, under, "g_nyk_phi", "PHI", "NYK"))
    for book, over, under in [
        ("fanduel", -115, -105), ("draftkings", -110, -110),
        ("betmgm", -118, -102), ("caesars", -112, -108),
    ]:
        lines.append(_line(book, "p_embiid", "Joel Embiid",
                           PropType.REBOUNDS, 10.5, over, under, "g_nyk_phi", "PHI", "NYK"))

    # ── Karl-Anthony Towns – NYK vs PHI ──────────────────────────────────
    for book, over, under in [
        ("fanduel", -112, -108), ("draftkings", -110, -110),
        ("betmgm", -112, -108), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_towns", "Karl-Anthony Towns",
                           PropType.POINTS, 23.5, over, under, "g_nyk_phi", "NYK", "PHI"))
    for book, over, under in [
        ("fanduel", -115, -105), ("draftkings", -110, -110),
        ("betmgm", -115, -105), ("caesars", -112, -108),
        ("pointsbet", -118, -102), ("betrivers", -112, -108),
    ]:
        lines.append(_line(book, "p_towns", "Karl-Anthony Towns",
                           PropType.REBOUNDS, 12.5, over, under, "g_nyk_phi", "NYK", "PHI"))

    # ── Luka Doncic – DAL vs SAC ─────────────────────────────────────────
    for book, over, under in [
        ("fanduel", -108, -112), ("draftkings", -110, -110),
        ("betmgm", -108, -112), ("caesars", -110, -110),
        ("pointsbet", -105, -115), ("betrivers", -108, -112),
    ]:
        lines.append(_line(book, "p_doncic", "Luka Doncic",
                           PropType.POINTS, 33.5, over, under, "g_sac_dal", "DAL", "SAC"))
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -108, -112),
        ("betmgm", -112, -108), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_doncic", "Luka Doncic",
                           PropType.ASSISTS, 9.5, over, under, "g_sac_dal", "DAL", "SAC"))
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -112, -108),
        ("betmgm", -108, -112), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_doncic", "Luka Doncic",
                           PropType.THREES, 3.5, over, under, "g_sac_dal", "DAL", "SAC"))

    # ── De'Aaron Fox – SAC vs DAL ─────────────────────────────────────────
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -108, -112),
        ("betmgm", -112, -108), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_fox", "De'Aaron Fox",
                           PropType.POINTS, 23.5, over, under, "g_sac_dal", "SAC", "DAL"))
    for book, over, under in [
        ("fanduel", -108, -112), ("draftkings", -110, -110),
        ("betmgm", -108, -112), ("caesars", -108, -112),
    ]:
        lines.append(_line(book, "p_fox", "De'Aaron Fox",
                           PropType.ASSISTS, 5.5, over, under, "g_sac_dal", "SAC", "DAL"))

    # ── Trae Young – ATL vs NOP ──────────────────────────────────────────
    for book, over, under in [
        ("fanduel", -112, -108), ("draftkings", -110, -110),
        ("betmgm", -112, -108), ("caesars", -110, -110),
        ("pointsbet", -110, -110), ("betrivers", -112, -108),
    ]:
        lines.append(_line(book, "p_young", "Trae Young",
                           PropType.POINTS, 25.5, over, under, "g_nop_atl", "ATL", "NOP"))
    for book, over, under in [
        ("fanduel", -108, -112), ("draftkings", -110, -110),
        ("betmgm", -108, -112), ("caesars", -110, -110),
        ("pointsbet", -105, -115), ("betrivers", -108, -112),
    ]:
        lines.append(_line(book, "p_young", "Trae Young",
                           PropType.ASSISTS, 10.5, over, under, "g_nop_atl", "ATL", "NOP"))
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -112, -108),
        ("betmgm", -110, -110), ("caesars", -108, -112),
    ]:
        lines.append(_line(book, "p_young", "Trae Young",
                           PropType.THREES, 2.5, over, under, "g_nop_atl", "ATL", "NOP"))

    # ── Brandon Ingram – NOP vs ATL ──────────────────────────────────────
    for book, over, under in [
        ("fanduel", -110, -110), ("draftkings", -108, -112),
        ("betmgm", -112, -108), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_ingram", "Brandon Ingram",
                           PropType.POINTS, 23.5, over, under, "g_nop_atl", "NOP", "ATL"))
    for book, over, under in [
        ("fanduel", -108, -112), ("draftkings", -110, -110),
        ("betmgm", -108, -112), ("caesars", -110, -110),
    ]:
        lines.append(_line(book, "p_ingram", "Brandon Ingram",
                           PropType.ASSISTS, 5.5, over, under, "g_nop_atl", "NOP", "ATL"))

    return lines
