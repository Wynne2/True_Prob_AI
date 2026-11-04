"""
Historical BALLDONTLIE advanced box scores.

``GET /nba/v2/stats/advanced?period=0`` is full-game. Counting stats still come
from ``GET /v1/stats``. Season-average endpoints are not called.
"""

from __future__ import annotations

from datetime import date

from services.cache_service import get_cache

_CACHE = get_cache("balldontlie_history", default_ttl=0)

# 2024-25 regular season ends 2025-04-13. February 2026 is the validation
# window. March 2026 is not fetched.
HISTORY_WINDOWS = (
    (date(2024, 10, 1), date(2024, 10, 31), "2024-25"),
    (date(2024, 11, 1), date(2024, 11, 30), "2024-25"),
    (date(2024, 12, 1), date(2024, 12, 31), "2024-25"),
    (date(2025, 1, 1), date(2025, 1, 31), "2024-25"),
    (date(2025, 2, 1), date(2025, 2, 28), "2024-25"),
    (date(2025, 3, 1), date(2025, 3, 31), "2024-25"),
    (date(2025, 4, 1), date(2025, 4, 13), "2024-25"),
    (date(2025, 10, 1), date(2025, 10, 31), "2025-26"),
    (date(2025, 11, 1), date(2025, 11, 30), "2025-26"),
    (date(2025, 12, 1), date(2025, 12, 31), "2025-26"),
    (date(2026, 1, 1), date(2026, 1, 30), "2025-26"),
    (date(2026, 2, 1), date(2026, 2, 28), "2025-26"),
)

TRACKED_FEATURES = (
    "pace",
    "possessions",
    "usage_percentage",
    "offensive_rating",
    "defensive_rating",
    "rebound_chances_total",
    "rebound_chances_off",
    "rebound_chances_def",
    "passes",
    "touches",
    "contested_fga",
    "uncontested_fga",
    "contested_shots",
    "potential_assists",
)


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_minutes(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if ":" in text:
        minutes, seconds = text.split(":", 1)
        try:
            return int(minutes) + int(seconds) / 60.0
        except ValueError:
            return None
    return _num(text)


def flatten_advanced_row(row: dict, season: str) -> dict:
    """Keep the fields the backtest can actually read. Do not invent missing ones."""
    player = row.get("player") if isinstance(row.get("player"), dict) else {}
    team = row.get("team") if isinstance(row.get("team"), dict) else {}
    game = row.get("game") if isinstance(row.get("game"), dict) else {}
    home = game.get("home_team") if isinstance(game.get("home_team"), dict) else {}
    away = game.get("visitor_team") if isinstance(game.get("visitor_team"), dict) else {}
    team_id = "" if team.get("id") is None else str(team.get("id"))
    home_id = "" if home.get("id") is None else str(home.get("id"))
    away_id = "" if away.get("id") is None else str(away.get("id"))
    if team_id and team_id == home_id:
        opponent_id = away_id
    elif team_id and team_id == away_id:
        opponent_id = home_id
    else:
        opponent_id = ""
    flat = {
        "season": season,
        "period": row.get("period"),
        "player_id": "" if player.get("id") is None else str(player.get("id")),
        "player_name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
        "position": str(player.get("position") or ""),
        "team_id": team_id,
        "team_abbr": str(team.get("abbreviation") or ""),
        "opponent_team_id": opponent_id,
        "game_id": "" if game.get("id") is None else str(game.get("id")),
        "game_date": str(game.get("date") or "")[:10],
        "potential_assists": row.get("potential_assists", None) if "potential_assists" in row else None,
    }
    for key in TRACKED_FEATURES:
        if key == "potential_assists":
            continue
        flat[key] = _num(row.get(key))
    off = flat.get("rebound_chances_off")
    deff = flat.get("rebound_chances_def")
    total = flat.get("rebound_chances_total")
    flat["rebound_chances_inconsistent"] = (
        None not in (off, deff, total) and abs((off or 0) + (deff or 0) - (total or 0)) > 0.01
    )
    return flat


def flatten_box_row(row: dict, season: str) -> dict:
    from data.loaders.nba_api_loader import map_box_score_to_backtest_log

    base = map_box_score_to_backtest_log(row)
    game = row.get("game") if isinstance(row.get("game"), dict) else {}
    base["season"] = season
    base["game_id"] = "" if game.get("id") is None else str(game.get("id"))
    base["min"] = _parse_minutes(base.get("min"))
    return base


def _paginate(path: str, start: date, end: date, extra: dict | None = None) -> list[dict]:
    from data.loaders.nba_api_loader import _client

    params = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "per_page": 100,
    }
    if extra:
        params.update(extra)
    return _client().paginate(path, params)


def load_historical_logs() -> tuple[list[dict], dict]:
    """
    Joined player-game logs. Cached per month. A second run does not refetch.

    March 2026 is not part of this default load. The accuracy test fetches it
    only after its weights are frozen.
    """
    return _load_windows(HISTORY_WINDOWS)


def load_march_2026_logs() -> list[dict]:
    """Box scores for the untouched March test. Not used to choose weights."""
    return _load_windows(((date(2026, 3, 1), date(2026, 3, 31), "2025-26"),))[0]


def _load_windows(windows) -> tuple[list[dict], dict]:
    advanced: list[dict] = []
    boxes: list[dict] = []
    windows_loaded = []
    for start, end, season in windows:
        advanced.extend(_cached_window("advanced", start, end, season))
        boxes.extend(_cached_window("box", start, end, season))
        windows_loaded.append(f"{start.isoformat()}_{end.isoformat()}")

    box_index = {
        (row.get("player_id"), row.get("game_id")): row
        for row in boxes
        if row.get("player_id") and row.get("game_id")
    }
    joined = []
    matched = 0
    for row in advanced:
        if row.get("period") not in (0, None):
            continue
        box = box_index.get((row.get("player_id"), row.get("game_id")))
        merged = dict(row)
        if box:
            matched += 1
            for key in ("min", "pts", "reb", "ast", "fg3m", "fga", "fg3a", "fta"):
                merged[key] = box.get(key)
            if not merged.get("opponent_team_id"):
                merged["opponent_team_id"] = box.get("opponent_team_id") or ""
            if not merged.get("position"):
                merged["position"] = box.get("position") or ""
            if not merged.get("player_name"):
                merged["player_name"] = box.get("player_name") or ""
        joined.append(merged)

    coverage = feature_coverage(joined)
    coverage["advanced_rows"] = len(advanced)
    coverage["box_rows"] = len(boxes)
    coverage["joined_rows"] = len(joined)
    coverage["box_match_rate"] = (matched / len(advanced)) if advanced else 0.0
    coverage["windows"] = windows_loaded
    return joined, coverage


def _cached_window(kind: str, start: date, end: date, season: str) -> list[dict]:
    key = f"{kind}_{start.isoformat()}_{end.isoformat()}"
    cached = _CACHE.get(key)
    if isinstance(cached, list):
        return cached
    print(f"Fetching {kind} {start.isoformat()} to {end.isoformat()}", flush=True)
    if kind == "advanced":
        raw = _paginate("nba/v2/stats/advanced", start, end, {"period": 0})
        slim = [flatten_advanced_row(row, season) for row in raw]
    else:
        raw = _paginate("v1/stats", start, end)
        slim = [flatten_box_row(row, season) for row in raw]
    _CACHE.set(key, slim, ttl_seconds=0)
    return slim


def feature_coverage(rows: list[dict]) -> dict:
    """Non-null and non-zero rates. A zero is a real zero, not a missing value."""
    n = len(rows)
    features = {}
    for key in TRACKED_FEATURES:
        present = sum(1 for row in rows if key in row)
        nonnull = sum(1 for row in rows if row.get(key) is not None)
        nonzero = sum(
            1 for row in rows
            if isinstance(row.get(key), (int, float)) and float(row.get(key)) != 0.0
        )
        features[key] = {
            "rows": n,
            "present_rate": (present / n) if n else 0.0,
            "nonnull_rate": (nonnull / n) if n else 0.0,
            "nonzero_rate": (nonzero / n) if n else 0.0,
        }
    inconsistent = sum(1 for row in rows if row.get("rebound_chances_inconsistent"))
    features["rebound_chances_inconsistent"] = {
        "rows": inconsistent,
        "rate": (inconsistent / n) if n else 0.0,
        "note": "Off plus def did not equal total. The returned total was kept.",
    }
    features["potential_assists"]["note"] = (
        "The advanced payload has no potential_assists field. Passes were not used as a substitute."
    )
    return {"features": features}
