"""Append-only pregame prop records. A later grade must not replace the saved price."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "data" / "predictions"


def _row_key(row: dict) -> tuple:
    return (
        str(row.get("player_id") or ""),
        str(row.get("game_id") or ""),
        str(row.get("prop") or ""),
        str(row.get("sportsbook") or ""),
    )


def _prop_row(prop, game_date: date, saved_at: str) -> dict:
    raw = float(getattr(prop, "raw_model_probability", 0.0) or prop.true_probability)
    calibrated = float(getattr(prop, "calibrated_probability", 0.0) or prop.true_probability)
    market = getattr(prop, "market_probability", None)
    return {
        "saved_at": saved_at,
        "game_date": game_date.isoformat(),
        "player": prop.player_name,
        "player_id": prop.player_id,
        "team_abbr": prop.team_abbr,
        "opponent_abbr": prop.opponent_abbr,
        "matchup": f"{prop.team_abbr} vs {prop.opponent_abbr}",
        "game_id": prop.game_id,
        "prop": f"{prop.prop_type.value} {prop.side.value} {prop.line}",
        "prop_type": prop.prop_type.value,
        "side": prop.side.value,
        "line": float(prop.line),
        "sportsbook": prop.best_book_key or getattr(prop.best_book, "value", str(prop.best_book)),
        "odds": prop.sportsbook_odds,
        "implied_probability": prop.implied_probability,
        "market_probability": None if market is None else float(market),
        "raw_model_probability": raw,
        "calibrated_probability": calibrated,
        "model_probability": float(prop.true_probability),
        "model_weight": float(getattr(prop, "model_weight", 0.0) or 0.0),
        "probability_method": getattr(prop, "probability_method", "legacy") or "legacy",
        "edge": prop.edge,
        "projected_value": float(getattr(prop, "projected_value", 0.0) or 0.0),
        "projected_minutes": prop.expected_minutes,
        "confidence": getattr(prop.confidence, "value", str(prop.confidence)),
    }


def save_pregame_predictions(game_date: date, props: list, directory: str | Path = DEFAULT_DIR) -> Path:
    """
    Add new pregame rows. An existing key keeps its original odds and probabilities.
    """
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{game_date.isoformat()}.json"
    saved_at = datetime.now(timezone.utc).isoformat()
    existing: list[dict] = []
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    kept = { _row_key(row): row for row in existing }
    for prop in props:
        row = _prop_row(prop, game_date, saved_at)
        key = _row_key(row)
        if key not in kept:
            kept[key] = row
    ordered = list(kept.values())
    path.write_text(json.dumps(ordered, indent=2), encoding="utf-8")
    return path
