from datetime import date, datetime, timedelta, timezone

import pytest

from arbitrage import finder
from arbitrage.finder import Event, Leg, Outcome
from arbitrage.venues import kalshi_event, polymarket_events

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


def leg(ask, venue="polymarket", fee=0.0, ref=None):
    return Leg(venue, ref or f"{venue}-{ask}", ask, fee)


def event(outcomes, exclusive=True, complete=False, venue="polymarket", **kw):
    return Event(venue, "ev", "Test event", NOW + timedelta(days=1), exclusive, complete, outcomes, **kw)


def test_all_no_three_way_hand_computed():
    ev = event([Outcome(x, no=[leg(a)]) for x, a in (("A", 0.60), ("B", 0.70), ("C", 0.65))])
    c = finder.all_no(ev)
    assert c.payout == 2 and c.unit_cost == pytest.approx(1.95) and c.edge == pytest.approx(0.05)
    assert finder.all_no(event(ev.outcomes, exclusive=False)) is None


def test_all_yes_needs_complete_outcome_list():
    outcomes = [Outcome(x, yes=[leg(0.30)]) for x in "ABC"]
    assert finder.all_yes(event(outcomes, complete=False)) is None
    assert finder.all_yes(event(outcomes, complete=True)).edge == pytest.approx(0.10)


def test_leg_cost_is_fee_inclusive():
    assert Leg("kalshi", "", 0.5, 0.07).cost() == pytest.approx(0.5175)


def test_walk_stops_when_unit_cost_reaches_payout():
    ladders = [[(0.40, 10), (0.55, 10)], [(0.50, 5), (0.58, 20)]]
    assert finder.walk(ladders, [leg(0.40), leg(0.50)], 1.0) == (10.0, 9.4, 0.6)
    # Kalshi rounds each order's fee up to the cent: one level touched on the Kalshi leg.
    assert finder.walk(ladders, [leg(0.40, "kalshi"), leg(0.50)], 1.0) == (10.0, 9.41, 0.59)
    assert finder.walk([[], [(0.1, 5)]], [leg(0.1), leg(0.1)], 1.0) == (0.0, 0.0, 0.0)


def test_annualized_return():
    assert finder.annualized(1, 100, NOW + timedelta(days=36.5), NOW) == pytest.approx(0.10)
    assert finder.annualized(1, 100, NOW + timedelta(hours=2), NOW) == pytest.approx(3.65)  # floor of one day


def _kalshi(labels, asks, league="KXUCLGAME"):
    outs = [Outcome(lab, [leg(a, "kalshi", ref=f"K{lab}")], [leg(1 - a + 0.02, "kalshi", ref=f"KN{lab}")],
                    f"K-{lab}", lab[:3].upper()) for lab, a in zip(labels, asks)]
    return event(outs, complete=True, venue="kalshi", league=league, game_date=date(2026, 11, 3))


def _poly(slug, labels, asks):
    outs = [Outcome(lab, [leg(a, ref=f"{slug}{lab}")], [leg(1 - a + 0.01, ref=f"{slug}N{lab}")], f"{slug}{lab}")
            for lab, a in zip(labels, asks)]
    ev = event(outs, complete=True, league="ucl", game_date=date(2026, 11, 3))
    ev.id = slug
    return ev


def test_match_games_needs_both_teams_and_rejects_decoys():
    k = _kalshi(["Galatasaray", "Tie", "Stuttgart"], [0.44, 0.24, 0.33])
    real = _poly("real", ["Galatasaray SK", "Draw (Galatasaray SK vs. VfB Stuttgart)", "VfB Stuttgart"], [0.45, 0.24, 0.30])
    decoy = _poly("decoy", ["Galatasaray SK", "Draw (Galatasaray SK vs. Fenerbahce)", "Fenerbahce"], [0.45, 0.24, 0.33])
    merged = finder.match_games([k], [real, decoy])
    assert len(merged) == 1 and merged[0].id == "ev|real"
    assert [len(o.yes) for o in merged[0].outcomes] == [2, 2, 2]
    # Stuttgart is cheaper on Polymarket (0.30) than Kalshi NO Stuttgart implies: a two-venue pair.
    assert any(c.kind == "cross-pair" for c in finder.candidates(merged))


def test_match_rejects_guard_words_and_price_gaps():
    assert not finder.names_match("Kansas", "Kansas State")
    assert finder.names_match("Stuttgart", "VfB Stuttgart")
    k = _kalshi(["Galatasaray", "Tie", "Stuttgart"], [0.44, 0.24, 0.33])
    swapped = _poly("x", ["Galatasaray SK", "Draw", "VfB Stuttgart"], [0.70, 0.24, 0.05])
    assert finder.match_games([k], [swapped]) == []


def test_cross_pairs_need_complete_event():
    o = Outcome("A", [leg(0.40, "kalshi")], [leg(0.50, "polymarket")])
    assert list(finder.cross_pairs(event([o], complete=False))) == []
    assert [c.edge for c in finder.cross_pairs(event([o], complete=True))] == [pytest.approx(0.1)]


def test_kalshi_event_completeness_and_fee_rate():
    raw = {
        "event_ticker": "KXUCLGAME-26NOV03GALVFB", "series_ticker": "KXUCLGAME", "title": "Galatasaray vs Stuttgart",
        "mutually_exclusive": True,
        "markets": [
            {"ticker": f"KXUCLGAME-26NOV03GALVFB-{c}", "yes_sub_title": c, "status": "active",
             "yes_ask_dollars": "0.40", "no_ask_dollars": "0.62", "close_time": "2026-11-05T17:45:00Z"}
            for c in ("GAL", "VFB", "TIE")
        ],
    }
    ev = kalshi_event(raw, {"KXUCLGAME": 0.035})
    assert ev.complete and ev.exclusive and ev.game_date == date(2026, 11, 3)
    assert ev.outcomes[0].yes[0].fee_rate == 0.035 and ev.outcomes[0].no[0].ask == 0.62
    raw["markets"] = raw["markets"][:2]  # no TIE market: soccer can draw, so the list is incomplete
    assert not kalshi_event(raw, {}).complete


def test_propose_pairs_rejects_lookalike_questions():
    def ev(venue, title, labels, eid):
        e = event([Outcome(lab, [leg(0.5, venue, ref=f"{eid}{i}")], [], f"{eid}{i}") for i, lab in enumerate(labels)],
                  venue=venue)
        e.title, e.id = title, eid
        return e

    k = ev("kalshi", "2028 Republican VP nominee", ["Donald J. Trump", "Donald J. Trump Jr."], "K")
    k2 = ev("kalshi", "Merriam-Webster Word of the Year 2026?", ["AI"], "W")
    k3 = ev("kalshi", "2028 U.S. Vice-Presidential Election winner", ["Jon Ossoff"], "V")
    k4 = ev("kalshi", "Rotten Tomatoes score", ["Above 30"], "R")
    poly = [
        ev("polymarket", "Republican VP Nominee 2028", ["Donald Trump", "Donald Trump Jr."], "P"),
        ev("polymarket", "Will Merriam-Webster's 2026 Word of the Year contain \"vibe\"?", ["vibe"], "Q"),
        ev("polymarket", "Presidential Election Winner 2028", ["Jon Ossoff"], "S"),
        ev("polymarket", "Rotten Tomatoes score", ["Above 35"], "T"),
    ]
    got = {(r["kalshi_ticker"], r["polymarket_id"]) for r in finder.propose_pairs([k, k2, k3, k4], poly, limit=50)}
    # Each Trump keeps only his own best match; AI is not "contains vibe"; VP is not President; 30 is not 35.
    assert got == {("K0", "P0"), ("K1", "P1")}


class _Resp:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class _Session:
    def __init__(self, books):
        self.books, self.calls = books, []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params["tickers"])
        return _Resp({"orderbooks": [{"ticker": t, "orderbook_fp": self.books[t]} for t in params["tickers"] if t in self.books]})


def test_refresh_kalshi_reprices_from_live_books_and_drops_empty_sides():
    from arbitrage.venues import refresh_kalshi

    yes, no = leg(0.60, "kalshi", ref="K1:yes"), leg(0.45, "kalshi", ref="K1:no")
    gone = leg(0.30, "kalshi", ref="K2:yes")
    ev = event([Outcome("A", [yes], [no]), Outcome("B", [gone], [])])
    # YES ask = 1 - best NO bid (0.43); NO ask = 1 - best YES bid (0.55); K2 has no NO bids at all.
    s = _Session({"K1": {"no_dollars": [["0.4000", "10"], ["0.4300", "5"]], "yes_dollars": [["0.5500", "3"]]},
                  "K2": {"no_dollars": [], "yes_dollars": [["0.2000", "9"]]}})
    refresh_kalshi(s, [ev])
    assert yes.ask == pytest.approx(0.57) and no.ask == pytest.approx(0.45)
    assert ev.outcomes[1].yes == [] and s.calls == [["K1", "K2"]]


def test_scan_lists_only_arbs_that_survive_a_one_tick_slip(monkeypatch, tmp_path):
    from arbitrage import scanner, venues

    # All-no on a 2-outcome exclusive market: payout 1 per contract, legs 0.45 + 0.53 = 0.98.
    ev = event([Outcome("A", no=[leg(0.45, ref="na")]), Outcome("B", no=[leg(0.53, ref="nb")])])
    ev.close = None  # never filtered as closing soon
    monkeypatch.setattr(venues, "fetch_kalshi", lambda s: [])
    monkeypatch.setattr(venues, "fetch_polymarket", lambda s: [ev])
    books = {"na": [(0.45, 1000)], "nb": [(0.53, 1000)]}
    monkeypatch.setattr(venues, "ladder", lambda s, l: books[l.ref])
    monkeypatch.setattr(scanner, "PAIRS_FILE", tmp_path / "none.json")
    res = scanner.scan(min_profit=5, min_annual=0, session=object(), log=False)
    (row,) = res["near"]  # $20 locked at the book, but -$0 after two 1-cent slips on 1,000 contracts
    assert row["profit"] == pytest.approx(20.0) and row["profit_if_slipped"] == pytest.approx(0.0)
    assert res["arbs"] == []
    books["nb"] = [(0.50, 1000)]  # 5 cents of edge: survives the slip
    res = scanner.scan(min_profit=5, min_annual=0, session=object(), log=False)
    assert res["arbs"][0]["profit_if_slipped"] == pytest.approx(30.0)


def test_polymarket_named_market_maps_second_outcome_to_one_minus_bid():
    raw = {
        "slug": "nba-okc-cle-2026-11-02", "title": "Thunder vs. Cavaliers", "startTime": "2026-11-03T00:00:00Z",
        "markets": [{
            "active": True, "closed": False, "outcomes": '["Thunder", "Cavaliers"]', "clobTokenIds": '["t0", "t1"]',
            "bestAsk": "0.40", "bestBid": "0.39", "sportsMarketType": "moneyline", "conditionId": "c",
            "feesEnabled": True, "feeSchedule": {"rate": 0.05, "exponent": 1}, "orderMinSize": 5,
            "endDate": "2026-11-03T00:00:00Z",
        }],
    }
    (ev,) = polymarket_events(raw)
    thunder, cavs = ev.outcomes
    assert (thunder.code, cavs.code) == ("okc", "cle") and ev.game_date == date(2026, 11, 3)
    assert thunder.yes[0].ref == "t0" and thunder.yes[0].ask == 0.40
    assert cavs.yes[0].ref == "t1" and cavs.yes[0].ask == pytest.approx(0.61) and cavs.no[0].ref == "t0"
    raw["markets"][0]["feeSchedule"]["exponent"] = 2  # unknown fee curve: skipped, not guessed
    assert polymarket_events(raw) == []
