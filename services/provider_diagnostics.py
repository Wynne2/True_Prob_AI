"""Read-only provider contract checks without global HTTP monkey-patching."""

from __future__ import annotations

from datetime import date
from typing import Any

from config import get_bdl_config
from providers.balldontlie_client import BallDontLieClient


def diagnose_provider(
    game_date: date, api_key: str, *, client: BallDontLieClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch uncached schedule and one game's props; keep only bounded previews."""
    client = client or BallDontLieClient(api_key, get_bdl_config())
    schedule, fetched_at = client.get_all(
        "/nba/v1/games",
        params={"dates[]": [game_date.isoformat()], "per_page": 100},
    )
    checks: list[dict[str, Any]] = [{
        "endpoint": "NBA schedule",
        "records": len(schedule),
        "fetched_at": fetched_at,
        "preview": schedule[:2],
    }]
    if schedule:
        game_id = schedule[0].get("id")
        if game_id is None:
            raise ValueError("BALLDONTLIE schedule is missing a game identifier.")
        props, fetched_at = client.get_all(
            "/nba/v2/odds/player_props",
            params={"game_id": str(game_id)},
        )
        checks.append({
            "endpoint": "NBA player props (first game)",
            "records": len(props),
            "fetched_at": fetched_at,
            "preview": props[:2],
        })
    return checks
