"""
Shared background work: one slate scan, one arbitrage scan and the AI Advisor runs.

A scan is expensive (data-provider calls, minutes of work), so every customer reads the
same cached result. A scan starts only when a signed-in customer asks and the cache is
older than its time-to-live; at most one of each kind runs at a time.

shortcut: threads inside the single web process. Move to a job queue (RQ/Celery) when the
app runs on more than one instance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pickle
import threading
import time
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from web import db

logger = logging.getLogger(__name__)

BOARD_DIR = db.ROOT / "data" / "boards"
BOARD_TTL = 30 * 60
ARB_TTL = 15 * 60
RUN_INLINE = False  # tests run jobs in the calling thread

_lock = threading.Lock()
_running: dict[str, float] = {}
_errors: dict[str, str] = {}
_props: dict[str, list] = {}  # slate -> live prop objects, for the Advisor


def today() -> date:
    from utils.date_utils import today_eastern

    return today_eastern()


def _start(name: str, work: Callable[[], None]) -> bool:
    """Run ``work`` in the background unless a job with this name is already running."""
    with _lock:
        if name in _running:
            return False
        _running[name] = time.time()

    def run() -> None:
        try:
            work()
            _errors.pop(name, None)
        except Exception as exc:  # a failed scan must leave the app usable
            logger.exception("Job %s failed", name)
            _errors[name] = f"{type(exc).__name__}: {exc}"[:300]
        finally:
            with _lock:
                _running.pop(name, None)

    if RUN_INLINE:
        run()
    else:
        threading.Thread(target=run, name=name, daemon=True).start()
    return True


def status(name: str) -> dict:
    started = _running.get(name)
    return {"running": started is not None, "seconds": int(time.time() - started) if started else 0,
            "error": _errors.get(name, "")}


def health() -> dict:
    return {"running": {k: int(time.time() - v) for k, v in _running.items()}, "errors": dict(_errors)}


# ---------------------------------------------------------------------------
# Slate board
# ---------------------------------------------------------------------------

def prop_id(p) -> str:
    raw = f"{p.player_id}|{p.prop_type.value}|{p.line}|{p.side.value}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _venue_range(p) -> tuple[Optional[float], Optional[float], int]:
    """Lowest and highest no-vig chance for this side across venues quoting this exact line."""
    from engine.market_blend import no_vig_side_probability

    probs, books = [], set()
    for ol in getattr(p, "all_lines", None) or []:
        try:
            if abs(float(ol.line) - float(p.line)) > 1e-9:
                continue
        except (TypeError, ValueError):
            continue
        books.add(getattr(ol, "book_key", "") or str(getattr(ol.book, "value", ol.book)))
        q = no_vig_side_probability(getattr(ol, "over_odds", 0), getattr(ol, "under_odds", 0), p.side.value)
        if q is not None:
            probs.append(q)
    return (min(probs), max(probs), len(books)) if probs else (None, None, len(books))


def prop_row(p) -> dict:
    """The fields the product shows for one priced line. Each probability keeps its own name."""
    from engine.model_agreement import model_agrees, own_model_probability
    from odds.normalizer import american_to_decimal
    from utils.formatting import book_display_name

    lo, hi, venues = _venue_range(p)
    dec = american_to_decimal(int(p.sportsbook_odds))
    own = own_model_probability(p)
    return {
        "id": prop_id(p),
        "player": p.player_name, "team": p.team_abbr or "", "opponent": p.opponent_abbr or "",
        "game_id": str(p.game_id), "prop": p.prop_type.value, "side": p.side.value, "line": float(p.line),
        "odds": int(p.sportsbook_odds),
        "venue": book_display_name(p.best_book, getattr(p, "best_book_key", "") or ""),
        "implied": float(p.implied_probability),             # the offered price, vig included
        "market": None if p.market_probability is None else float(p.market_probability),  # no-vig consensus
        "market_lo": lo, "market_hi": hi, "venues": venues,
        "model_own": own,                                     # the statistical model alone
        "blended": float(p.true_probability),                 # what edge is measured from
        "weight": float(getattr(p, "model_weight", 0.0) or 0.0),
        "method": getattr(p, "probability_method", "legacy") or "legacy",
        "edge": float(p.edge),
        "ev_per_100": round(100 * (float(p.true_probability) * dec - 1), 2),
        "confidence": getattr(p.confidence, "value", str(p.confidence)),
        "projection": round(float(p.projected_value or 0), 2),             # production model
        "cand_projection": None if getattr(p, "candidate_projection", None) is None else round(float(p.candidate_projection), 2),
        "minutes": round(float(p.expected_minutes or 0), 1),
        "model_agrees": bool(model_agrees(p)),
        "warnings": list(getattr(p, "calibration_warnings", None) or [])[:8],
        "notes": (p.explanation or "")[:900],
    }


def _board_path(slate: str) -> Path:
    return BOARD_DIR / f"{slate}.json"


def load_board(slate: Optional[str] = None) -> Optional[dict]:
    path = _board_path(slate or today().isoformat())
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def board_is_stale(board: Optional[dict]) -> bool:
    return board is None or time.time() - float(board.get("scanned_at", 0)) > BOARD_TTL


def _scan_board(day: date) -> None:
    from engine.board import scan_board

    board = scan_board(day)
    slate = day.isoformat()
    cand = board.candidate_status or {}
    snapshot = {
        "slate": slate,
        "scanned_at": time.time(),
        "games": [{"away": g.away_team_abbr, "home": g.home_team_abbr, "status": str(getattr(g, "status", "") or "")}
                  for g in board.games],
        "pregame_games": board.pregame_games,
        "sources": {k: int(v) for k, v in (board.odds_source_counts or {}).items()},
        "source_errors": {k: str(v)[:200] for k, v in (board.odds_source_errors or {}).items()},
        "candidate": {k: cand.get(k) for k in ("status", "version", "attached", "reason")},
        "rows": [prop_row(p) for p in board.props],
    }
    BOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _board_path(slate).with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot), encoding="utf-8")
    tmp.replace(_board_path(slate))
    _props[slate] = board.props
    try:
        (BOARD_DIR / f"{slate}.pkl").write_bytes(pickle.dumps(board.props))
        if board.props:  # pregame snapshot for next-day grading; the first save of a prop keeps its price
            from engine.prediction_ledger import save_pregame_predictions

            save_pregame_predictions(day, board.props)
    except Exception:
        logger.exception("Could not persist board extras for %s", slate)


def ensure_board() -> dict:
    """Start a slate scan when the cache is stale. Returns the job status."""
    day = today()
    if db.flag("scan") and board_is_stale(load_board(day.isoformat())):
        _start("board", lambda: _scan_board(day))
    return status("board")


def board_props(slate: str) -> Optional[list]:
    if slate not in _props:
        try:
            # Our own file, written above; never a customer upload.
            _props[slate] = pickle.loads((BOARD_DIR / f"{slate}.pkl").read_bytes())
        except Exception:
            return None
    return _props[slate]


# ---------------------------------------------------------------------------
# Arbitrage
# ---------------------------------------------------------------------------

ARB_PATH = BOARD_DIR / "arbitrage.json"


def load_arb() -> Optional[dict]:
    try:
        return json.loads(ARB_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _scan_arb() -> None:
    from arbitrage.scanner import scan

    started = time.time()
    result = scan(min_profit=5.0, min_annual=0.10)
    result.update(scanned_at=time.time(), seconds=round(time.time() - started))
    BOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ARB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(result), encoding="utf-8")
    tmp.replace(ARB_PATH)


def ensure_arb() -> dict:
    arb = load_arb()
    if db.flag("arbitrage") and (arb is None or time.time() - float(arb.get("scanned_at", 0)) > ARB_TTL):
        _start("arbitrage", _scan_arb)
    return status("arbitrage")


# ---------------------------------------------------------------------------
# AI Advisor
# ---------------------------------------------------------------------------

def start_advisor(user_id: int, slate: str, min_odds: int, max_odds: int) -> Optional[int]:
    """Charge for and start one Advisor run. None when the customer cannot pay. Refunded if the run fails."""
    props = board_props(slate)
    if not props:
        return None
    run_id = db.execute("INSERT INTO advisor_runs (user_id, slate, status, created_at) VALUES (?,?,?,?)",
                        (user_id, slate, "running", time.time()))
    if db.spend(user_id, "advisor", str(run_id), f"AI Advisor analysis ({slate})") != "ok":
        db.execute("DELETE FROM advisor_runs WHERE id=?", (run_id,))
        return None

    def work() -> None:
        try:
            from datetime import date as _date

            from engine.advisor.report import to_markdown
            from engine.advisor.run import AI_OK, AdvisorOptions, run_advisor

            result = run_advisor(_date.fromisoformat(slate), AdvisorOptions(min_odds=min_odds, max_odds=max_odds, min_edge=0.0),
                                 props=props)
            if result.ai_status != AI_OK and result.picks:
                raise RuntimeError(result.ai_error or "the AI step did not complete")
            db.execute("UPDATE advisor_runs SET status='done', body=? WHERE id=?", (to_markdown(result), run_id))
        except Exception as exc:
            db.execute("UPDATE advisor_runs SET status='failed', body=? WHERE id=?", (f"{type(exc).__name__}: {exc}"[:300], run_id))
            db.refund(user_id, "advisor", str(run_id), "the analysis did not complete")
            raise

    _start(f"advisor:{run_id}", work)
    return run_id
