from dataclasses import replace

import pytest

from domain.entities import OddsLine, PropProbability
from domain.enums import BookName, ConfidenceTier, DistributionType, PropSide, PropType
from services.evaluation_store import EvaluationStore


def _forecast(
    *,
    player_id: str,
    side: PropSide,
    probability: float,
    line: float = 24.5,
) -> PropProbability:
    quote = OddsLine(
        book=BookName.FANDUEL,
        book_key="fanduel",
        player_id=player_id,
        player_name=player_id,
        prop_type=PropType.POINTS,
        line=line,
        over_odds=-110,
        under_odds=-110,
        game_id="game-1",
    )
    return PropProbability(
        player_id=player_id,
        player_name=player_id,
        team_abbr="BOS",
        opponent_abbr="MIA",
        game_id="game-1",
        prop_type=PropType.POINTS,
        line=line,
        side=side,
        projected_value=25.0,
        true_probability=probability,
        implied_probability=0.5238,
        edge=probability - 0.5238,
        fair_odds=-120,
        sportsbook_odds=-110,
        best_book=BookName.FANDUEL,
        best_book_key="fanduel",
        selected_quote=quote,
        confidence=ConfidenceTier.MEDIUM,
        distribution_type=DistributionType.NORMAL,
    )


def test_ledger_persists_inputs_settles_and_scores(tmp_path):
    store = EvaluationStore(tmp_path / "ledger.sqlite3")
    forecasts = [
        _forecast(player_id="player-over", side=PropSide.OVER, probability=0.70),
        _forecast(player_id="player-under", side=PropSide.UNDER, probability=0.60),
        _forecast(player_id="player-push", side=PropSide.OVER, probability=0.55, line=24.0),
    ]
    snapshot_id = store.record_forecasts(forecasts, {})
    assert snapshot_id

    settled = store.settle_game(
        "game-1",
        {
            "player-over": {"points": 30.0},
            "player-under": {"points": 27.0},
            "player-push": {"points": 24.0},
        },
        source="fixture",
    )
    assert settled == 3

    report = store.report()
    assert report.settled_count == 2
    assert report.brier_score == pytest.approx(0.225)
    assert report.log_loss is not None
    assert report.quoted_return_per_unit == pytest.approx((100 / 110 - 1) / 2)
    assert sum(row["count"] for row in report.reliability) == 2
