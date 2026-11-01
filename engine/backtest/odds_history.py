"""
Historical Odds API access.

The official historical event-odds endpoint is
``GET /v4/historical/sports/{sport}/events/{eventId}/odds``.
It costs 10 credits per market per region per event.
The historical events list costs 1 credit, and 0 if it returns no events.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from domain.constants import PROP_ALIAS_MAP
from domain.entities import OddsLine
from domain.enums import BookName, DataSource

_ODDS_CACHE = Path("data/cache/odds_history")

HISTORICAL_EVENTS_COST = 1
HISTORICAL_EVENT_ODDS_COST_PER_MARKET_PER_REGION = 10
SAMPLE_MARKETS = (
    "player_points,player_rebounds,player_assists,player_threes"
)
SAMPLE_REGION = "us"

_BOOK_KEYS = {
    "fanduel": BookName.FANDUEL,
    "draftkings": BookName.DRAFTKINGS,
    "betmgm": BookName.BETMGM,
    "williamhill_us": BookName.CAESARS,
    "bovada": BookName.BOVADA,
    "betrivers": BookName.BETRIVERS,
    "betonlineag": BookName.BETONLINE,
    "pointsbetus": BookName.POINTSBET,
}


def estimate_event_odds_cost(n_events: int, markets: str, regions: str) -> int:
    """Credits for historical event-odds calls. Does not include the events list."""
    n_markets = len([m for m in markets.split(",") if m.strip()])
    n_regions = len([r for r in regions.split(",") if r.strip()])
    return (
        HISTORICAL_EVENT_ODDS_COST_PER_MARKET_PER_REGION
        * n_markets
        * n_regions
        * max(n_events, 0)
    )


def estimate_sample_cost(n_events: int = 1) -> dict:
    odds_cost = estimate_event_odds_cost(n_events, SAMPLE_MARKETS, SAMPLE_REGION)
    return {
        "events_list_credits": HISTORICAL_EVENTS_COST,
        "event_odds_credits": odds_cost,
        "total_credits": HISTORICAL_EVENTS_COST + odds_cost,
        "markets": SAMPLE_MARKETS,
        "regions": SAMPLE_REGION,
        "events": n_events,
        "note": (
            "Historical event odds cost 10 credits per market per region per event. "
            "Featured-market historical odds are a different endpoint and are not used."
        ),
    }


def snapshot_target(tip_off: datetime) -> datetime:
    """Two hours before tip, in UTC."""
    tip = tip_off if tip_off.tzinfo else tip_off.replace(tzinfo=timezone.utc)
    return tip.astimezone(timezone.utc) - timedelta(hours=2)


def snapshot_is_pregame(snapshot: datetime, tip_off: datetime) -> bool:
    """The returned snapshot must be strictly before tip-off."""
    snap = snapshot if snapshot.tzinfo else snapshot.replace(tzinfo=timezone.utc)
    tip = tip_off if tip_off.tzinfo else tip_off.replace(tzinfo=timezone.utc)
    return snap.astimezone(timezone.utc) < tip.astimezone(timezone.utc)


def parse_historical_bookmakers(event: dict, event_id: str) -> tuple[list[OddsLine], set[str]]:
    """Parse one historical event payload into two-sided lines. One-sided rows are counted, not invented."""
    lines: list[OddsLine] = []
    unsupported: set[str] = set()
    for book_data in event.get("bookmakers") or []:
        book_key = book_data.get("key") or ""
        book = _BOOK_KEYS.get(book_key, BookName.OTHER)
        for market in book_data.get("markets") or []:
            market_key = market.get("key") or ""
            prop_type = PROP_ALIAS_MAP.get(market_key)
            if prop_type is None:
                if market_key:
                    unsupported.add(market_key)
                continue
            grouped: dict[tuple, dict] = {}
            for outcome in market.get("outcomes") or []:
                name = str(outcome.get("name") or "")
                player = str(outcome.get("description") or "")
                point = outcome.get("point")
                price = outcome.get("price")
                if point is None or price is None or not player:
                    continue
                key = (player, float(point))
                slot = grouped.setdefault(key, {"player_name": player, "point": float(point)})
                if name.lower() == "over":
                    slot["over_odds"] = int(price)
                elif name.lower() == "under":
                    slot["under_odds"] = int(price)
            for (_player, _point), slot in grouped.items():
                if "over_odds" not in slot or "under_odds" not in slot:
                    continue
                lines.append(OddsLine(
                    book=book,
                    player_id=slot["player_name"].lower().replace(" ", "_"),
                    player_name=slot["player_name"],
                    prop_type=prop_type,
                    line=float(slot["point"]),
                    over_odds=int(slot["over_odds"]),
                    under_odds=int(slot["under_odds"]),
                    game_id=event_id,
                    data_source=DataSource.ODDS_API,
                    book_key=book_key,
                ))
    return lines, unsupported


def _provider():
    from config import get_credentials
    from providers.odds_api_provider import OddsAPIProvider

    key = get_credentials().odds_api_key
    if not key:
        raise RuntimeError("THE_ODDS_API_KEY is not set")
    return OddsAPIProvider(key)


def _cache_file(name: str) -> Path:
    _ODDS_CACHE.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)
    return _ODDS_CACHE / f"{safe}.json"


def _read_cache(name: str) -> dict | None:
    path = _cache_file(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(name: str, payload: dict) -> None:
    _cache_file(name).write_text(json.dumps(payload, default=str), encoding="utf-8")


def odds_snapshot_cached(
    event_id: str,
    at: datetime,
    markets: str = SAMPLE_MARKETS,
    regions: str = SAMPLE_REGION,
) -> bool:
    stamp = at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _cache_file(f"odds_{event_id}_{stamp}_{regions}_{markets}").exists()


def fetch_historical_events(at: datetime, *, use_cache: bool = True) -> dict:
    """
    List NBA events at a historical timestamp.

    Returns the raw wrapper plus quota. Does not download prop prices.
    """
    stamp = at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cached = _read_cache(f"events_{stamp}") if use_cache else None
    if cached is not None:
        cached["cached"] = True
        cached["quota"] = {"remaining": None, "used": None, "last": "0"}
        return cached
    provider = _provider()
    payload = provider._get(
        "historical/sports/basketball_nba/events",
        date=stamp,
    )
    quota = dict(provider.last_quota)
    if not isinstance(payload, dict):
        return {
            "available": False,
            "reason": "Historical events response was empty or not JSON.",
            "events": [],
            "quota": quota,
            "requested_at": stamp,
        }
    events = payload.get("data") or []
    result = {
        "available": True,
        "events": events,
        "timestamp": payload.get("timestamp"),
        "previous_timestamp": payload.get("previous_timestamp"),
        "next_timestamp": payload.get("next_timestamp"),
        "quota": quota,
        "requested_at": stamp,
        "credits_charged_header": quota.get("last"),
        "cached": False,
    }
    _write_cache(f"events_{stamp}", result)
    return result


def fetch_historical_event_odds(
    event_id: str,
    at: datetime,
    markets: str = SAMPLE_MARKETS,
    regions: str = SAMPLE_REGION,
) -> dict:
    """One historical event-odds snapshot. Refuses to invent prices if the call fails."""
    stamp = at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache_name = f"odds_{event_id}_{stamp}_{regions}_{markets}"
    cached = _read_cache(cache_name)
    if cached is not None:
        cached["lines"] = [_line_from_dict(row) for row in cached.get("lines") or []]
        cached["cached"] = True
        cached["quota"] = {"remaining": None, "used": None, "last": "0"}
        return cached
    provider = _provider()
    payload = provider._get(
        f"historical/sports/basketball_nba/events/{event_id}/odds",
        date=stamp,
        regions=regions,
        markets=markets,
        oddsFormat="american",
        dateFormat="iso",
    )
    quota = dict(provider.last_quota)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return {
            "available": False,
            "reason": "Historical event odds were not returned. Prices were not fabricated.",
            "lines": [],
            "quota": quota,
            "requested_at": stamp,
        }
    event = payload["data"]
    lines, unsupported = parse_historical_bookmakers(event, event_id)
    result = {
        "available": True,
        "lines": [_line_to_dict(line) for line in lines],
        "unsupported_markets": sorted(unsupported),
        "timestamp": payload.get("timestamp"),
        "home_team": event.get("home_team"),
        "away_team": event.get("away_team"),
        "commence_time": event.get("commence_time"),
        "bookmakers": sorted({line.book_key for line in lines if line.book_key}),
        "quota": quota,
        "requested_at": stamp,
        "cached": False,
    }
    _write_cache(cache_name, result)
    result["lines"] = lines
    return result


def _line_to_dict(line: OddsLine) -> dict:
    return {
        "book": line.book.value,
        "player_name": line.player_name,
        "prop_type": line.prop_type.value,
        "line": line.line,
        "over_odds": line.over_odds,
        "under_odds": line.under_odds,
        "game_id": line.game_id,
        "book_key": line.book_key,
    }


def _line_from_dict(row: dict) -> OddsLine:
    from domain.enums import PropType

    return OddsLine(
        book=_BOOK_KEYS.get(row.get("book_key") or "", BookName.OTHER),
        player_id=str(row.get("player_name") or "").lower().replace(" ", "_"),
        player_name=str(row.get("player_name") or ""),
        prop_type=PropType(row["prop_type"]),
        line=float(row["line"]),
        over_odds=int(row["over_odds"]),
        under_odds=int(row["under_odds"]),
        game_id=str(row.get("game_id") or ""),
        book_key=str(row.get("book_key") or ""),
        data_source=DataSource.ODDS_API,
    )
