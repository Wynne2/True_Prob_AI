"""One full scan: fetch both venues, find candidates, verify order-book depth, log real arbs."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import requests

from arbitrage import finder, venues

ROOT = Path(__file__).resolve().parent.parent
PAIRS_FILE = ROOT / "data" / "arb_pairs.json"
LOG_DIR = ROOT / "data" / "arbs"


SLIP_PER_LEG = 0.01  # legs fill one after another; each may fill one tick (1 cent) worse than the book read


def _row(c: finder.Candidate, contracts: float, cost: float, profit: float, now: datetime) -> dict:
    return {
        "kind": c.kind,
        "title": c.title,
        "contracts": contracts,
        "cost": cost,
        "payout": round(contracts * c.payout, 2),
        "profit": profit,
        "profit_if_slipped": round(profit - contracts * SLIP_PER_LEG * len(c.legs), 2),
        "roi": round(profit / cost, 4) if cost else 0.0,
        "annualized": round(finder.annualized(profit, cost, c.close, now), 4),
        "closes": c.close.isoformat() if c.close else None,
        "legs": [f"{leg.venue}: {leg.label} @ {leg.ask:.3f} (fee rate {leg.fee_rate:g})" for leg in c.legs],
        "urls": sorted({leg.url for leg in c.legs}),
    }


def scan(
    min_profit: float = 5.0,
    min_annual: float = 0.10,
    max_checks: int = 150,
    which: Iterable[str] = ("kalshi", "polymarket"),
    session: requests.Session | None = None,
    log: bool = True,
) -> dict:
    """Return {"arbs": [...], "near": [...], "stats": {...}}; arbs pass every filter, near are depth-real but small."""
    s = session or requests.Session()
    which = set(which)
    now = datetime.now(timezone.utc)
    kalshi = venues.fetch_kalshi(s) if "kalshi" in which else []
    poly = venues.fetch_polymarket(s) if "polymarket" in which else []
    games = finder.match_games(kalshi, poly)
    pairs = finder.confirmed_pairs(json.loads(PAIRS_FILE.read_text()) if PAIRS_FILE.exists() else [], kalshi, poly)
    # Listing prices lag the books by a few cents; cross-venue screens are re-priced from live books.
    for refresh in (venues.refresh_polymarket, venues.refresh_kalshi):
        try:
            refresh(s, games + pairs)
        except (requests.RequestException, ValueError):
            pass  # listing quotes are still usable; depth is re-read per candidate anyway
    cands = [
        c for c in finder.candidates(kalshi + poly + games + pairs)
        if not c.close or c.close > now + timedelta(minutes=10)
    ]
    cands.sort(key=lambda c: -c.edge)

    rows, seen, errors = [], set(), 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for c in cands[:max_checks]:
            key = frozenset(leg.ref for leg in c.legs)
            if key in seen:
                continue
            seen.add(key)
            try:
                ladders = list(pool.map(lambda leg: venues.ladder(s, leg), c.legs))
            except (requests.RequestException, ValueError, KeyError):
                errors += 1
                continue
            contracts, cost, profit = finder.walk(ladders, c.legs, c.payout)
            if profit > 0 and contracts >= max(leg.min_size for leg in c.legs):
                rows.append(_row(c, contracts, cost, profit, now))

    rows.sort(key=lambda r: -r["profit"])
    # An arb must still clear min_profit after every leg slips a tick, or it is a bet on execution.
    arbs = [r for r in rows if r["profit_if_slipped"] >= min_profit and r["annualized"] >= min_annual]
    if log and arbs:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / f"{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as fh:
            for r in arbs:
                fh.write(json.dumps({"seen_at": now.isoformat(), **r}) + "\n")
    stats = {
        "kalshi_events": len(kalshi),
        "polymarket_events": len(poly),
        "matched_games": len(games),
        "confirmed_pairs": len(pairs),
        "candidates": len(cands),
        "depth_checked": len(seen),
        "book_errors": errors,
    }
    return {"arbs": arbs, "near": [r for r in rows if r not in arbs], "stats": stats}


def propose(session: requests.Session | None = None, limit: int = 30) -> list[dict]:
    """Fuzzy cross-venue matches for a human to confirm into data/arb_pairs.json."""
    s = session or requests.Session()
    return finder.propose_pairs(venues.fetch_kalshi(s), venues.fetch_polymarket(s), limit)
