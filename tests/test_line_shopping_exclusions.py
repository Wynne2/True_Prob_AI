"""Excluded books (Bovada by default) never become the bet, but still count as coverage."""

from domain.entities import OddsLine
from domain.enums import BookName, PropType
from odds.line_shopping import is_bettable, shop_lines


def _line(book, over, under, key=""):
    return OddsLine(
        book=book, player_id="p1", player_name="Player", prop_type=PropType.POINTS,
        line=20.5, over_odds=over, under_odds=under, book_key=key,
    )


def test_bovada_never_best_book():
    lines = [
        _line(BookName.BOVADA, -300, +240),  # best price on both sides
        _line(BookName.FANDUEL, -350, +260),
        _line(BookName.DRAFTKINGS, -340, +250),
    ]
    best = {b.side: b for b in shop_lines(lines)}
    assert best["over"].best_book == BookName.DRAFTKINGS
    assert best["under"].best_book == BookName.FANDUEL
    assert all(BookName.BOVADA not in b.all_books for b in best.values())


def test_bovada_only_line_is_not_offered():
    assert shop_lines([_line(BookName.BOVADA, -300, +240)]) == []


def test_bovada_counts_toward_main_line_coverage():
    # The 20.5 line is posted by Bovada + FanDuel; a FanDuel-only 21.5 alt is dropped.
    lines = [_line(BookName.BOVADA, -110, -110), _line(BookName.FANDUEL, -110, -110)]
    alt = _line(BookName.FANDUEL, +120, -150)
    alt.line = 21.5
    out = shop_lines(lines + [alt])
    assert {b.line for b in out} == {20.5}


def test_book_key_match():
    assert not is_bettable(_line(BookName.OTHER, -110, -110, key="bovada"))
    assert is_bettable(_line(BookName.OTHER, -110, -110, key="fliff"))
