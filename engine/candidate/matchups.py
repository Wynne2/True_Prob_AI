"""
Matchup Finder: the candidate-discovery workflow, point in time.

    1. Bottom-10 defenses      opponent defensive rating (worst 10 of 30)
    2. Bottom-10 vs position   opponent fantasy points allowed to the player's
                               position group (worst 10 of 30)
    3. Real opportunity        minutes and the stat's own opportunity volume
    4. Probability             the frozen candidate model (no matchup boost added;
                               earlier tests found boosts hurt accuracy)
    5. Price                   break-even probability, edge, expected ROI

Positions: BALLDONTLIE lists G / F / C (and hybrids). Hybrids count toward
both groups (G-F is a guard and a forward). Five-slot PG/SG/SF/PF/C labels
are not used because the data does not support them.

Fantasy points here = PTS + 1.2 REB + 1.5 AST + 0.5 3PM (steals, blocks and
turnovers are not in the historical box data).

Everything is ranked from games strictly before the slate date. Teams with
fewer than MIN_TEAM_GAMES this season are not ranked.

Opportunity thresholds (fixed before any backtest; last 10 games):
    all          minutes >= 26
    points       usage >= 20% and FGA >= 12
    threes       3PA >= 5
    assists      passes >= 40 or assists >= 5
    rebounds     rebound chances >= 12
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from engine.backtest.profiles import team_game_rate

BOTTOM_N = 10
MIN_TEAM_GAMES = 5
GROUPS = ("G", "F", "C")
STATS = ("points", "rebounds", "assists", "threes")
_FIELD = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3m"}


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def groups_of(position: str) -> set[str]:
    token = str(position or "").upper()
    return {g for g in GROUPS if g in token}


def fantasy_points(row: dict) -> float:
    return _num(row.get("pts")) + 1.2 * _num(row.get("reb")) + 1.5 * _num(row.get("ast")) + 0.5 * _num(row.get("fg3m"))


class SeasonState:
    """Running team defense and player opportunity, updated one date at a time."""

    def __init__(self):
        self.drtg: dict[str, list[float]] = defaultdict(list)
        self.fp_allowed: dict[tuple[str, str], list[float]] = defaultdict(list)        # (team, group) -> per game
        self.stat_allowed: dict[tuple[str, str, str], list[float]] = defaultdict(list)  # (team, group, stat)
        self.player: dict[str, list[dict]] = defaultdict(list)

    def update(self, rows: list[dict]) -> None:
        by_tg: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for r in rows:
            if _num(r.get("min")) <= 0:
                continue
            by_tg[(str(r.get("team_id")), str(r.get("game_id")))].append(r)
            self.player[str(r.get("player_id"))].append(r)
        teams_in_game: dict[str, list[str]] = defaultdict(list)
        for team, gid in by_tg:
            teams_in_game[gid].append(team)
        for (team, gid), members in by_tg.items():
            d = team_game_rate(members, "defensive_rating")
            if d:
                self.drtg[team].append(d)
        for gid, teams in teams_in_game.items():
            if len(teams) != 2:
                continue
            for defense, offense in ((teams[0], teams[1]), (teams[1], teams[0])):
                players = by_tg[(offense, gid)]
                for g in GROUPS:
                    in_group = [p for p in players if g in groups_of(p.get("position"))]
                    self.fp_allowed[(defense, g)].append(sum(fantasy_points(p) for p in in_group))
                    for stat, field in _FIELD.items():
                        self.stat_allowed[(defense, g, stat)].append(sum(_num(p.get(field)) for p in in_group))

    # -- ranks -------------------------------------------------------------
    def _bottom(self, values: dict[str, list[float]]) -> dict[str, int]:
        """Rank 1 = worst defense (highest allowed). Only teams with enough games."""
        means = {t: sum(v) / len(v) for t, v in values.items() if len(v) >= MIN_TEAM_GAMES}
        order = sorted(means, key=lambda t: means[t], reverse=True)
        return {t: i + 1 for i, t in enumerate(order)}

    def defense_ranks(self) -> dict[str, int]:
        return self._bottom(self.drtg)

    def group_ranks(self, group: str) -> dict[str, int]:
        return self._bottom({t: v for (t, g), v in self.fp_allowed.items() if g == group})

    def stat_group_ranks(self, group: str, stat: str) -> dict[str, int]:
        return self._bottom({t: v for (t, g, s), v in self.stat_allowed.items() if g == group and s == stat})

    def averages(self, pid: str) -> dict | None:
        rows = self.player.get(pid) or []
        recent = rows[-10:]
        if len(recent) < 5:
            return None
        n = len(recent)
        mean = lambda key: sum(_num(r.get(key)) for r in recent) / n
        usage = [ _num(r.get("usage_percentage")) for r in recent if _num(r.get("usage_percentage")) > 0]
        return {
            "games": n,
            "minutes": mean("min"),
            "usage": (sum(usage) / len(usage)) if usage else 0.0,
            "fga": mean("fga"),
            "fg3a": mean("fg3a"),
            "passes": mean("passes"),
            "ast": mean("ast"),
            "rebound_chances": mean("rebound_chances_total"),
            "position": recent[-1].get("position") or "",
        }


def opportunity_ok(stat: str, avg: dict) -> bool:
    if avg is None or avg["minutes"] < 26:
        return False
    if stat == "points":
        usage = avg["usage"] / 100.0 if avg["usage"] > 1.5 else avg["usage"]
        return usage >= 0.20 and avg["fga"] >= 12
    if stat == "threes":
        return avg["fg3a"] >= 5
    if stat == "assists":
        return avg["passes"] >= 40 or avg["ast"] >= 5
    if stat == "rebounds":
        return avg["rebound_chances"] >= 12
    return False


def screen(state: SeasonState, pid: str, opponent: str, stat: str) -> dict:
    """Workflow steps 1-3 for one player, stat and opponent, from the state as it stands."""
    avg = state.averages(pid)
    d_rank = state.defense_ranks().get(opponent)
    groups = groups_of((avg or {}).get("position", ""))
    g_ranks = [state.group_ranks(g).get(opponent) for g in groups]
    g_ranks = [r for r in g_ranks if r is not None]
    s_ranks = [state.stat_group_ranks(g, stat).get(opponent) for g in groups]
    s_ranks = [r for r in s_ranks if r is not None]
    step1 = d_rank is not None and d_rank <= BOTTOM_N
    step2 = bool(g_ranks) and min(g_ranks) <= BOTTOM_N
    step3 = opportunity_ok(stat, avg)
    return {
        "defense_rank": d_rank,
        "position_groups": "/".join(sorted(groups)),
        "position_fp_rank": min(g_ranks) if g_ranks else None,
        "position_stat_rank": min(s_ranks) if s_ranks else None,
        "step1_bottom10_defense": step1,
        "step2_bottom10_vs_position": step2,
        "step3_opportunity": step3,
        "passes_screen": step1 and step2 and step3,
        "averages": avg,
    }


def screen_lines(logs: list[dict], lines: list[dict], season: str) -> None:
    """Annotate saved sportsbook lines (dicts with game_date, player_id, prop_type, team ids) in place."""
    rows = sorted((r for r in logs if r.get("season") == season and r.get("game_date")), key=lambda r: r["game_date"])
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_date[r["game_date"]].append(r)
    opponent_of = {(str(r.get("player_id")), str(r.get("game_date"))): str(r.get("opponent_team_id") or "") for r in rows}
    wanted: dict[str, list[dict]] = defaultdict(list)
    for line in lines:
        wanted[line["game_date"]].append(line)
    state = SeasonState()
    cache: dict = {}
    for day in sorted(set(by_date) | set(wanted)):
        for line in wanted.get(day, []):
            pid = str(line["player_id"])
            stat = str(line["prop_type"]).lower()
            opp = opponent_of.get((pid, day), "")
            key = (pid, opp, stat, day)
            if key not in cache:
                cache[key] = screen(state, pid, opp, stat) if opp else None
            line["screen"] = cache[key]
        state.update(by_date.get(day, []))
