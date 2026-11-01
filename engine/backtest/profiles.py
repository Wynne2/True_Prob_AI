"""
Player and opponent profiles from games completed before the slate date.

Previous-season games are a prior. Current-season games on or after the slate
date are excluded. Season-average endpoints are not used.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import mean

from domain.constants import LEAGUE_AVG_DEF_EFF, LEAGUE_AVG_PACE
from domain.entities import Player, TeamDefense
from domain.enums import DataSource, PlayerRole, Position
from engine.backtest.asof import logs_before
from models.matchup_model import shrink_allowed

# BALLDONTLIE positions are broad. This map is explicit and does not invent a
# PG-versus-SG split the feed does not provide.
POSITION_MAP = {
    "G": "PG",
    "GF": "SG",
    "FG": "SG",
    "F": "SF",
    "FC": "PF",
    "CF": "PF",
    "C": "C",
    "PG": "PG",
    "SG": "SG",
    "SF": "SF",
    "PF": "PF",
}
_BUCKET_POSITION = {
    "PG": Position.PG,
    "SG": Position.SG,
    "SF": Position.SF,
    "PF": Position.PF,
    "C": Position.C,
}
_BASELINES = {
    "pts": {"PG": 22.0, "SG": 20.5, "SF": 21.5, "PF": 19.5, "C": 24.0},
    "reb": {"PG": 3.2, "SG": 3.7, "SF": 5.2, "PF": 8.5, "C": 11.0},
    "ast": {"PG": 6.4, "SG": 3.1, "SF": 2.6, "PF": 2.3, "C": 2.8},
    "fg3": {"PG": 2.4, "SG": 2.3, "SF": 2.0, "PF": 0.9, "C": 0.4},
}
PRIOR_STRENGTH = 10


def position_bucket(raw: str) -> str:
    token = str(raw or "").upper().replace("-", "").replace("/", "").replace(" ", "")
    return POSITION_MAP.get(token, "")


def _played(row: dict) -> bool:
    minutes = row.get("min")
    try:
        return minutes is not None and float(minutes) > 0
    except (TypeError, ValueError):
        return False


def _blend(current: list[float], prior: list[float], strength: int = PRIOR_STRENGTH) -> tuple[float | None, str]:
    if not current and not prior:
        return None, "missing"
    if not prior:
        return mean(current), "current_only"
    if not current:
        return mean(prior), "prior_only"
    weight = len(current) / (len(current) + strength)
    return weight * mean(current) + (1.0 - weight) * mean(prior), "shrunk_to_previous_season"


def _values(rows: list[dict], key: str, *, positive: bool = False) -> list[float]:
    values = []
    for row in rows:
        raw = row.get(key)
        if raw is None:
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if positive and number <= 0:
            continue
        values.append(number)
    return values


def team_game_rate(rows: list[dict], key: str) -> float | None:
    """Possession-weighted on-court rate for one team in one game."""
    weighted = 0.0
    weight = 0.0
    for row in rows:
        poss = row.get("possessions")
        value = row.get(key)
        if poss is None or value is None:
            continue
        try:
            poss_n = float(poss)
            value_n = float(value)
        except (TypeError, ValueError):
            continue
        if poss_n <= 0 or value_n <= 0:
            continue
        weighted += value_n * poss_n
        weight += poss_n
    if weight <= 0:
        return None
    return weighted / weight


def build_player_asof(rows: list[dict], game_day: date) -> tuple[Player | None, dict]:
    """One player. ``rows`` must already be that player's games before ``game_day``."""
    played = [row for row in rows if _played(row)]
    if not played:
        return None, {"status": "no_prior_games"}
    current = [row for row in played if row.get("season") == "2025-26"]
    prior = [row for row in played if row.get("season") == "2024-25"]
    latest = sorted(current or prior, key=lambda row: row.get("game_date") or "")[-1]
    usage_current = _values(current, "usage_percentage", positive=True)
    usage_prior = _values(prior, "usage_percentage", positive=True)
    if usage_current and mean(usage_current) > 1.5:
        usage_current = [value / 100.0 for value in usage_current]
    if usage_prior and mean(usage_prior) > 1.5:
        usage_prior = [value / 100.0 for value in usage_prior]

    def stat(key: str, positive: bool = False) -> tuple[float, str]:
        value, source = _blend(_values(current, key, positive=positive), _values(prior, key, positive=positive))
        return (0.0 if value is None else value), source

    minutes, minutes_source = stat("min", positive=True)
    points, _ = stat("pts")
    rebounds, _ = stat("reb")
    assists, _ = stat("ast")
    threes, _ = stat("fg3m")
    fga, fga_source = stat("fga")
    fg3a, fg3a_source = stat("fg3a")
    chances, chances_source = stat("rebound_chances_total")
    usage, usage_source = _blend(usage_current, usage_prior)
    bucket = position_bucket(str(latest.get("position") or ""))
    recent = sorted(current, key=lambda row: row.get("game_date") or "")[-10:]
    player = Player(
        player_id=str(latest.get("player_id") or ""),
        name=str(latest.get("player_name") or ""),
        team_id=str(latest.get("team_id") or ""),
        team_abbr=str(latest.get("team_abbr") or ""),
        position=_BUCKET_POSITION.get(bucket, Position.G),
        role=PlayerRole.STARTER if minutes >= 28 else PlayerRole.BENCH,
        is_starter=minutes >= 28,
        minutes_per_game=minutes,
        points_per_game=points,
        rebounds_per_game=rebounds,
        assists_per_game=assists,
        threes_per_game=threes,
        field_goal_attempts=fga,
        three_point_attempts=fg3a,
        three_point_pct=(threes / fg3a) if fg3a > 0 else 0.0,
        usage_rate=0.0 if usage is None else usage,
        rebound_chances=chances,
        potential_assists=0.0,
        home_ppg=points,
        away_ppg=points,
        last10_points=[float(row.get("pts") or 0) for row in recent],
        last10_rebounds=[float(row.get("reb") or 0) for row in recent],
        last10_assists=[float(row.get("ast") or 0) for row in recent],
        last10_threes=[float(row.get("fg3m") or 0) for row in recent],
        last10_minutes=[float(row.get("min") or 0) for row in recent],
        data_source=DataSource.BALLDONTLIE,
    )
    quality = {
        "current_games": len(current),
        "previous_season_games": len(prior),
        "minutes_source": minutes_source,
        "fga_source": fga_source,
        "fg3a_source": fg3a_source,
        "rebound_chances_source": chances_source,
        "usage_source": usage_source,
        "potential_assists_source": "unavailable",
        "position_raw": str(latest.get("position") or ""),
        "position_bucket": bucket or "unmapped",
    }
    return player, quality


def build_players_asof(logs: list[dict], game_day: date) -> tuple[dict[str, Player], dict[str, dict]]:
    prior = logs_before(logs, game_day)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in prior:
        pid = str(row.get("player_id") or "")
        if pid:
            grouped[pid].append(row)
    players: dict[str, Player] = {}
    quality: dict[str, dict] = {}
    for pid, rows in grouped.items():
        player, info = build_player_asof(rows, game_day)
        if player is None:
            continue
        players[pid] = player
        quality[pid] = info
    return players, quality


def build_defense_asof(logs: list[dict], game_day: date, defense_team_id: str) -> tuple[TeamDefense, dict]:
    """
    Current-season games only. A previous-season roster is not this team's defense.
    """
    prior = [
        row for row in logs_before(logs, game_day)
        if row.get("season") == "2025-26"
    ]
    by_game: dict[str, list[dict]] = defaultdict(list)
    for row in prior:
        if str(row.get("team_id") or "") == str(defense_team_id) and row.get("game_id"):
            by_game[str(row["game_id"])].append(row)
    paces = [team_game_rate(rows, "pace") for rows in by_game.values()]
    ratings = [team_game_rate(rows, "defensive_rating") for rows in by_game.values()]
    paces = [value for value in paces if value is not None]
    ratings = [value for value in ratings if value is not None]
    pace_fallback = not paces
    rating_fallback = not ratings

    against = [
        row for row in prior
        if str(row.get("opponent_team_id") or "") == str(defense_team_id) and _played(row)
    ]
    buckets: dict[str, list[dict]] = defaultdict(list)
    unmapped = 0
    for row in against:
        bucket = position_bucket(str(row.get("position") or ""))
        if not bucket:
            unmapped += 1
            continue
        buckets[bucket].append(row)

    filled: dict[str, float] = {}
    samples: dict[str, int] = {}
    for slot, rows in buckets.items():
        samples[slot] = len(rows)
        if slot not in _BASELINES["pts"]:
            continue
        for stat, prefix in (("pts", "pts_allowed"), ("reb", "reb_allowed"), ("ast", "ast_allowed")):
            observed = sum(float(row.get(stat) or 0) for row in rows) / len(rows)
            filled[f"{prefix}_{slot.lower()}"] = shrink_allowed(observed, _BASELINES[stat][slot], len(rows))
        made = sum(float(row.get("fg3m") or 0) for row in rows) / len(rows)
        shrunk = shrink_allowed(made, _BASELINES["fg3"][slot], len(rows))
        if shrunk > 0:
            filled[f"threes_allowed_{slot.lower()}"] = shrunk

    defense = TeamDefense(
        team_id=str(defense_team_id),
        team_abbr="",
        pace=LEAGUE_AVG_PACE if pace_fallback else mean(paces),
        pace_is_fallback=pace_fallback,
        pace_source="measured_possession_weighted" if not pace_fallback else "missing_pace",
        defensive_efficiency=LEAGUE_AVG_DEF_EFF if rating_fallback else mean(ratings),
        data_source=DataSource.BALLDONTLIE,
        **filled,
    )
    quality = {
        "injury_lineup": "unavailable",
        "model_c_status": "incomplete",
        "model_c_reason": (
            "Historical injuries and lineups are not on the advanced box score. "
            "Model C does not include teammate-absence redistribution."
        ),
        "pace_games": len(paces),
        "pace_fallback": pace_fallback,
        "defensive_rating_games": len(ratings),
        "defensive_rating_fallback": rating_fallback,
        "positional_sample": samples,
        "positional_rows": len(against),
        "unmapped_positions": unmapped,
        "position_map": dict(POSITION_MAP),
    }
    return defense, quality
