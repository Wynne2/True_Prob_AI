"""
Rebuild player and opponent inputs from box scores completed before tip-off.

Current season-average endpoints are not used. Those totals include games
played after the historical date.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from statistics import mean

from domain.constants import LEAGUE_AVG_DEF_EFF, LEAGUE_AVG_PACE
from domain.entities import Game, Player, TeamDefense
from domain.enums import DataSource, PlayerRole, Position
from models.matchup_model import shrink_allowed

_POSITION_BUCKET = {
    "PG": "PG", "SG": "SG", "SF": "SF", "PF": "PF", "C": "C",
    "G": "PG", "GF": "SG", "F": "SF", "FC": "PF",
}
_BASELINES = {
    "pts": {"PG": 22.0, "SG": 20.5, "SF": 21.5, "PF": 19.5, "C": 24.0},
    "reb": {"PG": 3.2, "SG": 3.7, "SF": 5.2, "PF": 8.5, "C": 11.0},
    "ast": {"PG": 6.4, "SG": 3.1, "SF": 2.6, "PF": 2.3, "C": 2.8},
    "fg3": {"PG": 2.4, "SG": 2.3, "SF": 2.0, "PF": 0.9, "C": 0.4},
}


def log_date(row: dict) -> date | None:
    raw = str(row.get("game_date") or "")[:10]
    if len(raw) != 10:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def logs_before(logs: list[dict], game_day: date) -> list[dict]:
    """Drop every log on or after the game date. Same-day games have not finished."""
    kept = []
    for row in logs:
        played = log_date(row)
        if played is not None and played < game_day:
            kept.append(row)
    return kept


def normalize_player_name(name: str) -> str:
    text = name.lower().replace("'", "").replace(".", "").replace("-", " ")
    for suffix in (" jr", " sr", " iii", " ii", " iv"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return " ".join(text.split())


def names_match(left: str, right: str) -> bool:
    a = normalize_player_name(left)
    b = normalize_player_name(right)
    if not a or not b:
        return False
    if a == b:
        return True
    a_parts = a.split()
    b_parts = b.split()
    return len(a_parts) >= 2 and len(b_parts) >= 2 and a_parts[0] == b_parts[0] and a_parts[-1] == b_parts[-1]


def _num(value) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _position(raw: str) -> Position:
    token = str(raw or "G").upper().replace("-", "").replace("/", "")
    try:
        return Position(token)
    except ValueError:
        return Position.G


def build_players_asof(logs: list[dict], game_day: date) -> dict[str, Player]:
    """
    One Player per id, using only logs strictly before ``game_day``.

    Potential assists and rebound chances are not on the box score, so they
    stay 0 and the models report them as missing. Injury knowledge is not invented.
    """
    prior = logs_before(logs, game_day)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in prior:
        pid = str(row.get("player_id") or "")
        if pid:
            grouped[pid].append(row)

    players: dict[str, Player] = {}
    for pid, rows in grouped.items():
        rows.sort(key=lambda r: r.get("game_date") or "")
        games = len(rows)
        minutes = [_num(r.get("min")) for r in rows if r.get("min") not in (None, "")]
        mpg = mean(minutes) if minutes else 0.0
        latest = rows[-1]
        fga = sum(_num(r.get("fga")) for r in rows) / games
        fg3a = sum(_num(r.get("fg3a")) for r in rows) / games
        fg3m = sum(_num(r.get("fg3m")) for r in rows) / games
        pts = sum(_num(r.get("pts")) for r in rows) / games
        reb = sum(_num(r.get("reb")) for r in rows) / games
        ast = sum(_num(r.get("ast")) for r in rows) / games
        players[pid] = Player(
            player_id=pid,
            name=str(latest.get("player_name") or pid),
            team_id=str(latest.get("team_id") or ""),
            team_abbr=str(latest.get("team_abbr") or ""),
            position=_position(str(latest.get("position") or "G")),
            role=PlayerRole.STARTER if mpg >= 28 else PlayerRole.BENCH,
            is_starter=mpg >= 28,
            minutes_per_game=mpg,
            points_per_game=pts,
            rebounds_per_game=reb,
            assists_per_game=ast,
            threes_per_game=fg3m,
            field_goal_attempts=fga,
            three_point_attempts=fg3a,
            three_point_pct=(fg3m / fg3a) if fg3a > 0 else 0.0,
            usage_rate=min(0.40, max(0.08, fga / 40.0)) if fga > 0 else 0.0,
            data_source=DataSource.BALLDONTLIE,
        )
    return players


def match_player(odds_name: str, players: dict[str, Player]) -> Player | None:
    for player in players.values():
        if names_match(odds_name, player.name):
            return player
    return None


def build_defense_asof(
    logs: list[dict],
    game_day: date,
    defense_team_id: str,
) -> tuple[TeamDefense, dict]:
    """
    Positional allowances from prior box scores only.

    Pace and defensive rating are not on the player box score. They stay at
    the league average and are flagged, rather than copied from today's season line.
    """
    prior = [
        row for row in logs_before(logs, game_day)
        if str(row.get("opponent_team_id") or "") == str(defense_team_id)
    ]
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in prior:
        raw = str(row.get("position") or "G").upper().replace("-", "").replace("/", "")
        buckets[_POSITION_BUCKET.get(raw, "SF")].append(row)

    filled: dict[str, float] = {}
    samples: dict[str, int] = {}
    for slot, rows in buckets.items():
        games = len(rows)
        samples[slot] = games
        if games <= 0 or slot not in _BASELINES["pts"]:
            continue
        for stat, prefix in (
            ("pts", "pts_allowed"),
            ("reb", "reb_allowed"),
            ("ast", "ast_allowed"),
        ):
            observed = sum(_num(r.get(stat)) for r in rows) / games
            filled[f"{prefix}_{slot.lower()}"] = shrink_allowed(
                observed, _BASELINES[stat][slot], games,
            )
        made = sum(_num(r.get("fg3m")) for r in rows) / games
        shrunk_threes = shrink_allowed(made, _BASELINES["fg3"][slot], games)
        if shrunk_threes > 0:
            filled[f"threes_allowed_{slot.lower()}"] = shrunk_threes

    defense = TeamDefense(
        team_id=str(defense_team_id),
        team_abbr="",
        pace=LEAGUE_AVG_PACE,
        pace_is_fallback=True,
        pace_source="unavailable_asof_box_scores_have_no_pace",
        defensive_efficiency=LEAGUE_AVG_DEF_EFF,
        data_source=DataSource.BALLDONTLIE,
        **filled,
    )
    quality = {
        "injury_lineup": "limited",
        "injury_note": (
            "BALLDONTLIE injuries are current-only. Historical injury and lineup "
            "knowledge was not invented."
        ),
        "pace": "fallback",
        "defensive_rating": "fallback",
        "potential_assists": "missing_from_box_score",
        "rebound_chances": "missing_from_box_score",
        "positional_sample_games": samples,
        "prior_player_games_against": len(prior),
    }
    return defense, quality


def game_from_event(event: dict, game_day: date) -> Game:
    tip_raw = event.get("commence_time") or ""
    tip: datetime | None = None
    if tip_raw:
        tip = datetime.fromisoformat(tip_raw.replace("Z", "+00:00"))
    return Game(
        game_id=str(event.get("id") or ""),
        home_team_id=str(event.get("home_team") or ""),
        home_team_abbr=str(event.get("home_team") or ""),
        away_team_id=str(event.get("away_team") or ""),
        away_team_abbr=str(event.get("away_team") or ""),
        game_date=game_day,
        tip_off_time=tip,
        data_source=DataSource.ODDS_API,
    )
