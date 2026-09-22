"""Deterministic user-facing updates derived from observed local snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from domain.entities import OddsLine


@dataclass(frozen=True)
class ActivityEvent:
    occurred_at: datetime
    kind: str
    message: str
    quote_key: str = ""


def quote_updates(previous: Iterable[OddsLine], current: Iterable[OddsLine]) -> list[ActivityEvent]:
    """Report same-proposition price changes without treating line swaps as updates."""
    def identity(quote: OddsLine) -> tuple[str, str, str, float, str, str]:
        return (
            quote.game_id,
            quote.player_id,
            quote.market_key,
            quote.line,
            quote.book_key,
            quote.period,
        )

    before = {identity(quote): quote for quote in previous}
    updates: list[ActivityEvent] = []
    for quote in current:
        earlier = before.get(identity(quote))
        if earlier is None:
            continue
        if (earlier.over_odds, earlier.under_odds) == (quote.over_odds, quote.under_odds):
            continue
        updates.append(ActivityEvent(
            occurred_at=quote.fetched_at or datetime.now(timezone.utc),
            kind="quote_changed",
            quote_key=quote.provider_quote_id,
            message=(
                f"{quote.player_name} {quote.prop_type.value} {quote.line:g} at "
                f"{quote.book_key} changed from {earlier.over_odds}/{earlier.under_odds} "
                f"to {quote.over_odds}/{quote.under_odds}."
            ),
        ))
    return updates
