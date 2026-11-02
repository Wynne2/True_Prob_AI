"""Pure arbitrage logic: data model, basket/pair strategies, sports matching, depth walk.

An arb here is a set of legs whose combined fee-inclusive cost is below a payout that is
guaranteed whatever happens. No model probabilities are involved.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from odds.prediction_market import effective_cost

KALSHI_ROUNDING = 0.01  # Kalshi rounds each order's fee up to the cent; charged per leg per level


@dataclass
class Leg:
    venue: str  # "kalshi" | "polymarket"
    ref: str  # Kalshi "TICKER:yes|no"; Polymarket CLOB token id
    ask: float  # screen price before fees
    fee_rate: float  # fee per share = fee_rate * p * (1 - p)
    label: str = ""
    url: str = ""
    min_size: float = 1.0

    def cost(self, price: Optional[float] = None) -> float:
        return effective_cost(self.ask if price is None else price, self.fee_rate)


@dataclass
class Outcome:
    label: str
    yes: list[Leg] = field(default_factory=list)  # one leg per venue offering it
    no: list[Leg] = field(default_factory=list)
    id: str = ""  # Kalshi ticker / Polymarket conditionId (":i" for named two-outcome markets)
    code: str = ""  # team code from the ticker/slug, games only


@dataclass
class Event:
    venue: str  # "kalshi" | "polymarket" | "cross"
    id: str
    title: str
    close: Optional[datetime]
    exclusive: bool  # at most one outcome can win
    complete: bool  # exactly one outcome must win (every outcome is listed, no void)
    outcomes: list[Outcome]
    url: str = ""
    league: str = ""
    game_date: Optional[date] = None  # set only for moneyline game events


@dataclass
class Candidate:
    kind: str
    title: str
    legs: list[Leg]
    payout: float
    close: Optional[datetime]

    @property
    def unit_cost(self) -> float:
        return sum(leg.cost() for leg in self.legs)

    @property
    def edge(self) -> float:
        return self.payout - self.unit_cost


def _best(legs: list[Leg]) -> Optional[Leg]:
    return min(legs, key=lambda leg: leg.cost()) if legs else None


def all_no(ev: Event) -> Optional[Candidate]:
    """Buy NO on every outcome: pays >= n-1 whenever at most one outcome wins."""
    legs = [_best(o.no) for o in ev.outcomes]
    if not ev.exclusive or len(legs) < 2 or None in legs:
        return None
    return Candidate("all-no", ev.title, legs, len(legs) - 1, ev.close)


def all_yes(ev: Event) -> Optional[Candidate]:
    """Buy YES on every outcome: pays 1 only if the outcome list is complete."""
    legs = [_best(o.yes) for o in ev.outcomes]
    if not (ev.exclusive and ev.complete) or len(legs) < 2 or None in legs:
        return None
    return Candidate("all-yes", ev.title, legs, 1.0, ev.close)


def cross_pairs(ev: Event) -> Iterable[Candidate]:
    """YES on one venue + NO on the other for the same outcome. Same-venue pairs never beat $1."""
    if not ev.complete:  # a named two-outcome market's "NO" is the other side, so voids/ties break it
        return
    for o in ev.outcomes:
        pairs = [(y, n) for y in o.yes for n in o.no if y.venue != n.venue]
        if pairs:
            y, n = min(pairs, key=lambda p: p[0].cost() + p[1].cost())
            yield Candidate("cross-pair", f"{ev.title}: {o.label}", [y, n], 1.0, ev.close)


def candidates(events: Iterable[Event]) -> list[Candidate]:
    """Every strategy on every event, keeping only positive screen edge."""
    out = []
    for ev in events:
        found = [all_no(ev), all_yes(ev)] + list(cross_pairs(ev))
        for c in found:
            if c is None or c.edge <= 0:
                continue
            if ev.venue == "cross" and len({leg.venue for leg in c.legs}) < 2:
                continue  # already covered by the single-venue event
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Depth
# ---------------------------------------------------------------------------

def walk(ladders: list[list[tuple[float, float]]], legs: list[Leg], payout: float) -> tuple[float, float, float]:
    """Buy one unit of every leg level by level while the unit cost stays below payout.

    ``ladders`` are ask ladders [(price, size)] sorted ascending, one per leg.
    Returns (contracts, total cost, locked profit) with Kalshi fee rounding charged.
    """
    idx = [0] * len(ladders)
    left = [lad[0][1] if lad else 0.0 for lad in ladders]
    contracts = cost = 0.0
    kalshi_levels = set()
    while ladders and all(i < len(lad) for i, lad in zip(idx, ladders)):
        unit = sum(leg.cost(lad[i][0]) for leg, lad, i in zip(legs, ladders, idx))
        if unit >= payout:
            break
        q = min(left)
        contracts += q
        cost += q * unit
        for k, leg in enumerate(legs):
            if leg.venue == "kalshi":
                kalshi_levels.add((k, idx[k]))
            left[k] -= q
            if left[k] <= 1e-9:
                idx[k] += 1
                left[k] = ladders[k][idx[k]][1] if idx[k] < len(ladders[k]) else 0.0
    cost += KALSHI_ROUNDING * len(kalshi_levels)
    return round(contracts, 2), round(cost, 2), round(contracts * payout - cost, 2)


def annualized(profit: float, cost: float, close: Optional[datetime], now: datetime) -> float:
    """Return on capital per year; anything settling within a day counts as one day."""
    if cost <= 0:
        return 0.0
    days = max((close - now).total_seconds() / 86400, 1.0) if close else 365.0
    return profit / cost * 365 / days


# ---------------------------------------------------------------------------
# Cross-venue sports matching (moneyline only, both teams, same date)
# ---------------------------------------------------------------------------

# Extra words that turn one team into a different one ("Kansas" vs "Kansas State").
_GUARD = {
    "state", "st", "tech", "am", "city", "united", "christian", "southern", "northern",
    "eastern", "western", "central", "north", "south", "east", "west", "oh", "fl",
    "women", "w", "ii", "b", "u21", "u23", "u19",
}
_TIE = {"tie", "draw"}


def tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return set(re.findall(r"[a-z0-9]+", text.replace("&", "")))


def _is_tie(label: str) -> bool:
    return bool(tokens(label.split("(")[0]) & _TIE)


def names_match(a: str, b: str) -> bool:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    small, big = sorted((ta, tb), key=len)
    return small <= big and not (big - small) & _GUARD


def map_outcomes(k: Event, p: Event) -> Optional[list[int]]:
    """Index of the Polymarket outcome for each Kalshi outcome, or None unless it is a bijection."""
    if len(k.outcomes) != len(p.outcomes):
        return None
    codes_ok = bool(p.league) and p.league.upper() in k.league
    mapping = []
    for ko in k.outcomes:
        if _is_tie(ko.label):
            hits = [j for j, po in enumerate(p.outcomes) if _is_tie(po.label)]
        else:
            hits = [
                j for j, po in enumerate(p.outcomes)
                if not _is_tie(po.label) and (
                    names_match(ko.label, po.label)
                    or (codes_ok and ko.code and ko.code.lower() == po.code.lower())
                )
            ]
        if len(hits) != 1:
            return None
        mapping.append(hits[0])
    if len(set(mapping)) != len(mapping):
        return None
    # Same game priced on both venues: a swapped or wrong mapping shows up as a big price gap.
    for ko, j in zip(k.outcomes, mapping):
        ky, py = _best(ko.yes), _best(p.outcomes[j].yes)
        if ky and py and abs(ky.ask - py.ask) > 0.15:
            return None
    return mapping


def match_games(kalshi: list[Event], poly: list[Event]) -> list[Event]:
    """Merge each Kalshi game with its unique Polymarket twin into one cross-venue event."""
    index = defaultdict(list)
    for p in poly:
        if p.game_date:
            index[p.game_date].append(p)
    pairs = []
    for k in kalshi:
        if not k.game_date:
            continue
        near = [p for d in (-1, 0, 1) for p in index[k.game_date + timedelta(days=d)]]
        hits = [(p, m) for p in near if (m := map_outcomes(k, p))]
        if len(hits) == 1:
            pairs.append((k, *hits[0]))
    # A Polymarket game claimed by two Kalshi events (e.g. a doubleheader) is ambiguous.
    claimed = defaultdict(int)
    for _, p, _ in pairs:
        claimed[p.id] += 1
    merged = []
    for k, p, mapping in pairs:
        if claimed[p.id] > 1:
            continue
        outcomes = [
            Outcome(ko.label, ko.yes + p.outcomes[j].yes, ko.no + p.outcomes[j].no, ko.id)
            for ko, j in zip(k.outcomes, mapping)
        ]
        closes = [c for c in (k.close, p.close) if c]
        merged.append(Event(
            "cross", f"{k.id}|{p.id}", f"{k.title} [Kalshi+Polymarket]", max(closes) if closes else None,
            exclusive=True, complete=k.complete and p.complete, outcomes=outcomes,
            url=f"{k.url} | {p.url}", league=k.league, game_date=k.game_date,
        ))
    return merged


# ---------------------------------------------------------------------------
# Human-confirmed pairs (everything that is not a structured sports game)
# ---------------------------------------------------------------------------

def confirmed_pairs(pairs: list[dict], kalshi: list[Event], poly: list[Event]) -> list[Event]:
    """Build cross events from ``data/arb_pairs.json`` entries a human verified share one rulebook."""
    by_id = {o.id: (ev, o) for ev in kalshi + poly for o in ev.outcomes}
    out = []
    for pair in pairs:
        k, p = by_id.get(pair.get("kalshi_ticker")), by_id.get(pair.get("polymarket_id"))
        if not k or not p:
            continue
        (kev, ko), (pev, po) = k, p
        same = pair.get("yes_maps_to", "yes") == "yes"
        outcome = Outcome(ko.label, ko.yes + (po.yes if same else po.no), ko.no + (po.no if same else po.yes), ko.id)
        closes = [c for c in (kev.close, pev.close) if c]
        # complete=True: the reviewer confirmed YES/NO are exact complements on both venues.
        out.append(Event("cross", f"{ko.id}|{po.id}", f"{kev.title}: {ko.label} [confirmed pair]",
                         max(closes) if closes else None, True, True, [outcome], f"{kev.url} | {pev.url}"))
    return out


_STOP = set("the will and for with from that this than what when who which by of in on at to a an be is are or market yes no vs".split())


def propose_pairs(kalshi: list[Event], poly: list[Event], limit: int = 30) -> list[dict]:
    """Fuzzy title matches for a human to review. Never priced as arbs."""
    def toks(text: str) -> set[str]:
        return {w for w in tokens(text) if len(w) > 2 and w not in _STOP}

    index, info = defaultdict(set), []
    for ev in poly:
        for o in ev.outcomes:
            if ":" in o.id or not o.yes:
                continue
            t = toks(f"{ev.title} {o.label}")
            info.append((t, ev, o))
            for w in t:
                index[w].add(len(info) - 1)
    out = []
    for ev in kalshi:
        if ev.game_date:
            continue  # handled by match_games
        for o in ev.outcomes:
            kt = toks(f"{ev.title} {o.label}")
            rare = sorted(kt, key=lambda w: len(index.get(w, ())))[:3]
            for i in {i for w in rare if 0 < len(index.get(w, ())) < 200 for i in index[w]}:
                pt, pev, po = info[i]
                score = len(kt & pt) / len(kt | pt)
                close_gap = abs((ev.close - pev.close).days) if ev.close and pev.close else 99
                if score >= 0.6 and close_gap <= 3:
                    out.append({"score": round(score, 2), "kalshi_ticker": o.id, "kalshi": f"{ev.title}: {o.label}",
                                "polymarket_id": po.id, "polymarket": f"{pev.title}: {po.label}",
                                "urls": f"{ev.url} | {pev.url}"})
    return sorted(out, key=lambda r: -r["score"])[:limit]
