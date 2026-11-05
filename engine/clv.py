"""
Closing-line value (CLV) and outcome grading for saved pregame predictions.

``record_closing`` re-pulls Kalshi / Polymarket prices and stores, for every
ledger row whose game has not tipped yet, the latest consensus no-vig
probability. Run it shortly before tip-off; repeated runs keep the newest
pregame quote, so the last run before tip is the "closing" price.

``grade_predictions`` scores rows whose games are final against box scores and
reports Brier (model vs. market), hit rate, flat-stake ROI and average CLV.

CLV for a bet = closing no-vig probability − implied probability of the price
taken. Positive CLV over many bets is the fastest available evidence that the
picks beat the market; outcomes alone need thousands of bets.
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from engine.backtest_grade import brier_score, grade_prop, realized_roi
from engine.market_blend import consensus_market_probability

logger = logging.getLogger(__name__)

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "data" / "predictions"

_STAT_FIELDS = {
    "points": ("pts",),
    "rebounds": ("reb",),
    "assists": ("ast",),
    "threes": ("fg3m",),
    "steals": ("stl",),
    "blocks": ("blk",),
    "turnovers": ("tov",),
    "pra": ("pts", "reb", "ast"),
    "points_rebounds": ("pts", "reb"),
    "points_assists": ("pts", "ast"),
    "rebounds_assists": ("reb", "ast"),
}


def _norm_name(name: str) -> str:
    from engine.backtest.asof import normalize_player_name

    return normalize_player_name(str(name or ""))


def _value(v) -> str:
    return str(getattr(v, "value", v)).lower()


def parse_ledger_row(row: dict) -> Optional[tuple[str, str, float]]:
    """(prop_type, side, line) from structured fields, or from the legacy ``prop`` text."""
    if row.get("prop_type") and row.get("side") and row.get("line") is not None:
        return str(row["prop_type"]).lower(), str(row["side"]).lower(), float(row["line"])
    parts = str(row.get("prop") or "").split()
    if len(parts) < 3:
        return None
    try:
        return " ".join(parts[:-2]).lower(), parts[-2].lower(), float(parts[-1])
    except ValueError:
        return None


def row_key(row: dict) -> str:
    parsed = parse_ledger_row(row) or ("", "", 0.0)
    return "|".join((
        str(row.get("game_id") or ""), str(row.get("player_id") or ""),
        parsed[0], parsed[1], f"{parsed[2]:g}", str(row.get("sportsbook") or ""),
    ))


def load_ledger(game_date: date, directory: Path | str = DEFAULT_DIR) -> list[dict]:
    path = Path(directory) / f"{game_date.isoformat()}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _closing_path(game_date: date, directory: Path | str) -> Path:
    return Path(directory) / f"{game_date.isoformat()}.closing.json"


def _graded_path(game_date: date, directory: Path | str) -> Path:
    return Path(directory) / f"{game_date.isoformat()}.graded.json"


def _index_odds(odds_lines: Iterable) -> dict[tuple[str, str], list]:
    idx: dict[tuple[str, str], list] = defaultdict(list)
    for ol in odds_lines:
        idx[(_norm_name(ol.player_name), _value(ol.prop_type))].append(ol)
    return idx


def record_closing(
    game_date: date,
    directory: Path | str = DEFAULT_DIR,
    *,
    odds_lines: Optional[list] = None,
    pregame_game_ids: Optional[set[str]] = None,
    now: Optional[datetime] = None,
) -> dict:
    """
    Store the latest consensus no-vig price for ledger rows whose game has not
    started. Rows for started games keep their previous closing snapshot.
    """
    ledger = load_ledger(game_date, directory)
    path = _closing_path(game_date, directory)
    closing: dict[str, dict] = {}
    if path.exists():
        closing = {r["key"]: r for r in json.loads(path.read_text(encoding="utf-8"))}
    if not ledger:
        return {"ledger_rows": 0, "updated": 0, "path": str(path)}

    if pregame_game_ids is None:
        from data.loaders import load_games
        from utils.date_utils import filter_pregame_games

        pregame_game_ids = {str(g.game_id) for g in filter_pregame_games(load_games(game_date))}
    if odds_lines is None:
        from data.loaders import load_odds

        odds_lines = load_odds(game_date) if pregame_game_ids else []

    idx = _index_odds(odds_lines)
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    updated = 0
    for row in ledger:
        if str(row.get("game_id") or "") not in pregame_game_ids:
            continue
        parsed = parse_ledger_row(row)
        if not parsed:
            continue
        prop, side, line = parsed
        quotes = idx.get((_norm_name(row.get("player", "")), prop), [])
        p_close = consensus_market_probability(quotes, line, side)
        if p_close is None:
            continue
        implied = float(row.get("implied_probability") or 0.0)
        key = row_key(row)
        closing[key] = {
            "key": key,
            "recorded_at": stamp,
            "closing_probability": round(p_close, 6),
            "n_venues": sum(1 for q in quotes if abs(float(q.line) - line) < 1e-9),
            "clv": round(p_close - implied, 6) if implied else None,
        }
        updated += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(closing.values()), indent=2), encoding="utf-8")
    return {
        "ledger_rows": len(ledger),
        "pregame_games": len(pregame_game_ids),
        "updated": updated,
        "closing_rows": len(closing),
        "path": str(path),
    }


def actual_stat(log: dict, prop_type: str) -> Optional[float]:
    fields = _STAT_FIELDS.get(prop_type)
    if not fields or log is None:
        return None
    minutes = log.get("min")
    if minutes is not None and float(minutes) <= 0:
        return None
    total = 0.0
    for f in fields:
        if log.get(f) is None:
            return None
        total += float(log[f])
    return total


def _final_games_and_logs(game_date: date) -> tuple[set[str], list[dict]]:
    from data.loaders.nba_api_loader import _client, map_box_score_to_backtest_log

    client = _client()
    games = client.paginate("v1/games", {"dates": [game_date.isoformat()]})
    final_ids = {str(g.get("id")) for g in games if str(g.get("status") or "").lower() == "final"}
    if not final_ids:
        return set(), []
    rows = client.paginate("v1/stats", {"dates": [game_date.isoformat()]})
    return final_ids, [map_box_score_to_backtest_log(r) | {"game_id": str((r.get("game") or {}).get("id") or "")}
                       for r in rows]


def _summary(rows: list[dict]) -> dict:
    decided = [r for r in rows if r["result"] in ("win", "loss")]
    out: dict = {"n": len(rows), "decided": len(decided)}
    if not decided:
        return out
    y = [1 if r["result"] == "win" else 0 for r in decided]
    out["hit_rate"] = round(sum(y) / len(y), 4)
    out["avg_model_probability"] = round(statistics.fmean(r["model_probability"] for r in decided), 4)
    out["brier_model"] = round(statistics.fmean(
        brier_score(r["model_probability"], o) for r, o in zip(decided, y)), 5)
    with_market = [(r, o) for r, o in zip(decided, y) if r.get("market_probability") is not None]
    if with_market:
        out["brier_market"] = round(statistics.fmean(
            brier_score(r["market_probability"], o) for r, o in with_market), 5)
        out["brier_model_same_rows"] = round(statistics.fmean(
            brier_score(r["model_probability"], o) for r, o in with_market), 5)
    rois = [r["roi"] for r in rows if r.get("roi") is not None]
    if rois:
        out["flat_roi_per_bet"] = round(statistics.fmean(rois), 4)
    clvs = [r["clv"] for r in rows if r.get("clv") is not None]
    if clvs:
        out["avg_clv"] = round(statistics.fmean(clvs), 5)
        out["clv_positive_share"] = round(sum(c > 0 for c in clvs) / len(clvs), 4)
    return out


def grade_predictions(
    game_date: date,
    directory: Path | str = DEFAULT_DIR,
    *,
    final_game_ids: Optional[set[str]] = None,
    box_logs: Optional[list[dict]] = None,
) -> dict:
    """Grade ledger rows for final games; write ``{date}.graded.json`` and return a summary."""
    ledger = load_ledger(game_date, directory)
    if not ledger:
        return {"ledger_rows": 0}
    if final_game_ids is None or box_logs is None:
        final_game_ids, box_logs = _final_games_and_logs(game_date)

    closing_path = _closing_path(game_date, directory)
    closing = {}
    if closing_path.exists():
        closing = {r["key"]: r for r in json.loads(closing_path.read_text(encoding="utf-8"))}

    by_player: dict[tuple[str, str], dict] = {}
    by_name: dict[tuple[str, str], dict] = {}
    for log in box_logs:
        gid = str(log.get("game_id") or "")
        by_player[(gid, str(log.get("player_id") or ""))] = log
        by_name[(gid, _norm_name(log.get("player_name", "")))] = log

    graded: list[dict] = []
    for row in ledger:
        gid = str(row.get("game_id") or "")
        if gid not in final_game_ids:
            continue
        parsed = parse_ledger_row(row)
        if not parsed:
            continue
        prop, side, line = parsed
        log = by_player.get((gid, str(row.get("player_id") or ""))) or by_name.get((gid, _norm_name(row.get("player", ""))))
        actual = actual_stat(log, prop) if log else None
        result = "void" if actual is None else grade_prop(actual, line, side)
        close = closing.get(row_key(row)) or {}
        graded.append({
            "key": row_key(row),
            "player": row.get("player"),
            "prop": f"{prop} {side} {line:g}",
            "sportsbook": row.get("sportsbook"),
            "odds": row.get("odds"),
            "edge": row.get("edge"),
            "model_probability": float(row.get("model_probability") or 0.0),
            "market_probability": row.get("market_probability"),
            "probability_method": row.get("probability_method", "legacy"),
            "implied_probability": row.get("implied_probability"),
            "candidate_probability": row.get("candidate_probability"),
            "production_probability": (
                row.get("production_probability")
                if row.get("production_probability") is not None
                else (None if row.get("probability_method") == "candidate" else row.get("model_probability"))
            ),
            "candidate_version": row.get("candidate_version"),
            "candidate_outside_range": row.get("candidate_outside_range", False),
            "matchup": row.get("matchup"),
            "game_id": gid,
            "actual": actual,
            "result": result,
            "roi": realized_roi(None, int(row.get("odds") or 0), result) if result != "void" else None,
            "closing_probability": close.get("closing_probability"),
            "clv": close.get("clv"),
        })

    positive = [r for r in graded if (r.get("edge") or 0) > 0]
    summary = {
        "date": game_date.isoformat(),
        "ledger_rows": len(ledger),
        "final_games": len(final_game_ids),
        "all": _summary(graded),
        "positive_edge": _summary(positive),
    }
    path = _graded_path(game_date, directory)
    path.write_text(json.dumps({"summary": summary, "rows": graded}, indent=2), encoding="utf-8")
    summary["path"] = str(path)
    return summary
