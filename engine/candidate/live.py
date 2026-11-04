"""
Frozen candidate probabilities for a live slate, shown beside production.

Production probabilities, edges and rankings are not changed. Each prop gets
``candidate_probability`` (chance its side hits) and ``candidate_projection``
when the candidate can score it, else they stay None.

Data: BALLDONTLIE box + advanced logs for the current season through the day
before the slate (cached in data/cache/candidate_live, not tracked), plus last
season for the early-season features.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from domain.constants import NBA_SEASON, balldontlie_season_start_year
from engine.candidate import model as cmodel

logger = logging.getLogger(__name__)

STAT_OF_PROP = {"points": "points", "rebounds": "rebounds", "assists": "assists", "threes": "threes"}
_CURRENT_MONTH_TTL = 6 * 3600


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


def _team_ids(logs: list[dict]) -> dict[str, str]:
    by_abbr: dict[str, str] = {}
    for row in logs:
        abbr = str(row.get("team_abbr") or "").upper()
        if abbr and row.get("team_id"):
            by_abbr[abbr] = str(row["team_id"])
    return by_abbr


def _placeholder(pid: str, team: str, opp: str, slate: date, season: str) -> dict:
    """A pregame row for tonight. Its stats are never read; only history before it is."""
    zero = {k: 0 for k in ("pts", "fga", "fta", "fg3a", "fg3m", "reb", "rebound_chances_total", "ast", "passes")}
    return {
        **zero, "season": season, "game_date": slate.isoformat(), "game_id": f"live-{min(team, opp)}-{max(team, opp)}",
        "team_id": team, "opponent_team_id": opp, "player_id": pid, "min": 1.0,
    }


def attach_candidate(props: list, slate: date, season: str = NBA_SEASON) -> dict:
    """Set candidate_probability / candidate_projection on each prop the candidate can score."""
    params = cmodel.load_params()
    if params is None:
        return {"status": "no_params"}
    try:
        logs = season_logs(slate, season)
    except Exception as exc:  # keep the board usable if the extra fetch fails
        logger.warning("Candidate logs unavailable: %s", exc)
        return {"status": "no_data", "reason": str(exc)}

    teams = _team_ids(logs)
    placeholders, seen = [], set()
    for prop in props:
        pid = str(prop.player_id)
        team, opp = teams.get(str(prop.team_abbr).upper()), teams.get(str(prop.opponent_abbr).upper())
        if not team or not opp or pid in seen:
            continue
        seen.add(pid)
        placeholders.append(_placeholder(pid, team, opp, slate, season))
    if not placeholders:
        return {"status": "no_players"}

    target_end = (slate + timedelta(days=1)).isoformat()
    items = cmodel.build_items(logs + placeholders, target_end=target_end, emit_from=slate.isoformat())
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
    }
