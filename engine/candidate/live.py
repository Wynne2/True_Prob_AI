"""
Frozen candidate probabilities for a live slate, shown beside production.

Production probabilities, edges and rankings are not changed. Each prop gets
``candidate_probability`` (chance its side hits) and ``candidate_projection``
when the candidate can score it, else they stay None.

Inputs for tonight:
    history   BALLDONTLIE box + advanced logs for the current season through the
              day before the slate (cached in data/cache/candidate_live, untracked),
              plus last season for the early-season features.
    absences  BALLDONTLIE /v1/player_injuries: Out or Doubtful counts as absent.
              Every other recent rotation player on a team is treated as playing.
    spread    the board's game lines (Game.home_spread), when present.

The newest frozen version is used (v2, else v1).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from domain.constants import NBA_SEASON, balldontlie_season_start_year
from engine.candidate import model as cmodel

logger = logging.getLogger(__name__)

STAT_OF_PROP = {"points": "points", "rebounds": "rebounds", "assists": "assists", "threes": "threes"}
OUT_STATUSES = ("out", "doubtful")
ROTATION_LOOKBACK_GAMES = 5
_CURRENT_MONTH_TTL = 6 * 3600


def frozen_params() -> dict | None:
    """Newest frozen candidate: v2 if present, else v1."""
    v2 = cmodel.ROOT / "data" / "models" / "candidate_v2.json"
    return cmodel.load_params(v2) or cmodel.load_params()


def _month_windows(start: date, end: date, season: str):
    out = []
    while start <= end:
        month_end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        out.append((start, min(month_end, end), season))
        start = min(month_end, end) + timedelta(days=1)
    return out


def _prior_label(season: str) -> str:
    first = int(season[:4]) - 1
    return f"{first}-{str(first + 1)[-2:]}"


def season_logs(slate: date, season: str = NBA_SEASON) -> list[dict]:
    """Current-season logs before ``slate`` plus the previous season."""
    from engine.backtest.advanced_history import _load_windows, load_historical_logs
    from services.cache_service import get_cache

    cache = get_cache("candidate_live", default_ttl=_CURRENT_MONTH_TTL)
    prior = _prior_label(season)
    if prior == "2025-26":
        logs, _ = load_historical_logs()
        extra, _ = _load_windows(cmodel.EXTRA_2025_26_WINDOWS)
        prior_logs = [r for r in logs + extra if r.get("season") == prior]
    else:
        start_year = int(prior[:4])
        prior_logs, _ = _load_windows(_month_windows(date(start_year, 10, 1), date(start_year + 1, 4, 30), prior), cache=cache)

    start = date(balldontlie_season_start_year(season), 10, 1)
    end = slate - timedelta(days=1)
    current: list[dict] = []
    if end >= start:
        for window in _month_windows(start, end, season):
            complete = window[1].month != end.month or window[1].year != end.year
            rows, _ = _load_windows((window,), cache=cache, ttl_seconds=0 if complete else _CURRENT_MONTH_TTL)
            current.extend(rows)
    return prior_logs + current


def injured_out() -> set[str]:
    """Player ids listed Out or Doubtful on BALLDONTLIE's current injury report."""
    from data.loaders.nba_api_loader import fetch_balldontlie_injuries

    try:
        rows = fetch_balldontlie_injuries()
    except Exception as exc:
        logger.warning("Injury report unavailable: %s", exc)
        return set()
    return {
        str(r["player_id"]) for r in rows
        if str(r.get("status") or "").strip().lower().startswith(OUT_STATUSES)
    }


def _team_ids(logs: list[dict]) -> dict[str, str]:
    by_abbr: dict[str, str] = {}
    for row in logs:
        abbr = str(row.get("team_abbr") or "").upper()
        if abbr and row.get("team_id"):
            by_abbr[abbr] = str(row["team_id"])
    return by_abbr


def recent_rotation(logs: list[dict], season: str) -> dict[str, set[str]]:
    """team id -> players who appeared for that team in its last few games this season."""
    games_by_team: dict[str, list[tuple[str, str]]] = {}
    played: dict[tuple[str, str], set[str]] = {}
    last_team: dict[str, tuple[str, str]] = {}
    for r in logs:
        if r.get("season") != season or float(r.get("min") or 0) <= 0:
            continue
        team, gid, day, pid = str(r.get("team_id")), str(r.get("game_id")), r.get("game_date"), str(r.get("player_id"))
        games_by_team.setdefault(team, [])
        if (day, gid) not in games_by_team[team]:
            games_by_team[team].append((day, gid))
        played.setdefault((team, gid), set()).add(pid)
        if pid not in last_team or day >= last_team[pid][0]:
            last_team[pid] = (day, team)
    out: dict[str, set[str]] = {}
    for team, games in games_by_team.items():
        recent = sorted(games)[-ROTATION_LOOKBACK_GAMES:]
        members = set().union(*(played.get((team, gid), set()) for _, gid in recent))
        out[team] = {pid for pid in members if last_team.get(pid, ("", ""))[1] == team}
    return out


def _placeholder(pid: str, team: str, opp: str, slate: date, season: str) -> dict:
    """A pregame row for tonight. Its stats are never read; only history before it is."""
    zero = {k: 0 for k in ("pts", "fga", "fta", "fg3a", "fg3m", "reb", "rebound_chances_total", "ast", "passes")}
    return {
        **zero, "season": season, "game_date": slate.isoformat(), "game_id": f"live-{min(team, opp)}-{max(team, opp)}",
        "team_id": team, "opponent_team_id": opp, "player_id": pid, "min": 1.0,
    }


def _started(game, now=None) -> bool:
    """True once a game has tipped or its status says it is under way or over."""
    from datetime import datetime, timezone

    status = str(getattr(game, "status", "") or "").lower()
    if any(word in status for word in ("progress", "final", "half", "qtr", "live")):
        return True
    tip = getattr(game, "tip_off_time", None)
    if tip is None:
        return False
    now = now or datetime.now(timezone.utc)
    if tip.tzinfo is None:
        tip = tip.replace(tzinfo=timezone.utc)
    return tip <= now


def _spreads(games: list | None, teams: dict[str, str], slate: date, now=None) -> dict[tuple[str, str], float]:
    """Pregame spreads only: lines for games already under way are in-game prices."""
    out = {}
    for g in games or []:
        spread = getattr(g, "home_spread", None)
        if spread in (None, 0, 0.0) or _started(g, now):
            continue  # 0.0 is the entity default for "no line"
        home, away = teams.get(str(g.home_team_abbr).upper()), teams.get(str(g.away_team_abbr).upper())
        if home and away:
            out[(home, slate.isoformat())] = float(spread)
            out[(away, slate.isoformat())] = -float(spread)
    return out


def attach_candidate(props: list, slate: date, season: str = NBA_SEASON, games: list | None = None) -> dict:
    """Set candidate_probability / candidate_projection on each prop the candidate can score."""
    params = frozen_params()
    if params is None:
        return {"status": "no_params"}
    try:
        logs = season_logs(slate, season)
    except Exception as exc:  # keep the board usable if the extra fetch fails
        logger.warning("Candidate logs unavailable: %s", exc)
        return {"status": "no_data", "reason": str(exc)}

    teams = _team_ids(logs)
    rotation = recent_rotation(logs, season)
    out_ids = injured_out()
    matchups: dict[str, str] = {}
    for prop in props:
        team, opp = teams.get(str(prop.team_abbr).upper()), teams.get(str(prop.opponent_abbr).upper())
        if team and opp:
            matchups[team] = opp
            matchups[opp] = team

    placeholders, seen = [], set()
    for team, opp in matchups.items():
        for pid in sorted(rotation.get(team, set())):
            if pid in out_ids or pid in seen:
                continue
            seen.add(pid)
            placeholders.append(_placeholder(pid, team, opp, slate, season))
    for prop in props:  # players with a prop but outside the recent rotation
        pid = str(prop.player_id)
        team, opp = teams.get(str(prop.team_abbr).upper()), teams.get(str(prop.opponent_abbr).upper())
        if team and opp and pid not in seen and pid not in out_ids:
            seen.add(pid)
            placeholders.append(_placeholder(pid, team, opp, slate, season))
    if not placeholders:
        return {"status": "no_players"}

    target_end = (slate + timedelta(days=1)).isoformat()
    items = cmodel.build_items(
        logs + placeholders, target_end=target_end, emit_from=slate.isoformat(),
        spreads=_spreads(games, teams, slate),
    )
    preds = cmodel.predict(items, params)
    by_key = {(it["player_id"], it["stat"]): p for it, p in zip(items, preds)}

    attached = 0
    for prop in props:
        stat = STAT_OF_PROP.get(getattr(prop.prop_type, "value", str(prop.prop_type)))
        pred = by_key.get((str(prop.player_id), stat)) if stat else None
        if pred is None:
            continue
        over = cmodel.over_probability(pred, float(prop.line))
        push = cmodel.push_probability(pred, float(prop.line))
        side = getattr(prop.side, "value", str(prop.side))
        prop.candidate_probability = over if side == "over" else max(0.001, 1.0 - over - push)
        prop.candidate_projection = pred["mean"]
        attached += 1
    return {
        "status": "ok", "version": params.get("version"), "attached": attached, "props": len(props),
        "players_scored": len({k[0] for k in by_key}),
        "injured_out_on_slate": len([p for t in matchups for p in rotation.get(t, set()) if p in out_ids]),
        "games_with_spread": len(_spreads(games, teams, slate)) // 2,
    }


def apply_candidate_edges(props: list) -> dict:
    """
    Make the candidate the true probability that drives edges.

    For each prop the candidate scored: true probability = candidate (clamped to the
    production floor/ceiling), edge = that minus the book's implied probability,
    fair odds recomputed. The production blend is kept in ``production_probability``.
    Props without a candidate probability are left on the production blend.
    """
    from domain import constants
    from odds.fair_odds import calculate_edge, true_prob_to_american_odds

    if not getattr(constants, "USE_CANDIDATE_EDGES", False):
        return {"applied": 0, "fallback": len(props), "enabled": False}
    lo, hi = getattr(constants, "CANDIDATE_EDGE_IMPLIED_RANGE", (0.0, 1.0))
    applied = outside = 0
    for prop in props:
        p = getattr(prop, "candidate_probability", None)
        if p is None:
            continue
        if not (lo <= float(prop.implied_probability) <= hi):
            prop.candidate_outside_range = True
            outside += 1
            continue
        p = min(max(float(p), constants.MIN_PROBABILITY_FLOOR), constants.MAX_PROBABILITY_CEILING)
        prop.production_probability = prop.true_probability
        prop.true_probability = p
        prop.calibrated_probability = p
        prop.raw_model_probability = p
        prop.edge = calculate_edge(p, prop.implied_probability)
        prop.fair_odds = true_prob_to_american_odds(p)
        prop.model_weight = 1.0
        prop.probability_method = "candidate"
        applied += 1
    return {"applied": applied, "fallback": len(props) - applied, "outside_range": outside, "enabled": True}
