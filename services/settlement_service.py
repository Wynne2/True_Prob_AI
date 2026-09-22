"""Provider-backed, idempotent final-stat settlement for the local ledger."""

from __future__ import annotations

from config import get_settings
from data.loaders import load_final_player_totals
from services.evaluation_store import EvaluationStore


def settle_game(game_id: str) -> int:
    """Append final-stat settlement revisions using BALLDONTLIE in live mode."""
    if get_settings().app_mode != "live":
        return 0
    totals = load_final_player_totals(game_id)
    if not totals:
        return 0
    return EvaluationStore().settle_game(
        game_id,
        totals,
        source="balldontlie_final_box_score",
    )
