"""
Pregame point spreads per game, for blowout-related minutes.

Historical: GET /v4/historical/sports/basketball_nba/odds (markets=spreads,
regions=us) at 18:00 UTC on each game date, before any NBA tip. One snapshot
covers every game that day and costs 10 credits. Snapshots are cached in
data/cache/odds_history (tracked), so each date is paid for once.

Live: the board's game lines (Game.home_spread), already pulled during a scan.

The value used is the median home spread across books; the model uses its
absolute size (how lopsided the game is expected to be).

Run: python -m engine.candidate.spreads --start 2025-10-21 --end 2026-02-28 --cap 1300
"""

from __future__ import annotations

import argparse
import statistics
from datetime import date, datetime, timedelta, timezone

from engine.backtest.odds_history import _provider, _read_cache, _write_cache

CREDITS_PER_DATE = 10


def _stamp(day: date) -> str:
    return datetime(day.year, day.month, day.day, 18, 0, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cached(day: date) -> dict | None:
    return _read_cache(f"spreads_{_stamp(day)}")


def fetch(day: date) -> dict:
    hit = cached(day)
    if hit is not None:
        return hit
    provider = _provider()
    payload = provider._get(
        "historical/sports/basketball_nba/odds",
        date=_stamp(day), regions="us", markets="spreads", oddsFormat="american",
    )
    events = (payload or {}).get("data") or [] if isinstance(payload, dict) else []
    games = []
    lo = datetime(day.year, day.month, day.day, 18, 0, tzinfo=timezone.utc)
    hi = lo + timedelta(hours=18)
    for event in events:
        tip = datetime.fromisoformat(str(event.get("commence_time")).replace("Z", "+00:00"))
        if not (lo <= tip < hi):
            continue
        home = event.get("home_team")
        points = []
        for book in event.get("bookmakers") or []:
            for market in book.get("markets") or []:
                if market.get("key") != "spreads":
                    continue
                for outcome in market.get("outcomes") or []:
                    if outcome.get("name") == home and outcome.get("point") is not None:
                        points.append(float(outcome["point"]))
        if points:
            games.append({
                "home_team": home, "away_team": event.get("away_team"),
                "commence_time": event.get("commence_time"),
                "home_spread": statistics.median(points), "n_books": len(points),
            })
    result = {"date": day.isoformat(), "timestamp": (payload or {}).get("timestamp") if isinstance(payload, dict) else None,
              "games": games, "credits_last": provider.last_quota.get("last")}
    _write_cache(f"spreads_{_stamp(day)}", result)
    return result


def _norm(name: str) -> str:
    from engine.backtest.runner import normalize_team_name
    return normalize_team_name(name)


def team_name_index(logs: list[dict]) -> dict[str, str]:
    """Odds API full team name -> BALLDONTLIE team id, using BALLDONTLIE's team list."""
    from data.loaders.nba_api_loader import _client

    rows = _client().paginate("v1/teams", {})
    return {_norm(str(t.get("full_name") or "")): str(t.get("id")) for t in rows if t.get("id")}


def spread_by_team_date(days: list[date], logs: list[dict]) -> dict[tuple[str, str], float]:
    """(team_id, game_date) -> that team's own spread (negative = favorite). Cached dates only."""
    names = team_name_index(logs)
    out: dict[tuple[str, str], float] = {}
    for day in days:
        snap = cached(day)
        if not snap:
            continue
        for g in snap["games"]:
            home, away = names.get(_norm(g["home_team"])), names.get(_norm(g["away_team"]))
            if home and away:
                out[(home, day.isoformat())] = g["home_spread"]
                out[(away, day.isoformat())] = -g["home_spread"]
    return out


def game_dates(logs: list[dict], start: date, end: date) -> list[date]:
    return sorted({date.fromisoformat(r["game_date"]) for r in logs if start.isoformat() <= r["game_date"] <= end.isoformat()})


def main() -> None:
    from engine.backtest.advanced_history import load_historical_logs

    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-10-21")
    parser.add_argument("--end", default="2026-02-28")
    parser.add_argument("--cap", type=int, default=1300)
    args = parser.parse_args()
    logs, _ = load_historical_logs()
    days = game_dates(logs, date.fromisoformat(args.start), date.fromisoformat(args.end))
    cold = [d for d in days if cached(d) is None]
    cost = len(cold) * CREDITS_PER_DATE
    print(f"{len(days)} game dates, {len(cold)} not cached, about {cost} credits (cap {args.cap})")
    if cost > args.cap:
        raise SystemExit("Over the credit cap; nothing fetched.")
    games = 0
    for d in days:
        games += len(fetch(d)["games"])
    print(f"Spreads for {games} games on {len(days)} dates")


if __name__ == "__main__":
    main()
