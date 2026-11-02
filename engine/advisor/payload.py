"""
JSON payload sent to the LLM.

Only whitelisted, already-computed fields are included. No API keys, raw
provider responses, URLs or internal market tickers are ever serialised.
"""

from __future__ import annotations

import json
from datetime import date

from engine.advisor.candidates import CandidateStats
from engine.advisor.parlays import ParlayRow, PickRow
from utils.formatting import book_display_name

PICK_FIELDS = (
    "id", "player", "team", "opponent", "game_id", "market", "line", "side",
    "odds_american", "odds_decimal", "venue", "projected_value", "model_true_probability",
    "implied_probability", "edge", "fair_odds", "confidence", "expected_minutes",
    "calibration_warnings", "model_notes", "straight_bet",
)

PARLAY_FIELDS = (
    "id", "legs", "num_legs", "combined_american_odds", "combined_decimal_odds",
    "implied_probability", "model_true_probability", "edge", "stake", "total_return",
    "profit", "expected_value", "correlation_risk_score", "same_game", "venues",
    "single_venue",
)

_NOTE_LIMIT = 280


def _r(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def _pick_dict(row: PickRow) -> dict:
    p = row.prop
    notes = (p.explanation or "").strip().replace("\n", " ")
    if len(notes) > _NOTE_LIMIT:
        notes = notes[: _NOTE_LIMIT - 1].rstrip() + "…"
    data = {
        "id": row.id,
        "player": p.player_name,
        "team": p.team_abbr or "",
        "opponent": p.opponent_abbr or "",
        "game_id": str(p.game_id),
        "market": p.prop_type.value,
        "line": p.line,
        "side": p.side.value,
        "odds_american": int(p.sportsbook_odds),
        "odds_decimal": _r(row.decimal_odds),
        "venue": book_display_name(p.best_book, getattr(p, "best_book_key", "") or ""),
        "projected_value": _r(p.projected_value, 2),
        "model_true_probability": _r(p.true_probability),
        "implied_probability": _r(p.implied_probability),
        "edge": _r(p.edge),
        "fair_odds": int(p.fair_odds),
        "confidence": p.confidence.value,
        "expected_minutes": _r(p.expected_minutes, 1) if getattr(p, "expected_minutes", None) else None,
        "calibration_warnings": list(getattr(p, "calibration_warnings", None) or []),
        "model_notes": notes,
        "straight_bet": {
            "stake": _r(row.stake, 2),
            "total_return": _r(row.total_return, 2),
            "profit": _r(row.profit, 2),
            "expected_value": _r(row.expected_value, 2),
        },
    }
    return {k: data[k] for k in PICK_FIELDS}


def _parlay_dict(row: ParlayRow, venue_labels: dict[str, str]) -> dict:
    data = {
        "id": row.id,
        "legs": list(row.leg_ids),
        "num_legs": row.num_legs,
        "combined_american_odds": int(row.combined_american_odds),
        "combined_decimal_odds": _r(row.combined_decimal_odds),
        "implied_probability": _r(row.implied_probability),
        "model_true_probability": _r(row.true_probability),
        "edge": _r(row.edge),
        "stake": _r(row.stake, 2),
        "total_return": _r(row.total_return, 2),
        "profit": _r(row.profit, 2),
        "expected_value": _r(row.expected_value, 2),
        "correlation_risk_score": _r(row.correlation_risk_score, 3),
        "same_game": row.same_game,
        "venues": [venue_labels.get(pid, "") for pid in row.leg_ids],
        "single_venue": row.single_venue,
    }
    return {k: data[k] for k in PARLAY_FIELDS}


def build_payload(
    *,
    game_date: date,
    picks: list[PickRow],
    parlays: list[ParlayRow],
    stats: CandidateStats,
    min_odds: int,
    max_odds: int,
    min_edge: float,
    stake: float,
    odds_source_counts: dict[str, int] | None = None,
) -> dict:
    """Assemble the deterministic, whitelisted payload for the LLM."""
    pick_dicts = [_pick_dict(r) for r in picks]
    venue_labels = {d["id"]: d["venue"] for d in pick_dicts}
    return {
        "date": game_date.isoformat(),
        "settings": {
            "odds_band_american": [min(min_odds, max_odds), max(min_odds, max_odds)],
            "min_edge_exclusive": min_edge,
            "stake": _r(stake, 2),
        },
        "odds_sources": {k: int(v) for k, v in sorted((odds_source_counts or {}).items())},
        "filter_counts": stats.as_dict(),
        "notes": [
            "true probabilities come from the statistical model; implied probabilities come from the venue price",
            "parlay model_true_probability is calibrated and penalised for correlation; it is not a naive product",
            "Kalshi and Polymarket are separate venues: a parlay mixing venues cannot be placed as one ticket",
        ],
        "picks": pick_dicts,
        "parlays": [_parlay_dict(r, venue_labels) for r in parlays],
    }


def payload_to_json(payload: dict) -> str:
    """Stable JSON text for the prompt."""
    return json.dumps(payload, indent=2, sort_keys=False, default=str)
