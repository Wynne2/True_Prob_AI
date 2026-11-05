"""Render the Streamlit dashboard offline with a fake slate and check every tab draws."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from domain.entities import Game, GameLine, OddsLine, PropProbability
from domain.enums import BookName, ConfidenceTier, PropSide, PropType

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")
SLATE_DAY = date(2026, 11, 2)
HOSTILE_NAME = "<script>alert(1)</script> Jr."


def _implied(odds: int) -> float:
    return abs(odds) / (abs(odds) + 100) if odds < 0 else 100 / (odds + 100)


def _prop(i, name, team, opp, game_id, prop_type, line, odds, true_p, conf, book=BookName.DRAFTKINGS):
    implied = _implied(odds)
    return PropProbability(
        player_id=f"p{i}",
        player_name=name,
        team_abbr=team,
        opponent_abbr=opp,
        game_id=game_id,
        prop_type=prop_type,
        line=line,
        side=PropSide.OVER,
        projected_value=line + 1.6,
        true_probability=true_p,
        implied_probability=implied,
        edge=true_p - implied,
        fair_odds=-int(round(100 * true_p / (1 - true_p))),
        sportsbook_odds=odds,
        best_book=book,
        confidence=conf,
        explanation=f"Minutes projected 34.0. Shot volume supports the {line:g} line.",
        all_lines=[
            OddsLine(BookName.DRAFTKINGS, f"p{i}", name, prop_type, line, odds, -odds // 2 if odds < -200 else 100),
            OddsLine(BookName.KALSHI, f"p{i}", name, prop_type, line, odds - 10, 120),
        ],
        expected_minutes=34.0,
        baseline_projection=line + 1.0,
    )


def fake_slate() -> tuple[list[Game], list[PropProbability], list[GameLine]]:
    tip = datetime(2026, 11, 3, 0, 30, tzinfo=timezone.utc)
    games = [
        Game("g1", "BOS", "BOS", "NYK", "NYK", SLATE_DAY, tip, arena="TD Garden", city="Boston"),
        Game("g2", "DEN", "DEN", "LAL", "LAL", SLATE_DAY, tip, arena="Ball Arena", city="Denver"),
        Game("g3", "OKC", "OKC", "MIN", "MIN", SLATE_DAY, None),
    ]
    rows = [
        ("Jalen Brunson", "NYK", "BOS", "g1", PropType.POINTS, 24.5, -230, 0.735, ConfidenceTier.HIGH),
        ("Jayson Tatum", "BOS", "NYK", "g1", PropType.PRA, 39.5, -250, 0.742, ConfidenceTier.MEDIUM),
        ("Nikola Jokic", "DEN", "LAL", "g2", PropType.REBOUNDS, 11.5, -320, 0.79, ConfidenceTier.HIGH),
        ("LeBron James", "LAL", "DEN", "g2", PropType.ASSISTS, 6.5, -210, 0.70, ConfidenceTier.MEDIUM),
        ("Shai Gilgeous-Alexander", "OKC", "MIN", "g3", PropType.POINTS, 29.5, -280, 0.765, ConfidenceTier.HIGH),
        ("Anthony Edwards", "MIN", "OKC", "g3", PropType.THREES, 2.5, -205, 0.69, ConfidenceTier.LOW),
        ("Derrick White", "BOS", "NYK", "g1", PropType.STEALS, 0.5, -400, 0.83, ConfidenceTier.MEDIUM),
        (HOSTILE_NAME, "DEN", "LAL", "g2", PropType.BLOCKS, 0.5, 120, 0.47, ConfidenceTier.VERY_LOW),
        ("Mikal Bridges", "NYK", "BOS", "g1", PropType.POINTS, 17.5, -115, 0.52, ConfidenceTier.LOW),
    ]
    props = [_prop(i, *row) for i, row in enumerate(rows)]
    lines = [
        GameLine(BookName.KALSHI, SLATE_DAY, "NYK", "BOS", "total", "over", 221.5, 0.51, -104),
        GameLine(BookName.POLYMARKET, SLATE_DAY, "NYK", "BOS", "spread", "BOS", -5.5, 0.50, 100),
    ]
    return games, props, lines


@pytest.fixture
def patched_scan(monkeypatch):
    games, props, lines = fake_slate()

    def fake_scan(self, game_date, prop_types=None, is_playoff=False):
        self.last_games = list(games)
        self.last_game_lines = lines
        self.last_odds_source_counts = {"kalshi": 120, "polymarket": 64}
        self.last_odds_source_errors = {}
        return list(props)

    monkeypatch.setattr("engine.slate_scanner.SlateScanner.scan", fake_scan)
    monkeypatch.setattr("data.loaders.load_games", lambda d: [g for g in games])
    monkeypatch.setattr("data.loaders.reset_registry", lambda: None)
    return props


def _html(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_first_load_shows_empty_state():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert "Scan tonight" in _html(at)
    assert len(at.tabs) == 10  # includes Matchup Finder
    assert "1-800-GAMBLER" in _html(at)


def test_scan_renders_every_tab(patched_scan):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.button[0].click().run()
    assert not at.exception, at.exception

    page = _html(at)
    assert "tp-pick" in page and "tp-ticket" in page and "tp-game" in page
    assert "Jalen Brunson" in page
    # Feed strings are escaped before they reach HTML.
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt; Jr." in page
    assert len(at.dataframe) >= 3  # props table, line shopping, game lines
    assert "Scan tonight" not in page


def test_parlay_and_odds_filters_drive_the_board(patched_scan):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.number_input(key="max_leg_odds_band_v3").set_value(-200)
    at.sidebar.button[0].click().run()
    assert not at.exception
    page = _html(at)
    # Plus-money and -115 legs are outside the -600..-200 band.
    assert "Mikal Bridges" not in page.split("Props analysis")[0]
    assert "Nikola Jokic" in page


def test_tip_time_is_portable():
    from app.ui import tip_time

    games, _, _ = fake_slate()
    assert tip_time(games[0]) == "7:30 PM ET"
    assert tip_time(games[2]) == "Tip time TBD"


def test_prob_meter_marks_negative_edge():
    from app.ui import prob_meter

    assert "neg" not in prob_meter(0.7, 0.6)
    assert "tp-meter-gap neg" in prob_meter(0.4, 0.6)
