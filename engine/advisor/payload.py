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
from engine.model_agreement import model_agrees, own_model_probability
from utils.formatting import book_display_name

PICK_FIELDS = (
    "id", "in_shortlist", "player", "team", "opponent", "game_id", "market", "line", "side",
    "odds_american", "odds_decimal", "venue", "venues_quoting", "projected_value", "model_true_probability",
    "implied_probability", "market_probability", "venue_gap", "model_own_probability",
    "model_agrees_with_edge", "model_weight", "probability_method",
    "candidate_probability", "candidate_projection", "production_probability",
    "edge", "fair_odds", "confidence", "expected_minutes",
    "calibration_warnings", "model_notes", "straight_bet", "kelly",
)

PARLAY_FIELDS = (
    "id", "legs", "num_legs", "combined_american_odds", "combined_decimal_odds",
    "implied_probability", "model_true_probability", "edge", "stake", "total_return",
    "profit", "expected_value", "correlation_risk_score", "same_game", "venues",
    "single_venue", "kelly",
)


def _kelly_dict(row, bankroll: float | None) -> dict:
    return {
        "bankroll_fraction": _r(row.kelly_fraction, 4),
        "suggested_stake": row.kelly_stake(bankroll),
    }

_NOTE_LIMIT = 700


def _r(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def _opt(value, digits: int = 4) -> float | None:
    return None if value is None else _r(value, digits)


def _venues_quoting(p) -> int:
    """Distinct venues with a price at this exact line."""
    books = set()
    for ol in getattr(p, "all_lines", None) or []:
        try:
            if abs(float(ol.line) - float(p.line)) < 1e-9:
                books.add(getattr(ol, "book_key", "") or str(getattr(ol, "book", "")))
        except (TypeError, ValueError):
            continue
    return len(books)


def _pick_dict(row: PickRow, bankroll: float | None = None, in_shortlist: bool = True) -> dict:
    p = row.prop
    notes = (p.explanation or "").strip().replace("\n", " ")
    if len(notes) > _NOTE_LIMIT:
        notes = notes[: _NOTE_LIMIT - 1].rstrip() + "…"
    market = getattr(p, "market_probability", None)
    data = {
        "id": row.id,
        "in_shortlist": in_shortlist,
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
        "venues_quoting": _venues_quoting(p),
        "projected_value": _r(p.projected_value, 2),
        "model_true_probability": _r(p.true_probability),
        "implied_probability": _r(p.implied_probability),
        "market_probability": _opt(market),
        "venue_gap": None if market is None else _r(float(market) - float(p.implied_probability)),
        "model_own_probability": _opt(own_model_probability(p)),
        "model_agrees_with_edge": model_agrees(p),
        "model_weight": _r(getattr(p, "model_weight", 0.0) or 0.0, 3),
        "probability_method": getattr(p, "probability_method", "legacy") or "legacy",
        "candidate_probability": _opt(getattr(p, "candidate_probability", None)),
        "candidate_projection": _opt(getattr(p, "candidate_projection", None), 2),
        "production_probability": _opt(getattr(p, "production_probability", None)),
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
        "kelly": _kelly_dict(row, bankroll),
    }
    return {k: data[k] for k in PICK_FIELDS}


def _parlay_dict(row: ParlayRow, venue_labels: dict[str, str], bankroll: float | None = None) -> dict:
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
        "kelly": _kelly_dict(row, bankroll),
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
    bankroll: float | None = None,
    top_n: int | None = None,
    track_record: dict | None = None,
) -> dict:
    """Assemble the deterministic, whitelisted payload for the LLM."""
    n_short = len(picks) if top_n is None else top_n
    pick_dicts = [_pick_dict(r, bankroll, i < n_short) for i, r in enumerate(picks)]
    venue_labels = {d["id"]: d["venue"] for d in pick_dicts}
    return {
        "date": game_date.isoformat(),
        "settings": {
            "odds_band_american": [min(min_odds, max_odds), max(min_odds, max_odds)],
            "min_edge_exclusive": min_edge,
            "stake": _r(stake, 2),
            "bankroll": _r(bankroll, 2) if bankroll else None,
            "top_n": n_short,
        },
        "odds_sources": {k: int(v) for k, v in sorted((odds_source_counts or {}).items())},
        "filter_counts": stats.as_dict(),
        "track_record": track_record,
        "notes": [
            "picks is a pool ranked by edge (P1 highest). in_shortlist marks the top_n by edge; parlays are "
            "built from the shortlist only",
            "model_true_probability is the probability that drives edge. It starts from the de-vigged "
            "consensus market (market_probability) and moves toward the statistical model only by "
            "model_weight, fitted on graded lines (0 means the model did not beat the market for that prop "
            "type, so the probability is pure consensus). The model is the frozen candidate when "
            "probability_method is 'candidate' (production_probability keeps the older production blend), "
            "else the production model",
            "implied_probability is the vig-included price at the listed venue; edge = model_true_probability - implied_probability",
            "venue_gap = market_probability - implied_probability: the part of the edge that is only this venue "
            "pricing differently from the consensus of venues_quoting venues",
            "model_own_probability is the model's chance before any market anchoring. model_agrees_with_edge "
            "is false when it is below implied_probability: the model itself does not back that edge. By "
            "default such props are removed before this list (filter_counts.model_agrees)",
            "track_record grades earlier boards against box scores; null when nothing is graded yet",
            "parlay model_true_probability is a Gaussian-copula joint probability using correlations fitted "
            "from backtest residuals; it is not a naive product",
            "kelly.bankroll_fraction is quarter-Kelly capped at 5% of bankroll; suggested_stake is null when no bankroll was given",
            "Kalshi and Polymarket are separate venues: a parlay mixing venues cannot be placed as one ticket",
        ],
        "picks": pick_dicts,
        "parlays": [_parlay_dict(r, venue_labels, bankroll) for r in parlays],
    }


def payload_to_json(payload: dict) -> str:
    """Stable JSON text for the prompt."""
    return json.dumps(payload, indent=2, sort_keys=False, default=str)
