"""Fetch every open Kalshi and Polymarket event, normalize to ``finder.Event``, and read order books."""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from typing import Optional

import requests

from arbitrage.finder import Event, Leg, Outcome
from config import get_prediction_market_config
from odds.prediction_market import KALSHI_TAKER_FEE_RATE, to_float
from providers.kalshi_provider import _market_side_prices
from providers.polymarket_provider import _fee_rate, _is_tradeable, _json_list

CLOB_URL = "https://clob.polymarket.com"
_MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
_KALSHI_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
_POLY_GAME_SLUG = re.compile(r"^([a-z0-9]+)-([a-z0-9]+)-([a-z0-9]+)-\d{4}-\d{2}-\d{2}$")
_GAME_SERIES = ("GAME", "MATCH")
# Kalshi has no completeness flag. Game series qualify only when every result is a listed
# market: a TIE market exists, or the league cannot end level.
_NO_TIE_SERIES = {"KXNBAGAME", "KXWNBAGAME", "KXNCAAMBGAME", "KXNCAAWBGAME", "KXMLBGAME", "KXNHLGAME"}


def _get(session: requests.Session, url: str, params: Optional[dict] = None, tries: int = 6) -> dict:
    for attempt in range(tries):
        resp = session.get(url, params=params, timeout=get_prediction_market_config().timeout)
        if resp.status_code == 429 and attempt < tries - 1:
            time.sleep(1 + attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    return {}


def _when(value: object) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T")) if value else None
    except ValueError:
        return None


def _latest(values) -> Optional[datetime]:
    times = [t for t in map(_when, values) if t and t.tzinfo]
    return max(times) if times else None


def _price(value: object) -> Optional[float]:
    p = to_float(value)
    return p if p is not None and 0 < p < 1 else None


# ---------------------------------------------------------------------------
# Kalshi
# ---------------------------------------------------------------------------

def kalshi_fee_rates(session: requests.Session) -> dict[str, float]:
    """Per-series taker fee rate; most series use 0.07, some 0.035 or 0."""
    base = get_prediction_market_config().kalshi_base_url.rstrip("/")
    rates, cursor = {}, None
    for _ in range(100):
        payload = _get(session, f"{base}/series", {"limit": 1000, **({"cursor": cursor} if cursor else {})})
        for s in payload.get("series") or []:
            mult = to_float(s.get("fee_multiplier"))
            rates[s.get("ticker")] = KALSHI_TAKER_FEE_RATE * (1.0 if mult is None else mult)
        cursor = payload.get("cursor")
        if not cursor or not payload.get("series"):
            break
    return rates


def kalshi_event(ev: dict, fee_rates: dict[str, float]) -> Optional[Event]:
    series = ev.get("series_ticker") or ""
    markets = ev.get("markets") or []
    active = [m for m in markets if str(m.get("status", "")).lower() in {"active", "open"}]
    if not active:
        return None
    rate = fee_rates.get(series, KALSHI_TAKER_FEE_RATE)  # unknown series: assume the standard rate
    url = f"https://kalshi.com/markets/{series.lower()}"
    outcomes = []
    for m in active:
        ticker = m.get("ticker") or ""
        label = m.get("yes_sub_title") or m.get("title") or ticker
        yes_ask, no_ask = (_price(p) for p in _market_side_prices(m))
        outcomes.append(Outcome(
            label,
            [Leg("kalshi", f"{ticker}:yes", yes_ask, rate, f"YES {label}", url)] if yes_ask else [],
            [Leg("kalshi", f"{ticker}:no", no_ask, rate, f"NO {label}", url)] if no_ask else [],
            ticker, ticker.rsplit("-", 1)[-1],
        ))
    game_date = None
    is_game = series.endswith(_GAME_SERIES)
    if is_game and (dm := _KALSHI_DATE.search(ev.get("event_ticker") or "")) and dm.group(2) in _MONTHS:
        try:
            game_date = date(2000 + int(dm.group(1)), _MONTHS[dm.group(2)], int(dm.group(3)))
        except ValueError:
            pass
    has_tie = any(o.code == "TIE" for o in outcomes)
    complete = (
        is_game and len(active) == len(markets) >= 2 and (has_tie or series in _NO_TIE_SERIES)
    )
    return Event(
        "kalshi", ev.get("event_ticker") or "", ev.get("title") or "",
        _latest(m.get("expected_expiration_time") or m.get("close_time") for m in active),
        exclusive=bool(ev.get("mutually_exclusive")), complete=complete, outcomes=outcomes,
        url=url, league=series, game_date=game_date,
    )


def fetch_kalshi(session: requests.Session) -> list[Event]:
    base = get_prediction_market_config().kalshi_base_url.rstrip("/")
    fee_rates = kalshi_fee_rates(session)
    events, cursor = [], None
    for _ in range(1000):
        params = {"status": "open", "with_nested_markets": "true", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        payload = _get(session, f"{base}/events", params)
        for raw in payload.get("events") or []:
            if ev := kalshi_event(raw, fee_rates):
                events.append(ev)
        cursor = payload.get("cursor")
        if not cursor or not payload.get("events"):
            break
    return events


def _kalshi_asks(book: dict, side: str) -> list[tuple[float, float]]:
    """Kalshi books hold bids only: YES ask = 1 - NO bid."""
    bids = (book or {}).get("no_dollars" if side == "yes" else "yes_dollars") or []
    return sorted((round(1 - float(p), 4), float(q)) for p, q in bids if float(q) > 0)


def kalshi_ladder(session: requests.Session, ref: str) -> list[tuple[float, float]]:
    """Ask ladder for one side."""
    ticker, side = ref.rsplit(":", 1)
    base = get_prediction_market_config().kalshi_base_url.rstrip("/")
    return _kalshi_asks(_get(session, f"{base}/markets/{ticker}/orderbook").get("orderbook_fp"), side)


def refresh_kalshi(session: requests.Session, events: list[Event]) -> None:
    """Replace the event listing's asks with live book asks (100 tickers per call); drop empty sides."""
    base = get_prediction_market_config().kalshi_base_url.rstrip("/")
    legs = {leg.ref: leg for ev in events for o in ev.outcomes for leg in o.yes + o.no if leg.venue == "kalshi"}
    tickers = sorted({ref.rsplit(":", 1)[0] for ref in legs})
    books = {}
    for i in range(0, len(tickers), 100):
        payload = _get(session, f"{base}/markets/orderbooks", {"tickers": tickers[i:i + 100]})
        books.update({b.get("ticker"): b.get("orderbook_fp") or {} for b in payload.get("orderbooks") or []})
    empty = set()
    for ref, leg in legs.items():
        ticker, side = ref.rsplit(":", 1)
        if ticker not in books:
            continue  # not returned: keep the listing price; depth is re-read per candidate anyway
        asks = _kalshi_asks(books[ticker], side)
        if asks:
            leg.ask = asks[0][0]
        else:
            empty.add(ref)
    for ev in events:
        for o in ev.outcomes:
            o.yes = [leg for leg in o.yes if leg.ref not in empty]
            o.no = [leg for leg in o.no if leg.ref not in empty]


# ---------------------------------------------------------------------------
# Polymarket
# ---------------------------------------------------------------------------

def _poly_rate(m: dict) -> Optional[float]:
    schedule = m.get("feeSchedule") or {}
    if isinstance(schedule, dict) and schedule.get("exponent") not in (None, 1, 1.0, "1"):
        return None  # shortcut: unknown fee curve, skipped until Polymarket documents it
    return _fee_rate(m)


def polymarket_events(ev: dict) -> list[Event]:
    slug = ev.get("slug") or ""
    url = f"https://polymarket.com/event/{slug}"
    all_markets = ev.get("markets") or []
    markets = [m for m in all_markets if _is_tradeable(m) and _poly_rate(m) is not None]
    if not markets:
        return []
    game = _POLY_GAME_SLUG.match(slug)
    start = _when(ev.get("startTime") or markets[0].get("gameStartTime"))
    game_day = start.date() if game and start else None
    moneyline = lambda m: m.get("sportsMarketType") == "moneyline"
    close = _latest(m.get("endDate") for m in markets)

    def legs(m: dict, i: int, label: str) -> tuple[list[Leg], list[Leg]]:
        tokens = _json_list(m.get("clobTokenIds"))
        ask, bid = _price(m.get("bestAsk")), to_float(m.get("bestBid"))
        first_ask, second_ask = ask, _price(1 - bid) if bid else None  # second token ask ~ 1 - first bid
        asks = (first_ask, second_ask) if i == 0 else (second_ask, first_ask)
        rate, size = _poly_rate(m), to_float(m.get("orderMinSize")) or 1.0
        mk = lambda tok, a, side: [Leg("polymarket", tok, a, rate, f"{side} {label}", url, size)] if a else []
        if len(tokens) != 2:
            return [], []
        return mk(tokens[i], asks[0], "YES"), mk(tokens[1 - i], asks[1], "NO")

    if ev.get("negRisk"):
        outcomes = []
        for m in markets:
            label = m.get("groupItemTitle") or m.get("question") or ""
            yes, no = legs(m, 0, label)
            outcomes.append(Outcome(label, yes, no, m.get("conditionId") or ""))
        complete = not ev.get("negRiskAugmented") and len(markets) == len(all_markets)
        return [Event("polymarket", slug, ev.get("title") or "", close, True, complete, outcomes, url,
                      game.group(1) if game else "", game_day if all(map(moneyline, markets)) else None)]

    out = []
    for m in markets:
        names = _json_list(m.get("outcomes"))
        cid = m.get("conditionId") or ""
        if [str(n).lower() for n in names] == ["yes", "no"] or len(names) != 2:
            yes, no = legs(m, 0, m.get("question") or "")
            if yes or no:  # plain binary: only useful to confirmed pairs
                out.append(Event("polymarket", cid, m.get("question") or "", close, False, False,
                                 [Outcome(m.get("question") or "", yes, no, cid)], url))
            continue
        # Named two-outcome market ("Thunder vs. Cavaliers"): each name is an outcome.
        codes = game.groups()[1:] if game else ("", "")
        outcomes = []
        for i, name in enumerate(names):
            yes, no = legs(m, i, str(name))
            outcomes.append(Outcome(str(name), yes, no, f"{cid}:{i}", codes[i]))
        game_date = game_day if moneyline(m) else None
        out.append(Event("polymarket", f"{slug}:{cid}", m.get("question") or ev.get("title") or "", close,
                         True, True, outcomes, url, game.group(1) if game else "", game_date))
    return out


def fetch_polymarket(session: requests.Session) -> list[Event]:
    """Keyset paging: offset paging silently stops early on this endpoint."""
    base = get_prediction_market_config().polymarket_base_url.rstrip("/")
    events, cursor = [], None
    for _ in range(1000):
        params = {"closed": "false", "limit": 500}
        if cursor:
            params["after_cursor"] = cursor
        payload = _get(session, f"{base}/events/keyset", params)
        for raw in payload.get("events") or []:
            events.extend(polymarket_events(raw))
        cursor = payload.get("next_cursor")
        if not cursor or not payload.get("events"):
            break
    return events


def polymarket_ladder(session: requests.Session, token: str) -> list[tuple[float, float]]:
    book = _get(session, f"{CLOB_URL}/book", {"token_id": token})
    return sorted((float(a["price"]), float(a["size"])) for a in book.get("asks") or [] if float(a["size"]) > 0)


def refresh_polymarket(session: requests.Session, events: list[Event]) -> None:
    """Replace Gamma's cached asks with live CLOB best asks; drop legs with an empty book."""
    legs = {leg.ref: leg for ev in events for o in ev.outcomes for leg in o.yes + o.no if leg.venue == "polymarket"}
    refs, best = list(legs), {}
    for i in range(0, len(refs), 500):  # the batch endpoint takes 500 tokens per call
        resp = session.post(f"{CLOB_URL}/books", json=[{"token_id": r} for r in refs[i:i + 500]],
                            timeout=get_prediction_market_config().timeout)
        resp.raise_for_status()
        for book in resp.json():
            asks = [float(a["price"]) for a in book.get("asks") or [] if float(a["size"]) > 0]
            best[book.get("asset_id")] = min(asks) if asks else None
    for ref, ask in best.items():
        if ask is not None:
            legs[ref].ask = ask
    empty = {ref for ref, ask in best.items() if ask is None}
    for ev in events:
        for o in ev.outcomes:
            o.yes = [leg for leg in o.yes if leg.ref not in empty]
            o.no = [leg for leg in o.no if leg.ref not in empty]


def ladder(session: requests.Session, leg: Leg) -> list[tuple[float, float]]:
    return kalshi_ladder(session, leg.ref) if leg.venue == "kalshi" else polymarket_ladder(session, leg.ref)
