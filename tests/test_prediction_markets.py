"""Kalshi / Polymarket odds providers, pricing, merging, and game-line enrichment (offline)."""

from __future__ import annotations

from datetime import date

import pytest

from domain.entities import Game, GameLine, OddsLine
from domain.enums import BookName, DataSource, PropType
from odds.game_lines import apply_game_lines, consensus_game_lines
from odds.prediction_market import binary_odds_pair, effective_cost, price_to_american
from providers.kalshi_provider import KalshiProvider, parse_event_ticker
from providers.polymarket_provider import PolymarketProvider, parse_event_slug
from providers.provider_registry import ProviderRegistry
from utils.names import compact_name, player_key

DAY = date(2026, 11, 1)


# ---------------------------------------------------------------------------
# Fake HTTP session
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Session:
    """Returns a payload chosen by *router(params)*; records every call."""

    def __init__(self, router):
        self.headers: dict = {}
        self.calls: list[dict] = []
        self._router = router

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        return _Resp(self._router(params or {}))


def _implied(american: int) -> float:
    return 100 / (american + 100) if american > 0 else -american / (-american + 100)


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def test_effective_cost_adds_taker_fee():
    assert effective_cost(0.5, 0.07) == pytest.approx(0.5175)
    assert effective_cost(0.5, 0.0) == 0.5


def test_price_to_american_round_trips_break_even():
    american = price_to_american(0.40, 0.07)
    assert american > 0
    assert _implied(american) == pytest.approx(effective_cost(0.40, 0.07), abs=0.003)
    assert price_to_american(0.0) is None
    assert price_to_american(1.0) is None
    assert price_to_american("bad") is None


def test_binary_odds_pair_filters_wide_and_extreme_books():
    priced = binary_odds_pair(0.52, 0.50, fee_rate=0.07)
    assert priced is not None
    yes_am, no_am, yes_cost, no_cost = priced
    assert yes_cost == pytest.approx(effective_cost(0.52, 0.07))
    assert no_cost == pytest.approx(effective_cost(0.50, 0.07))
    assert yes_am < 0 and no_am < 0

    assert binary_odds_pair(0.60, 0.60, max_spread=0.15) is None  # 20c overround
    assert binary_odds_pair(0.99, 0.02, min_price=0.02, max_price=0.98) is None
    assert binary_odds_pair(None, 0.5) is None


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def test_player_keys_strip_accents_punctuation_and_suffixes():
    assert player_key("Luka Dončić") == "luka_doncic"
    assert player_key("Jaren Jackson Jr.") == "jaren_jackson"
    assert compact_name("De'Aaron Fox") == compact_name("DeAaron Fox") == "deaaronfox"
    assert compact_name("Jaren Jackson Jr.") == compact_name("Jaren Jackson")


def test_evaluator_name_match_ignores_accents_and_suffix():
    from engine.prop_evaluator import _normalize_name

    assert _normalize_name("Nikola Jokić") == _normalize_name("Nikola Jokic")
    assert _normalize_name("Michael Porter Jr.") == _normalize_name("Michael Porter")


# ---------------------------------------------------------------------------
# Kalshi
# ---------------------------------------------------------------------------


def test_parse_event_ticker():
    assert parse_event_ticker("KXNBAPTS-26NOV01BOSORL") == (DAY, "BOS", "ORL")
    assert parse_event_ticker("KXNBAGAME-26NOV011930BOSORL") == (DAY, "BOS", "ORL")
    assert parse_event_ticker("KXNBAPTS") is None
    assert parse_event_ticker("KXNBAPTS-26XXX01BOSORL") is None


def _kalshi_market(ticker, title="", strike=None, yes="0.55", no="0.48", **extra):
    market = {
        "ticker": ticker,
        "title": title,
        "status": "active",
        "yes_ask_dollars": yes,
        "no_ask_dollars": no,
    }
    if strike is not None:
        market["floor_strike"] = strike
        market["strike_type"] = "greater"
    market.update(extra)
    return market


def _kalshi_router(params):
    series = params["series_ticker"]
    if series == "KXNBAPTS":
        return {
            "events": [
                {
                    "event_ticker": "KXNBAPTS-26NOV01BOSORL",
                    "markets": [
                        _kalshi_market("KXNBAPTS-26NOV01BOSORL-ORLFWAGNER22-15", "Franz Wagner: 15+ points", 14.5),
                        _kalshi_market("KXNBAPTS-26NOV01BOSORL-BOSJTATUM0-25", "Jayson Tatum: 25+ points", 24.5),
                        # Untradeable: 30c overround.
                        _kalshi_market("KXNBAPTS-26NOV01BOSORL-BOSJTATUM0-40", "Jayson Tatum: 40+ points", 39.5,
                                       yes="0.70", no="0.60"),
                        # Settled market.
                        _kalshi_market("KXNBAPTS-26NOV01BOSORL-BOSJBROWN7-20", "Jaylen Brown: 20+ points", 19.5,
                                       status="settled"),
                    ],
                },
                {
                    "event_ticker": "KXNBAPTS-26NOV02NYKMIA",
                    "markets": [_kalshi_market("KXNBAPTS-26NOV02NYKMIA-NYKJBRUNSON11-25", "Jalen Brunson: 25+ points", 24.5)],
                },
            ],
            "cursor": "",
        }
    if series == "KXNBAGAME":
        return {"events": [{
            "event_ticker": "KXNBAGAME-26NOV01BOSORL",
            "markets": [
                _kalshi_market("KXNBAGAME-26NOV01BOSORL-BOS", yes="0.40", no="0.62"),
                _kalshi_market("KXNBAGAME-26NOV01BOSORL-ORL", yes="0.62", no="0.40"),
            ],
        }]}
    if series == "KXNBASPREAD":
        return {"events": [{
            "event_ticker": "KXNBASPREAD-26NOV01BOSORL",
            "markets": [_kalshi_market("KXNBASPREAD-26NOV01BOSORL-ORL5", strike=4.5, yes="0.50", no="0.52")],
        }]}
    if series == "KXNBATOTAL":
        return {"events": [{
            "event_ticker": "KXNBATOTAL-26NOV01BOSORL",
            "markets": [
                _kalshi_market("KXNBATOTAL-26NOV01BOSORL-220", strike=219.5, yes="0.51", no="0.51"),
                _kalshi_market("KXNBATOTAL-26NOV01BOSORL-230", strike=229.5, yes="0.20", no="0.82"),
            ],
        }]}
    return {"events": []}


def test_kalshi_props_filter_date_assign_team_and_price_sides():
    session = _Session(_kalshi_router)
    lines = KalshiProvider(session=session).get_player_props(DAY)

    by_name = {(l.player_name, l.line): l for l in lines}
    assert set(by_name) == {("Franz Wagner", 14.5), ("Jayson Tatum", 24.5)}

    wagner = by_name[("Franz Wagner", 14.5)]
    assert wagner.book == BookName.KALSHI
    assert wagner.data_source == DataSource.KALSHI
    assert wagner.prop_type == PropType.POINTS
    assert wagner.player_id == "franz_wagner"
    assert (wagner.team_abbr, wagner.opponent_abbr) == ("ORL", "BOS")
    assert _implied(wagner.over_odds) == pytest.approx(effective_cost(0.55, 0.07), abs=0.003)
    assert _implied(wagner.under_odds) == pytest.approx(effective_cost(0.48, 0.07), abs=0.003)

    tatum = by_name[("Jayson Tatum", 24.5)]
    assert (tatum.team_abbr, tatum.opponent_abbr) == ("BOS", "ORL")

    queried = {c["series_ticker"] for c in session.calls}
    assert {"KXNBAPTS", "KXNBAREB", "KXNBAAST", "KXNBA3PT", "KXNBAPRA"} <= queried
    assert all(c["status"] == "open" for c in session.calls)


def test_kalshi_paginates_with_cursor():
    pages = {
        None: {"events": [{"event_ticker": "KXNBAREB-26NOV01BOSORL", "markets": [
            _kalshi_market("KXNBAREB-26NOV01BOSORL-BOSJTATUM0-8", "Jayson Tatum: 8+ rebounds", 7.5)]}],
            "cursor": "next"},
        "next": {"events": [{"event_ticker": "KXNBAREB-26NOV01BOSORL", "markets": [
            _kalshi_market("KXNBAREB-26NOV01BOSORL-ORLPBANCHERO5-10", "Paolo Banchero: 10+ rebounds", 9.5)]}],
            "cursor": ""},
    }

    def router(params):
        if params["series_ticker"] != "KXNBAREB":
            return {"events": []}
        return pages[params.get("cursor")]

    lines = KalshiProvider(session=_Session(router)).get_player_props(DAY)
    assert sorted(l.player_name for l in lines) == ["Jayson Tatum", "Paolo Banchero"]
    assert all(l.prop_type == PropType.REBOUNDS for l in lines)


def test_kalshi_game_lines_moneyline_spread_total():
    lines = KalshiProvider(session=_Session(_kalshi_router)).get_game_lines(DAY)
    ml = {l.selection: l for l in lines if l.market == "moneyline"}
    assert set(ml) == {"BOS", "ORL"}
    assert ml["ORL"].american_odds < 0 < ml["BOS"].american_odds

    spread = {l.selection: l.line for l in lines if l.market == "spread"}
    assert spread == {"ORL": -4.5, "BOS": 4.5}

    totals = {(l.selection, l.line) for l in lines if l.market == "total"}
    assert totals == {("over", 219.5), ("under", 219.5), ("over", 229.5), ("under", 229.5)}
    assert all((l.away_abbr, l.home_abbr) == ("BOS", "ORL") for l in lines)


def test_kalshi_http_error_is_recorded_not_raised():
    import requests

    class _Boom(_Session):
        def get(self, url, params=None, timeout=None):
            raise requests.ConnectionError("network down")

    provider = KalshiProvider(session=_Boom(lambda p: {}))
    assert provider.get_player_props(DAY) == []
    assert provider.last_errors and "network down" in provider.last_errors[0]


# ---------------------------------------------------------------------------
# Polymarket
# ---------------------------------------------------------------------------


def test_parse_event_slug():
    assert parse_event_slug("nba-tor-lal-2026-11-01") == (DAY, "TOR", "LAL")
    assert parse_event_slug("nba-tor-lal-2026-11-01-exact-margin") is None
    assert parse_event_slug("nfl-dal-nyg-2026-11-01") is None


def _poly_market(kind, outcomes, bid, ask, **extra):
    market = {
        "sportsMarketType": kind,
        "outcomes": outcomes,
        "bestBid": bid,
        "bestAsk": ask,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
    }
    market.update(extra)
    return market


_POLY_EVENTS = [
    {
        "id": "9001",
        "slug": "nba-tor-lal-2026-11-01",
        "title": "Raptors vs. Lakers",
        "markets": [
            _poly_market("points", '["Over", "Under"]', 0.48, 0.52,
                         question="LeBron James: Points O/U 24.5", line=24.5,
                         feesEnabled=True, feeSchedule={"rate": 0.05, "takerOnly": True}),
            _poly_market("rebounds", '["Over", "Under"]', 0.45, 0.47,
                         question="Luka Dončić: Rebounds O/U 8.5"),
            # Too wide (bid 0.10 / ask 0.60 -> 50c overround).
            _poly_market("assists", '["Over", "Under"]', 0.10, 0.60,
                         question="Austin Reaves: Assists O/U 5.5", line=5.5),
            # Closed.
            _poly_market("points", '["Over", "Under"]', 0.48, 0.52,
                         question="Scottie Barnes: Points O/U 19.5", line=19.5, closed=True),
            _poly_market("moneyline", '["Raptors", "Lakers"]', 0.30, 0.32, question="Raptors vs. Lakers"),
            _poly_market("spreads", '["Lakers", "Raptors"]', 0.49, 0.51,
                         question="Spread: Lakers (-5.5)", line=-5.5),
            _poly_market("totals", '["Over", "Under"]', 0.49, 0.51, question="O/U 225.5", line=225.5),
        ],
    },
    {"id": "9002", "slug": "nba-tor-lal-2026-11-01-exact-margin", "title": "x",
     "markets": [_poly_market("points", '["Over", "Under"]', 0.48, 0.52, question="A B: Points O/U 1.5", line=1.5)]},
    {"id": "9003", "slug": "nba-nyk-mia-2026-11-02", "title": "Knicks vs. Heat",
     "markets": [_poly_market("points", '["Over", "Under"]', 0.48, 0.52, question="Jalen Brunson: Points O/U 25.5", line=25.5)]},
]


def test_polymarket_props_parse_filter_and_fee():
    session = _Session(lambda p: _POLY_EVENTS)
    provider = PolymarketProvider(session=session)
    lines = provider.get_player_props(DAY)

    by_name = {l.player_name: l for l in lines}
    assert set(by_name) == {"LeBron James", "Luka Dončić"}

    lebron = by_name["LeBron James"]
    assert lebron.book == BookName.POLYMARKET
    assert lebron.prop_type == PropType.POINTS
    assert lebron.line == 24.5
    assert lebron.player_id == "lebron_james"
    assert _implied(lebron.over_odds) == pytest.approx(effective_cost(0.52, 0.05), abs=0.003)
    assert _implied(lebron.under_odds) == pytest.approx(effective_cost(0.52, 0.05), abs=0.003)

    luka = by_name["Luka Dončić"]
    assert luka.line == 8.5  # parsed from the question when 'line' is missing
    assert luka.player_id == "luka_doncic"
    assert _implied(luka.over_odds) == pytest.approx(0.47, abs=0.003)  # fees disabled

    params = session.calls[0]
    assert params["closed"] == "false"
    assert params["end_date_min"] == "2026-11-01T00:00:00Z"
    assert params["end_date_max"] == "2026-11-02T12:00:00Z"


def test_polymarket_game_lines_and_shared_event_cache():
    session = _Session(lambda p: _POLY_EVENTS)
    provider = PolymarketProvider(session=session)
    provider.get_player_props(DAY)
    lines = provider.get_game_lines(DAY)
    assert len(session.calls) == 1  # props + game lines share one fetch

    ml = {l.selection: l for l in lines if l.market == "moneyline"}
    assert set(ml) == {"TOR", "LAL"}
    assert ml["LAL"].american_odds < 0 < ml["TOR"].american_odds

    spread = {l.selection: l.line for l in lines if l.market == "spread"}
    assert spread == {"LAL": -5.5, "TOR": 5.5}

    totals = {(l.selection, l.line) for l in lines if l.market == "total"}
    assert totals == {("over", 225.5), ("under", 225.5)}
    assert all((l.away_abbr, l.home_abbr) == ("TOR", "LAL") for l in lines)


# ---------------------------------------------------------------------------
# Registry merge
# ---------------------------------------------------------------------------


def _odds_line(book, source, name):
    return OddsLine(
        book=book, player_id=player_key(name), player_name=name, prop_type=PropType.POINTS,
        line=20.5, over_odds=-110, under_odds=-110, data_source=source,
    )


class _FakeKalshi:
    source_name = DataSource.KALSHI
    last_errors: list = []

    def get_player_props(self, game_date):
        return [_odds_line(BookName.KALSHI, DataSource.KALSHI, "A B"),
                _odds_line(BookName.KALSHI, DataSource.KALSHI, "C D")]

    def get_game_lines(self, game_date):
        return [GameLine(book=BookName.KALSHI, game_date=game_date, away_abbr="BOS", home_abbr="ORL",
                         market="total", selection="over", line=220.5, price=0.5, american_odds=-100)]


class _FakePoly:
    source_name = DataSource.POLYMARKET
    last_errors = ["Polymarket events: timed out"]

    def get_player_props(self, game_date):
        return []

    def get_game_lines(self, game_date):
        raise RuntimeError("boom")


class _FakeOddsApi:
    source_name = DataSource.ODDS_API

    def get_player_props(self, game_date):
        raise ConnectionError("TLS handshake failed")


def test_registry_merges_all_odds_sources_and_isolates_failures():
    registry = ProviderRegistry({"odds": [_FakeKalshi(), _FakePoly(), _FakeOddsApi()]})
    props = registry.get_player_props(DAY)
    assert len(props) == 2
    assert registry.last_odds_counts == {"kalshi": 2, "polymarket": 0, "odds_api": 0}
    assert "timed out" in registry.last_odds_errors["polymarket"]
    assert "TLS" in registry.last_odds_errors["odds_api"]

    game_lines = registry.get_game_lines(DAY)  # Odds API lacks get_game_lines; Polymarket raises
    assert [gl.book for gl in game_lines] == [BookName.KALSHI]


def test_registry_registers_keyless_providers_by_default(monkeypatch):
    import config
    from providers import provider_registry as reg

    monkeypatch.delenv("KALSHI_ENABLED", raising=False)
    monkeypatch.delenv("POLYMARKET_ENABLED", raising=False)
    monkeypatch.setattr(config, "_pm_config", None, raising=False)
    names = {p.__class__.__name__ for p in reg._build_providers()["odds"]}
    assert {"KalshiProvider", "PolymarketProvider"} <= names

    monkeypatch.setenv("KALSHI_ENABLED", "false")
    monkeypatch.setattr(config, "_pm_config", None, raising=False)
    names = {p.__class__.__name__ for p in reg._build_providers()["odds"]}
    assert "KalshiProvider" not in names
    monkeypatch.setattr(config, "_pm_config", None, raising=False)


# ---------------------------------------------------------------------------
# Game-line consensus + enrichment
# ---------------------------------------------------------------------------


def _gl(book, market, selection, line, price, away="BOS", home="ORL"):
    return GameLine(book=book, game_date=DAY, away_abbr=away, home_abbr=home, market=market,
                    selection=selection, line=line, price=price, american_odds=-110)


_LADDER = [
    # Kalshi total ladder: 219.5 is the 50/50 strike.
    _gl(BookName.KALSHI, "total", "over", 209.5, 0.80), _gl(BookName.KALSHI, "total", "under", 209.5, 0.22),
    _gl(BookName.KALSHI, "total", "over", 219.5, 0.51), _gl(BookName.KALSHI, "total", "under", 219.5, 0.51),
    _gl(BookName.KALSHI, "total", "over", 229.5, 0.20), _gl(BookName.KALSHI, "total", "under", 229.5, 0.82),
    _gl(BookName.POLYMARKET, "total", "over", 221.5, 0.50), _gl(BookName.POLYMARKET, "total", "under", 221.5, 0.52),
    # Spreads: home ORL -4.5 on Kalshi, -5.5 on Polymarket.
    _gl(BookName.KALSHI, "spread", "ORL", -4.5, 0.51), _gl(BookName.KALSHI, "spread", "BOS", 4.5, 0.51),
    _gl(BookName.KALSHI, "spread", "ORL", -10.5, 0.25), _gl(BookName.KALSHI, "spread", "BOS", 10.5, 0.77),
    _gl(BookName.POLYMARKET, "spread", "ORL", -5.5, 0.50), _gl(BookName.POLYMARKET, "spread", "BOS", 5.5, 0.52),
    _gl(BookName.POLYMARKET, "moneyline", "ORL", None, 0.62),
]


def test_consensus_uses_main_line_per_book_then_median():
    consensus = consensus_game_lines(_LADDER)
    assert consensus == {("BOS", "ORL"): {"total": 220.5, "home_spread": -5.0}}


def _game(away="BOS", home="ORL", **kw):
    return Game(game_id="g1", home_team_id="h", home_team_abbr=home, away_team_id="a",
                away_team_abbr=away, game_date=DAY, **kw)


def test_apply_game_lines_fills_total_spread_and_implied_totals():
    game = _game()
    assert apply_game_lines([game], _LADDER) == 1
    assert game.game_total == 220.5
    assert game.home_spread == -5.0
    assert game.home_implied_total == pytest.approx(112.8, abs=0.06)
    assert game.away_implied_total == pytest.approx(107.8, abs=0.06)


def test_apply_game_lines_handles_reversed_order_and_keeps_existing_values():
    flipped = _game(away="ORL", home="BOS")
    apply_game_lines([flipped], _LADDER)
    assert flipped.home_spread == 5.0  # BOS is the road underdog in the market
    assert flipped.game_total == 220.5

    keep = _game(game_total=230.0, home_spread=-2.0)
    apply_game_lines([keep], _LADDER)
    assert (keep.game_total, keep.home_spread) == (230.0, -2.0)

    other = _game(away="NYK", home="MIA")
    assert apply_game_lines([other], _LADDER) == 0
    assert other.game_total == 0.0
