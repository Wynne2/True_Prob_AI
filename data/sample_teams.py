"""
Realistic NBA team defensive profiles for sample data.

Values are approximate 2024-25 season estimates including:
- Points allowed per position
- Rebounds/assists/threes allowed per position
- Fantasy points allowed (DraftKings) per position
- Pace, defensive efficiency, paint/perimeter splits
"""

from __future__ import annotations

from domain.entities import Team, TeamDefense
from domain.enums import DataSource


def _td(team_id: str, abbr: str, **kw) -> TeamDefense:
    return TeamDefense(team_id=team_id, team_abbr=abbr, data_source=DataSource.SAMPLE, **kw)


def _t(team_id: str, abbr: str, city: str, name: str, conf: str, div: str,
       w: int, l: int, implied: float) -> Team:
    return Team(
        team_id=team_id, team_abbr=abbr, city=city, name=name,
        conference=conf, division=div, wins=w, losses=l,
        implied_total=implied, data_source=DataSource.SAMPLE
    )


# ---------------------------------------------------------------------------
# Team entities
# ---------------------------------------------------------------------------

SAMPLE_TEAMS: list[Team] = [
    _t("t_bos", "BOS", "Boston", "Celtics", "East", "Atlantic", 52, 18, 116.5),
    _t("t_mia", "MIA", "Miami", "Heat", "East", "Southeast", 38, 33, 108.0),
    _t("t_den", "DEN", "Denver", "Nuggets", "West", "Northwest", 48, 22, 114.0),
    _t("t_min", "MIN", "Minnesota", "Timberwolves", "West", "Northwest", 46, 24, 110.5),
    _t("t_nyk", "NYK", "New York", "Knicks", "East", "Atlantic", 43, 28, 111.5),
    _t("t_okc", "OKC", "Oklahoma City", "Thunder", "West", "Northwest", 54, 16, 115.5),
    _t("t_gsw", "GSW", "Golden State", "Warriors", "West", "Pacific", 40, 30, 112.0),
    _t("t_lal", "LAL", "Los Angeles", "Lakers", "West", "Pacific", 42, 28, 112.5),
    _t("t_phx", "PHX", "Phoenix", "Suns", "West", "Pacific", 36, 35, 109.0),
    _t("t_phi", "PHI", "Philadelphia", "76ers", "East", "Atlantic", 37, 33, 109.5),
    _t("t_sac", "SAC", "Sacramento", "Kings", "West", "Pacific", 37, 33, 110.0),
    _t("t_dal", "DAL", "Dallas", "Mavericks", "West", "Southwest", 39, 32, 111.0),
    _t("t_nop", "NOP", "New Orleans", "Pelicans", "West", "Southwest", 34, 36, 108.5),
    _t("t_atl", "ATL", "Atlanta", "Hawks", "East", "Southeast", 33, 37, 109.5),
]

TEAM_BY_ID: dict[str, Team] = {t.team_id: t for t in SAMPLE_TEAMS}
TEAM_BY_ABBR: dict[str, Team] = {t.team_abbr: t for t in SAMPLE_TEAMS}


# ---------------------------------------------------------------------------
# Team defensive profiles
# ---------------------------------------------------------------------------

SAMPLE_TEAM_DEFENSE: list[TeamDefense] = [
    # Boston Celtics – elite defense
    _td("t_bos", "BOS",
        pts_allowed_pg=19.5, pts_allowed_sg=18.0, pts_allowed_sf=20.0,
        pts_allowed_pf=17.5, pts_allowed_c=22.0,
        reb_allowed_pg=2.8, reb_allowed_sg=3.2, reb_allowed_sf=4.5,
        reb_allowed_pf=7.8, reb_allowed_c=10.2,
        ast_allowed_pg=5.8, ast_allowed_sg=2.8, ast_allowed_sf=2.2,
        ast_allowed_pf=2.0, ast_allowed_c=2.5,
        threes_allowed_pg=2.1, threes_allowed_sg=2.0, threes_allowed_sf=1.8,
        threes_allowed_pf=0.8, threes_allowed_c=0.3,
        blocks_allowed_per_game=4.2, steals_forced_per_game=7.8, turnovers_forced_per_game=14.2,
        defensive_efficiency=107.8, pace=99.2,
        paint_pts_allowed=42.5, perimeter_pts_allowed=28.5,
        fast_break_pts_allowed=10.2, second_chance_pts_allowed=9.8,
        fpa_pg=31.2, fpa_sg=28.8, fpa_sf=29.5, fpa_pf=30.5, fpa_c=33.8),

    # Miami Heat – above-average defense
    _td("t_mia", "MIA",
        pts_allowed_pg=21.5, pts_allowed_sg=20.0, pts_allowed_sf=21.5,
        pts_allowed_pf=19.5, pts_allowed_c=23.5,
        reb_allowed_pg=3.0, reb_allowed_sg=3.5, reb_allowed_sf=5.0,
        reb_allowed_pf=8.2, reb_allowed_c=10.8,
        ast_allowed_pg=6.2, ast_allowed_sg=3.0, ast_allowed_sf=2.5,
        ast_allowed_pf=2.2, ast_allowed_c=2.8,
        threes_allowed_pg=2.3, threes_allowed_sg=2.2, threes_allowed_sf=1.9,
        threes_allowed_pf=0.9, threes_allowed_c=0.4,
        blocks_allowed_per_game=4.8, steals_forced_per_game=8.5, turnovers_forced_per_game=15.0,
        defensive_efficiency=110.5, pace=99.8,
        paint_pts_allowed=46.0, perimeter_pts_allowed=30.5,
        fast_break_pts_allowed=11.5, second_chance_pts_allowed=10.5,
        fpa_pg=33.5, fpa_sg=31.0, fpa_sf=31.5, fpa_pf=32.5, fpa_c=36.0),

    # Denver Nuggets – average defense, high pace
    _td("t_den", "DEN",
        pts_allowed_pg=24.0, pts_allowed_sg=22.0, pts_allowed_sf=23.5,
        pts_allowed_pf=21.0, pts_allowed_c=25.5,
        reb_allowed_pg=3.5, reb_allowed_sg=4.0, reb_allowed_sf=5.5,
        reb_allowed_pf=8.8, reb_allowed_c=11.5,
        ast_allowed_pg=6.8, ast_allowed_sg=3.4, ast_allowed_sf=2.8,
        ast_allowed_pf=2.5, ast_allowed_c=3.0,
        threes_allowed_pg=2.6, threes_allowed_sg=2.5, threes_allowed_sf=2.2,
        threes_allowed_pf=1.1, threes_allowed_c=0.5,
        blocks_allowed_per_game=5.5, steals_forced_per_game=6.8, turnovers_forced_per_game=13.5,
        defensive_efficiency=113.2, pace=101.5,
        paint_pts_allowed=50.5, perimeter_pts_allowed=33.0,
        fast_break_pts_allowed=13.0, second_chance_pts_allowed=11.5,
        fpa_pg=36.5, fpa_sg=33.8, fpa_sf=34.0, fpa_pf=34.5, fpa_c=38.5),

    # Minnesota Timberwolves – elite defense
    _td("t_min", "MIN",
        pts_allowed_pg=18.5, pts_allowed_sg=17.5, pts_allowed_sf=19.0,
        pts_allowed_pf=17.0, pts_allowed_c=21.5,
        reb_allowed_pg=2.6, reb_allowed_sg=3.0, reb_allowed_sf=4.2,
        reb_allowed_pf=7.5, reb_allowed_c=9.8,
        ast_allowed_pg=5.5, ast_allowed_sg=2.6, ast_allowed_sf=2.1,
        ast_allowed_pf=1.9, ast_allowed_c=2.4,
        threes_allowed_pg=2.0, threes_allowed_sg=1.9, threes_allowed_sf=1.7,
        threes_allowed_pf=0.7, threes_allowed_c=0.3,
        blocks_allowed_per_game=4.0, steals_forced_per_game=8.2, turnovers_forced_per_game=14.5,
        defensive_efficiency=106.8, pace=98.5,
        paint_pts_allowed=40.5, perimeter_pts_allowed=27.5,
        fast_break_pts_allowed=9.8, second_chance_pts_allowed=9.2,
        fpa_pg=30.2, fpa_sg=28.0, fpa_sf=28.8, fpa_pf=29.8, fpa_c=32.5),

    # New York Knicks – above-average defense
    _td("t_nyk", "NYK",
        pts_allowed_pg=22.0, pts_allowed_sg=20.5, pts_allowed_sf=22.0,
        pts_allowed_pf=20.0, pts_allowed_c=24.0,
        reb_allowed_pg=3.1, reb_allowed_sg=3.5, reb_allowed_sf=5.1,
        reb_allowed_pf=8.3, reb_allowed_c=10.9,
        ast_allowed_pg=6.3, ast_allowed_sg=3.1, ast_allowed_sf=2.5,
        ast_allowed_pf=2.2, ast_allowed_c=2.7,
        threes_allowed_pg=2.3, threes_allowed_sg=2.2, threes_allowed_sf=2.0,
        threes_allowed_pf=0.9, threes_allowed_c=0.4,
        blocks_allowed_per_game=4.9, steals_forced_per_game=7.9, turnovers_forced_per_game=14.3,
        defensive_efficiency=111.0, pace=100.0,
        paint_pts_allowed=47.0, perimeter_pts_allowed=31.0,
        fast_break_pts_allowed=11.8, second_chance_pts_allowed=10.8,
        fpa_pg=34.0, fpa_sg=31.5, fpa_sf=32.0, fpa_pf=33.0, fpa_c=36.5),

    # Oklahoma City Thunder – solid defense
    _td("t_okc", "OKC",
        pts_allowed_pg=20.5, pts_allowed_sg=19.0, pts_allowed_sf=20.5,
        pts_allowed_pf=18.5, pts_allowed_c=22.5,
        reb_allowed_pg=2.9, reb_allowed_sg=3.3, reb_allowed_sf=4.7,
        reb_allowed_pf=7.9, reb_allowed_c=10.4,
        ast_allowed_pg=6.0, ast_allowed_sg=2.9, ast_allowed_sf=2.3,
        ast_allowed_pf=2.1, ast_allowed_c=2.6,
        threes_allowed_pg=2.2, threes_allowed_sg=2.0, threes_allowed_sf=1.8,
        threes_allowed_pf=0.8, threes_allowed_c=0.4,
        blocks_allowed_per_game=4.5, steals_forced_per_game=8.0, turnovers_forced_per_game=14.8,
        defensive_efficiency=108.5, pace=99.5,
        paint_pts_allowed=44.0, perimeter_pts_allowed=29.0,
        fast_break_pts_allowed=10.5, second_chance_pts_allowed=10.0,
        fpa_pg=32.0, fpa_sg=29.5, fpa_sf=30.2, fpa_pf=31.2, fpa_c=34.5),

    # Golden State Warriors – average defense
    _td("t_gsw", "GSW",
        pts_allowed_pg=23.5, pts_allowed_sg=21.5, pts_allowed_sf=23.0,
        pts_allowed_pf=20.5, pts_allowed_c=24.5,
        reb_allowed_pg=3.3, reb_allowed_sg=3.8, reb_allowed_sf=5.3,
        reb_allowed_pf=8.6, reb_allowed_c=11.2,
        ast_allowed_pg=6.5, ast_allowed_sg=3.3, ast_allowed_sf=2.7,
        ast_allowed_pf=2.4, ast_allowed_c=2.9,
        threes_allowed_pg=2.5, threes_allowed_sg=2.4, threes_allowed_sf=2.1,
        threes_allowed_pf=1.0, threes_allowed_c=0.5,
        blocks_allowed_per_game=5.2, steals_forced_per_game=7.0, turnovers_forced_per_game=13.8,
        defensive_efficiency=112.0, pace=101.0,
        paint_pts_allowed=48.5, perimeter_pts_allowed=32.0,
        fast_break_pts_allowed=12.5, second_chance_pts_allowed=11.0,
        fpa_pg=35.5, fpa_sg=33.0, fpa_sf=33.5, fpa_pf=34.0, fpa_c=37.5),

    # Los Angeles Lakers – slightly above average defense
    _td("t_lal", "LAL",
        pts_allowed_pg=22.5, pts_allowed_sg=21.0, pts_allowed_sf=22.5,
        pts_allowed_pf=20.0, pts_allowed_c=24.5,
        reb_allowed_pg=3.2, reb_allowed_sg=3.6, reb_allowed_sf=5.2,
        reb_allowed_pf=8.4, reb_allowed_c=11.0,
        ast_allowed_pg=6.4, ast_allowed_sg=3.2, ast_allowed_sf=2.6,
        ast_allowed_pf=2.3, ast_allowed_c=2.8,
        threes_allowed_pg=2.4, threes_allowed_sg=2.3, threes_allowed_sf=2.0,
        threes_allowed_pf=0.9, threes_allowed_c=0.4,
        blocks_allowed_per_game=5.0, steals_forced_per_game=7.5, turnovers_forced_per_game=14.0,
        defensive_efficiency=111.5, pace=100.5,
        paint_pts_allowed=47.5, perimeter_pts_allowed=31.5,
        fast_break_pts_allowed=12.0, second_chance_pts_allowed=10.8,
        fpa_pg=34.5, fpa_sg=32.0, fpa_sf=32.5, fpa_pf=33.5, fpa_c=37.0),

    # Phoenix Suns – below average defense
    _td("t_phx", "PHX",
        pts_allowed_pg=25.5, pts_allowed_sg=23.5, pts_allowed_sf=25.0,
        pts_allowed_pf=22.5, pts_allowed_c=26.5,
        reb_allowed_pg=3.7, reb_allowed_sg=4.2, reb_allowed_sf=5.8,
        reb_allowed_pf=9.0, reb_allowed_c=11.8,
        ast_allowed_pg=7.0, ast_allowed_sg=3.6, ast_allowed_sf=2.9,
        ast_allowed_pf=2.6, ast_allowed_c=3.1,
        threes_allowed_pg=2.8, threes_allowed_sg=2.7, threes_allowed_sf=2.4,
        threes_allowed_pf=1.2, threes_allowed_c=0.6,
        blocks_allowed_per_game=5.8, steals_forced_per_game=6.5, turnovers_forced_per_game=13.0,
        defensive_efficiency=115.0, pace=102.0,
        paint_pts_allowed=52.0, perimeter_pts_allowed=34.5,
        fast_break_pts_allowed=14.0, second_chance_pts_allowed=12.0,
        fpa_pg=38.0, fpa_sg=35.5, fpa_sf=36.0, fpa_pf=36.5, fpa_c=40.0),

    # Philadelphia 76ers – average defense
    _td("t_phi", "PHI",
        pts_allowed_pg=23.0, pts_allowed_sg=21.5, pts_allowed_sf=23.0,
        pts_allowed_pf=21.0, pts_allowed_c=25.0,
        reb_allowed_pg=3.3, reb_allowed_sg=3.7, reb_allowed_sf=5.3,
        reb_allowed_pf=8.5, reb_allowed_c=11.0,
        ast_allowed_pg=6.5, ast_allowed_sg=3.2, ast_allowed_sf=2.6,
        ast_allowed_pf=2.3, ast_allowed_c=2.8,
        threes_allowed_pg=2.5, threes_allowed_sg=2.4, threes_allowed_sf=2.1,
        threes_allowed_pf=1.0, threes_allowed_c=0.5,
        blocks_allowed_per_game=5.2, steals_forced_per_game=7.2, turnovers_forced_per_game=14.0,
        defensive_efficiency=112.5, pace=100.8,
        paint_pts_allowed=49.0, perimeter_pts_allowed=32.5,
        fast_break_pts_allowed=12.8, second_chance_pts_allowed=11.2,
        fpa_pg=35.8, fpa_sg=33.2, fpa_sf=33.8, fpa_pf=34.5, fpa_c=38.0),

    # Sacramento Kings – weak defense
    _td("t_sac", "SAC",
        pts_allowed_pg=26.0, pts_allowed_sg=24.0, pts_allowed_sf=25.5,
        pts_allowed_pf=23.0, pts_allowed_c=27.0,
        reb_allowed_pg=3.8, reb_allowed_sg=4.3, reb_allowed_sf=6.0,
        reb_allowed_pf=9.2, reb_allowed_c=12.0,
        ast_allowed_pg=7.2, ast_allowed_sg=3.7, ast_allowed_sf=3.0,
        ast_allowed_pf=2.7, ast_allowed_c=3.2,
        threes_allowed_pg=2.9, threes_allowed_sg=2.8, threes_allowed_sf=2.5,
        threes_allowed_pf=1.3, threes_allowed_c=0.6,
        blocks_allowed_per_game=5.9, steals_forced_per_game=6.3, turnovers_forced_per_game=12.8,
        defensive_efficiency=116.5, pace=103.0,
        paint_pts_allowed=54.0, perimeter_pts_allowed=35.5,
        fast_break_pts_allowed=14.5, second_chance_pts_allowed=12.5,
        fpa_pg=39.5, fpa_sg=37.0, fpa_sf=37.5, fpa_pf=38.0, fpa_c=41.5),

    # Dallas Mavericks – average defense
    _td("t_dal", "DAL",
        pts_allowed_pg=22.8, pts_allowed_sg=21.2, pts_allowed_sf=22.8,
        pts_allowed_pf=20.5, pts_allowed_c=24.8,
        reb_allowed_pg=3.2, reb_allowed_sg=3.6, reb_allowed_sf=5.2,
        reb_allowed_pf=8.5, reb_allowed_c=11.1,
        ast_allowed_pg=6.4, ast_allowed_sg=3.2, ast_allowed_sf=2.6,
        ast_allowed_pf=2.3, ast_allowed_c=2.8,
        threes_allowed_pg=2.4, threes_allowed_sg=2.3, threes_allowed_sf=2.0,
        threes_allowed_pf=0.9, threes_allowed_c=0.4,
        blocks_allowed_per_game=5.1, steals_forced_per_game=7.4, turnovers_forced_per_game=14.1,
        defensive_efficiency=112.0, pace=100.6,
        paint_pts_allowed=48.0, perimeter_pts_allowed=31.8,
        fast_break_pts_allowed=12.2, second_chance_pts_allowed=10.9,
        fpa_pg=35.0, fpa_sg=32.5, fpa_sf=33.0, fpa_pf=34.0, fpa_c=37.2),

    # New Orleans Pelicans – slightly below average
    _td("t_nop", "NOP",
        pts_allowed_pg=24.5, pts_allowed_sg=22.5, pts_allowed_sf=24.0,
        pts_allowed_pf=21.5, pts_allowed_c=26.0,
        reb_allowed_pg=3.5, reb_allowed_sg=4.0, reb_allowed_sf=5.6,
        reb_allowed_pf=8.9, reb_allowed_c=11.6,
        ast_allowed_pg=6.7, ast_allowed_sg=3.4, ast_allowed_sf=2.7,
        ast_allowed_pf=2.4, ast_allowed_c=2.9,
        threes_allowed_pg=2.6, threes_allowed_sg=2.5, threes_allowed_sf=2.2,
        threes_allowed_pf=1.1, threes_allowed_c=0.5,
        blocks_allowed_per_game=5.5, steals_forced_per_game=7.0, turnovers_forced_per_game=13.5,
        defensive_efficiency=113.8, pace=101.2,
        paint_pts_allowed=51.0, perimeter_pts_allowed=33.5,
        fast_break_pts_allowed=13.5, second_chance_pts_allowed=11.8,
        fpa_pg=37.0, fpa_sg=34.5, fpa_sf=35.0, fpa_pf=35.5, fpa_c=39.5),

    # Atlanta Hawks – weak defense, high pace
    _td("t_atl", "ATL",
        pts_allowed_pg=27.0, pts_allowed_sg=25.0, pts_allowed_sf=26.5,
        pts_allowed_pf=24.0, pts_allowed_c=28.0,
        reb_allowed_pg=3.9, reb_allowed_sg=4.4, reb_allowed_sf=6.2,
        reb_allowed_pf=9.4, reb_allowed_c=12.2,
        ast_allowed_pg=7.5, ast_allowed_sg=3.8, ast_allowed_sf=3.1,
        ast_allowed_pf=2.8, ast_allowed_c=3.3,
        threes_allowed_pg=3.0, threes_allowed_sg=2.9, threes_allowed_sf=2.6,
        threes_allowed_pf=1.4, threes_allowed_c=0.7,
        blocks_allowed_per_game=6.0, steals_forced_per_game=6.0, turnovers_forced_per_game=12.5,
        defensive_efficiency=117.5, pace=103.5,
        paint_pts_allowed=55.0, perimeter_pts_allowed=36.5,
        fast_break_pts_allowed=15.0, second_chance_pts_allowed=13.0,
        fpa_pg=40.5, fpa_sg=38.0, fpa_sf=38.5, fpa_pf=39.0, fpa_c=43.0),
]

DEFENSE_BY_TEAM_ID: dict[str, TeamDefense] = {d.team_id: d for d in SAMPLE_TEAM_DEFENSE}
DEFENSE_BY_TEAM_ABBR: dict[str, TeamDefense] = {d.team_abbr: d for d in SAMPLE_TEAM_DEFENSE}
