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
