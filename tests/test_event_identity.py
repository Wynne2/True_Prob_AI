from domain.entities import Game, OddsLine
from domain.enums import BookName, PropType
from engine.slate_scanner import SlateScanner


def test_event_identity_maps_only_an_unambiguous_team_pair():
    games = [
        Game("schedule-1", "bos", "BOS", "mia", "MIA"),
        Game("schedule-2", "lal", "LAL", "den", "DEN"),
    ]
    quote = OddsLine(
        book=BookName.FANDUEL,
        book_key="fanduel",
        player_id="p1",
        player_name="Test",
        prop_type=PropType.POINTS,
        line=20.5,
        over_odds=-110,
        under_odds=-110,
        game_id="odds-event-1",
        event_home_team_abbr="BOS",
        event_away_team_abbr="MIA",
    )

    canonical = SlateScanner._canonicalize_odds_event_ids(games, [quote])

    assert canonical[0].game_id == "schedule-1"


def test_event_identity_rejects_unresolved_quote():
    games = [Game("schedule-1", "bos", "BOS", "mia", "MIA")]
    quote = OddsLine(
        book=BookName.FANDUEL,
        book_key="fanduel",
        player_id="p1",
        player_name="Test",
        prop_type=PropType.POINTS,
        line=20.5,
        over_odds=-110,
        under_odds=-110,
        game_id="odds-event-1",
        event_home_team_abbr="LAL",
        event_away_team_abbr="DEN",
    )

    assert SlateScanner._canonicalize_odds_event_ids(games, [quote]) == []
