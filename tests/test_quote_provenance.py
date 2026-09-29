from datetime import datetime, timedelta

import pytest

from domain.entities import OddsLine, PropProbability
from domain.enums import BookName, ConfidenceTier, DistributionType, PropSide, PropType
from engine.parlay_builder import ParlayConstraints, build_parlays
from odds.line_shopping import shop_lines
from odds.normalizer import american_to_raw_implied_prob


def make_quote(
    *,
    book: BookName,
    book_key: str,
    over_odds: int,
    under_odds: int = -110,
    game_id: str = "event-1",
    timestamp: datetime | None = None,
    event_home_team_abbr: str = "",
    event_away_team_abbr: str = "",
) -> OddsLine:
    return OddsLine(
        book=book,
        book_key=book_key,
        player_id="player-1",
        player_name="Player One",
        prop_type=PropType.POINTS,
        line=24.5,
        over_odds=over_odds,
        under_odds=under_odds,
        game_id=game_id,
        timestamp=timestamp,
        event_home_team_abbr=event_home_team_abbr,
        event_away_team_abbr=event_away_team_abbr,
    )


def make_prop(
    player_id: str,
    game_id: str,
    true_probability: float,
    side: PropSide = PropSide.OVER,
    quotes: list[OddsLine] | None = None,
) -> PropProbability:
    quotes = quotes or []
    selected = quotes[0] if quotes else None
    odds = selected.over_odds if selected and side == PropSide.OVER else -110
    implied = american_to_raw_implied_prob(odds)
    return PropProbability(
        player_id=player_id,
        player_name=player_id,
        team_abbr="BOS",
        opponent_abbr="MIA",
        game_id=game_id,
        prop_type=PropType.POINTS,
        line=24.5,
        side=side,
        projected_value=25.0,
        true_probability=true_probability,
        implied_probability=implied,
        edge=true_probability - implied,
        fair_odds=-120,
        sportsbook_odds=odds,
        best_book=selected.book if selected else BookName.FANDUEL,
        best_book_key=selected.book_key if selected else "fanduel",
        selected_quote=selected,
        confidence=ConfidenceTier.HIGH,
        distribution_type=DistributionType.NORMAL,
        all_lines=quotes,
    )


class TestQuoteProvenance:
    def test_unknown_book_keys_remain_distinct_and_best_quote_is_self_consistent(self):
        fanatics = make_quote(book=BookName.OTHER, book_key="fanatics", over_odds=-110)
        espn_bet = make_quote(book=BookName.OTHER, book_key="espnbet", over_odds=200)

        result = shop_lines([fanatics, espn_bet], min_books=2)
        best_over = next(line for line in result if line.side == "over")

        assert best_over.best_book_key == "espnbet"
        assert best_over.selected_quote is espn_bet
        assert best_over.best_odds == 200
        assert best_over.all_books == {"fanatics": -110, "espnbet": 200}

    def test_shopping_keeps_freshest_quote_from_each_book(self):
        now = datetime.now()
        stale = make_quote(
            book=BookName.FANDUEL,
            book_key="fanduel",
            over_odds=150,
            timestamp=now - timedelta(minutes=5),
        )
        current = make_quote(
            book=BookName.FANDUEL,
            book_key="fanduel",
            over_odds=-105,
            timestamp=now,
        )
        draftkings = make_quote(book=BookName.DRAFTKINGS, book_key="draftkings", over_odds=-110)

        result = shop_lines([stale, current, draftkings], min_books=2)
        best_over = next(line for line in result if line.side == "over")

        assert best_over.best_book_key == "fanduel"
        assert best_over.best_odds == -105
        assert best_over.selected_quote is current

    def test_event_identity_prevents_quotes_from_different_games_merging(self):
        event_one = make_quote(book=BookName.FANDUEL, book_key="fanduel", over_odds=-110)
        event_two = make_quote(
            book=BookName.DRAFTKINGS,
            book_key="draftkings",
            over_odds=200,
            game_id="event-2",
        )

        result = shop_lines([event_one, event_two], min_books=1)

        assert len([line for line in result if line.side == "over"]) == 2

    def test_stale_quotes_are_excluded_when_a_freshness_limit_is_set(self):
        now = datetime.now()
        stale = make_quote(
            book=BookName.FANDUEL,
            book_key="fanduel",
            over_odds=200,
            timestamp=now - timedelta(minutes=30),
        )
        fresh = make_quote(
            book=BookName.DRAFTKINGS,
            book_key="draftkings",
            over_odds=-105,
            timestamp=now,
        )

        result = shop_lines([stale, fresh], min_books=1, max_quote_age_seconds=60)
        best_over = next(line for line in result if line.side == "over")
        assert best_over.best_book_key == "draftkings"


class TestBookSpecificParlays:
    def test_parlay_uses_only_a_common_raw_bookmaker_key(self):
        first_quotes = [
            make_quote(book=BookName.FANDUEL, book_key="fanduel", over_odds=-110),
            make_quote(book=BookName.DRAFTKINGS, book_key="draftkings", over_odds=130),
        ]
        second_quotes = [
            OddsLine(
                book=BookName.FANDUEL,
                book_key="fanduel",
                player_id="player-2",
                player_name="Player Two",
                prop_type=PropType.POINTS,
                line=24.5,
                over_odds=-110,
                under_odds=-110,
                game_id="event-2",
            ),
        ]
        first = make_prop("player-1", "event-1", 0.78, quotes=first_quotes)
        second = make_prop("player-2", "event-2", 0.78, quotes=second_quotes)

        parlays = build_parlays(
            [first, second],
            ParlayConstraints(min_edge=0.01, max_legs=2, min_legs=2),
        )

        assert len(parlays) == 1
        parlay = parlays[0]
        assert parlay.ticket_book_key == "fanduel"
        assert {leg.best_book_key for leg in parlay.legs} == {"fanduel"}
        assert parlay.is_theoretical_price is True

    def test_negative_final_ev_ticket_is_rejected(self):
        first_quote = make_quote(book=BookName.FANDUEL, book_key="fanduel", over_odds=-110)
        second_quote = OddsLine(
            book=BookName.FANDUEL,
            book_key="fanduel",
            player_id="player-2",
            player_name="Player Two",
            prop_type=PropType.POINTS,
            line=24.5,
            over_odds=-110,
            under_odds=-110,
            game_id="event-2",
        )
        first = make_prop("player-1", "event-1", 0.58, PropSide.UNDER, [first_quote])
        second = make_prop("player-2", "event-2", 0.58, PropSide.UNDER, [second_quote])

        parlays = build_parlays(
            [first, second],
            ParlayConstraints(min_edge=0.05, max_legs=2, min_legs=2),
        )

        assert parlays == []
