"""
Walk-forward player-game rows from box scores and advanced logs.

A game is scored only with rows whose date is strictly earlier.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from engine.accuracy.fit import FEATURES
from engine.backtest.profiles import position_bucket, team_game_rate

PRIOR_STRENGTH = 8
_STATS = {
    "minutes": "min",
    "points": "pts",
    "rebounds": "reb",
    "assists": "ast",
    "threes": "fg3m",
    "fga": "fga",
    "fg3a": "fg3a",
    "fta": "fta",
}


def _played(row: dict) -> bool:
    try:
        return float(row.get("min") or 0) > 0
    except (TypeError, ValueError):
        return False


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_player_games(logs: list[dict], start: date, end: date) -> list[dict]:
    """
    One row per player-game in ``start`` .. ``end`` inclusive.

    Context uses only earlier 2025-26 games. 2024-25 is not treated as this roster's defense.
    """
    current = [
        row for row in logs
        if row.get("season") == "2025-26" and row.get("game_date") and _played(row)
    ]
    current.sort(key=lambda row: (row["game_date"], str(row.get("game_id"))))
    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in current:
        by_date[row["game_date"]].append(row)

    player_hist: dict[str, list[dict]] = defaultdict(list)
    team_pace: dict[str, list[float]] = defaultdict(list)
    team_drtg: dict[str, list[float]] = defaultdict(list)
    allowed: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    league_allowed: dict[tuple[str, str], list[float]] = defaultdict(list)
    position_rates: dict[tuple[str, str], list[float]] = defaultdict(list)
    league_pace: list[float] = []
    league_drtg: list[float] = []
    out: list[dict] = []

    for game_day in sorted(by_date):
        played = date.fromisoformat(game_day)
        if start <= played <= end:
            out.extend(_emit(
                by_date[game_day], game_day, player_hist, team_pace, team_drtg,
                allowed, league_allowed, league_pace, league_drtg, position_rates,
            ))
        _update(
            by_date[game_day], player_hist, team_pace, team_drtg,
            allowed, league_allowed, league_pace, league_drtg, position_rates,
        )
    return out


def _emit(rows, game_day, player_hist, team_pace, team_drtg, allowed, league_allowed, league_pace, league_drtg, position_rates):
    emitted = []
    league_pace_mean = _mean(league_pace) or 100.3
    league_drtg_mean = _mean(league_drtg) or 113.1
    for row in rows:
        pid = str(row.get("player_id") or "")
        history = player_hist.get(pid) or []
        if len(history) < 3:
            continue
        opponent = str(row.get("opponent_team_id") or "")
        if not opponent:
            continue
        slot = position_bucket(str(row.get("position") or "")) or "SF"
        baseline = {
            stat: _shrunk(history, field, _mean(position_rates.get((slot, field)) or []))
            for stat, field in _STATS.items()
        }
        pace = _mean(team_pace.get(opponent) or [])
        drtg = _mean(team_drtg.get(opponent) or [])
        pace_gap = 0.0 if not pace else (pace - league_pace_mean) / league_pace_mean
        drtg_gap = 0.0 if not drtg else (drtg - league_drtg_mean) / league_drtg_mean
        usage = _mean([
            _num(item.get("usage_percentage"))
            for item in history
            if _num(item.get("usage_percentage")) > 0
        ]) or 0.0
        if usage > 1.5:
            usage /= 100.0
        recent_minutes = [
            _num(item.get("min")) for item in history[-10:] if _num(item.get("min")) > 0
        ]
        minutes = (sum(recent_minutes) / len(recent_minutes)) if recent_minutes else 0.0
        role = "starter" if minutes >= 28 else "bench"
        shared = {
            "game_date": game_day,
            "game_id": str(row.get("game_id") or ""),
            "player_id": pid,
            "player_name": row.get("player_name") or pid,
            "position": slot,
            "role": role,
            "opponent_team_id": opponent,
            "usage": usage,
            "pace_gap": pace_gap,
            "drtg_gap": drtg_gap,
            "pace_observed": pace is not None,
            "drtg_observed": drtg is not None,
        }
        for stat, field in _STATS.items():
            base = baseline[stat]
            volume = base if base > 0 else 1.0
            pos_gap = 0.0 if stat == "minutes" else _positional_gap(
                allowed, league_allowed, opponent, slot, _STATS[stat],
            )
            features = [
                pace_gap * volume,
                drtg_gap * volume,
                pos_gap * volume,
                (usage - 0.20) * volume,
            ]
            shared_stat = {**shared, "pos_gap": pos_gap}
            emitted.append({
                **shared_stat,
                "stat": stat,
                "baseline": base,
                "actual": _num(row.get(field)),
                "x": features,
                "feature_names": list(FEATURES),
            })
    return emitted


def _update(rows, player_hist, team_pace, team_drtg, allowed, league_allowed, league_pace, league_drtg, position_rates):
    by_team_game: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        pid = str(row.get("player_id") or "")
        if pid:
            player_hist[pid].append(row)
        team = str(row.get("team_id") or "")
        game = str(row.get("game_id") or "")
        if team and game:
            by_team_game[(team, game)].append(row)
        slot = position_bucket(str(row.get("position") or ""))
        opponent = str(row.get("opponent_team_id") or "")
        if slot:
            for field in ("min", "pts", "reb", "ast", "fg3m", "fga", "fg3a", "fta"):
                position_rates[(slot, field)].append(_num(row.get(field)))
        if slot and opponent:
            for field in ("pts", "reb", "ast", "fg3m", "fga", "fg3a", "fta"):
                value = _num(row.get(field))
                allowed[(opponent, slot, field)].append(value)
                league_allowed[(slot, field)].append(value)
    for (team, _game), members in by_team_game.items():
        pace = team_game_rate(members, "pace")
        drtg = team_game_rate(members, "defensive_rating")
        if pace:
            team_pace[team].append(pace)
            league_pace.append(pace)
        if drtg:
            team_drtg[team].append(drtg)
            league_drtg.append(drtg)


def _positional_gap(allowed, league_allowed, opponent: str, slot: str, field: str) -> float:
    observed = allowed.get((opponent, slot, field)) or []
    league = league_allowed.get((slot, field)) or []
    league_mean = _mean(league)
    observed_mean = _mean(observed)
    if not observed or not league_mean:
        return 0.0
    shrunk = len(observed) / (len(observed) + 12.0)
    return shrunk * ((observed_mean or 0.0) - league_mean) / league_mean


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _shrunk(history: list[dict], field: str, position_mean: float | None) -> float:
    """Player recent mean, pulled toward the position mean. Not toward zero."""
    recent = history[-20:]
    values = [_num(row.get(field)) for row in recent]
    if not values:
        return 0.0
    player_mean = sum(values) / len(values)
    if not position_mean or position_mean <= 0:
        return player_mean
    weight = len(values) / (len(values) + PRIOR_STRENGTH)
    return weight * player_mean + (1.0 - weight) * position_mean
