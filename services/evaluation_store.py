"""Append-only forecast, settlement, and chronological evaluation ledger."""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
import hashlib
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from config import get_settings
from domain.entities import PropProbability
from domain.enums import PropSide


_CREATE_FORECASTS = """
CREATE TABLE IF NOT EXISTS forecasts (
    forecast_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    game_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    prop_type TEXT NOT NULL,
    line REAL NOT NULL,
    side TEXT NOT NULL,
    bookmaker_key TEXT NOT NULL,
    american_odds INTEGER NOT NULL,
    true_probability REAL NOT NULL,
    implied_probability REAL NOT NULL,
    model_version TEXT NOT NULL,
    distribution_type TEXT NOT NULL,
    feature_snapshot_json TEXT NOT NULL,
    quote_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS forecasts_observed_at ON forecasts(observed_at);
CREATE INDEX IF NOT EXISTS forecasts_game_id ON forecasts(game_id);
CREATE TABLE IF NOT EXISTS settlements (
    forecast_id TEXT PRIMARY KEY REFERENCES forecasts(forecast_id),
    settled_at TEXT NOT NULL,
    actual_value REAL,
    status TEXT NOT NULL CHECK(status IN ('win', 'loss', 'push', 'void')),
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settlement_revisions (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_id TEXT NOT NULL REFERENCES forecasts(forecast_id),
    settled_at TEXT NOT NULL,
    actual_value REAL,
    status TEXT NOT NULL CHECK(status IN ('win', 'loss', 'push', 'void', 'pending_manual_review')),
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS settlement_revisions_forecast ON settlement_revisions(forecast_id, revision_id);
CREATE TABLE IF NOT EXISTS exclusions (
    exclusion_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    game_id TEXT,
    player_id TEXT,
    reason TEXT NOT NULL,
    context_json TEXT NOT NULL
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported snapshot value: {type(value).__name__}")


@dataclass(frozen=True)
class EvaluationReport:
    settled_count: int
    brier_score: Optional[float]
    log_loss: Optional[float]
    quoted_return_per_unit: Optional[float]
    reliability: list[dict[str, float | int]]


class EvaluationStore:
    """SQLite-backed ledger suitable for a single-process local deployment."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        path = Path(db_path or get_settings().evaluation_db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with self._connection() as conn:
            conn.executescript(_CREATE_FORECASTS)
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(forecasts)").fetchall()
            }
            if "decision_key" not in columns:
                conn.execute("ALTER TABLE forecasts ADD COLUMN decision_key TEXT")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS forecasts_decision_key "
                    "ON forecasts(decision_key)"
                )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        """Open, commit or roll back, and always close one SQLite connection."""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record_forecasts(
        self,
        forecasts: Iterable[PropProbability],
        feature_store: Mapping[tuple[str, str], Any],
        *,
        snapshot_id: Optional[str] = None,
        observed_at: Optional[str] = None,
        model_version: str = "heuristic-v1",
    ) -> str:
        """Persist every evaluated offer with its exact input and quote snapshot."""
        snapshot_id = snapshot_id or str(uuid.uuid4())
        observed_at = observed_at or _utc_now()
        rows: list[tuple[Any, ...]] = []

        for forecast in forecasts:
            quote = forecast.selected_quote
            if quote is None:
                continue
            feature = feature_store.get((forecast.player_id, forecast.prop_type.value))
            feature_json = json.dumps(feature, default=_json_default, sort_keys=True)
            quote_json = json.dumps(quote, default=_json_default, sort_keys=True)
            decision_material = {
                "game_id": forecast.game_id,
                "player_id": forecast.player_id,
                "prop_type": forecast.prop_type.value,
                "line": forecast.line,
                "side": forecast.side.value,
                "book": forecast.best_book_key or forecast.best_book.value,
                "price": forecast.sportsbook_odds,
                "quote": quote_json,
                "feature": feature_json,
                "true_probability": forecast.true_probability,
                "implied_probability": forecast.implied_probability,
                "model_version": model_version,
            }
            decision_key = hashlib.sha256(
                json.dumps(decision_material, default=_json_default, sort_keys=True).encode()
            ).hexdigest()
            rows.append((
                str(uuid.uuid4()),
                snapshot_id,
                observed_at,
                forecast.game_id,
                forecast.player_id,
                forecast.player_name,
                forecast.prop_type.value,
                forecast.line,
                forecast.side.value,
                forecast.best_book_key or forecast.best_book.value,
                forecast.sportsbook_odds,
                forecast.true_probability,
                forecast.implied_probability,
                model_version,
                forecast.distribution_type.value,
                feature_json,
                quote_json,
                decision_key,
            ))

        if not rows:
            return snapshot_id

        with self._connection() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO forecasts (
                    forecast_id, snapshot_id, observed_at, game_id, player_id,
                    player_name, prop_type, line, side, bookmaker_key,
                    american_odds, true_probability, implied_probability,
                    model_version, distribution_type, feature_snapshot_json,
                    quote_json, decision_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return snapshot_id

    def record_exclusions(
        self,
        exclusions: Iterable[Mapping[str, Any]],
        *,
        snapshot_id: str,
        observed_at: Optional[str] = None,
    ) -> None:
        """Persist exclusion reasons alongside forecasts; they are never discarded."""
        now = observed_at or _utc_now()
        rows = [
            (
                snapshot_id,
                now,
                item.get("game_id"),
                item.get("player_id"),
                str(item.get("reason") or "unspecified"),
                json.dumps(dict(item), default=_json_default, sort_keys=True),
            )
            for item in exclusions
        ]
        if not rows:
            return
        with self._connection() as conn:
            conn.executemany(
                """
                INSERT INTO exclusions (
                    snapshot_id, observed_at, game_id, player_id, reason, context_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def settle_game(
        self,
        game_id: str,
        player_stat_totals: Mapping[str, Mapping[str, float]],
        *,
        source: str,
        settled_at: Optional[str] = None,
    ) -> int:
        """
        Settle forecasts using canonical player IDs and prop totals.

        Whole-number markets become push when totals equal the line. A missing
        player/prop total remains unsettled rather than being guessed.
        """
        settled_at = settled_at or _utc_now()
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT f.*,
                    (
                        SELECT status FROM settlement_revisions r
                        WHERE r.forecast_id = f.forecast_id
                        ORDER BY r.revision_id DESC LIMIT 1
                    ) AS latest_status,
                    (
                        SELECT actual_value FROM settlement_revisions r
                        WHERE r.forecast_id = f.forecast_id
                        ORDER BY r.revision_id DESC LIMIT 1
                    ) AS latest_actual_value
                FROM forecasts f
                WHERE f.game_id = ?
                """,
                (game_id,),
            ).fetchall()
            settlements = []
            for row in rows:
                stats = player_stat_totals.get(row["player_id"])
                if stats is None or row["prop_type"] not in stats:
                    continue
                actual_value = float(stats[row["prop_type"]])
                if actual_value == row["line"]:
                    status = "push"
                elif row["side"] == PropSide.OVER.value:
                    status = "win" if actual_value > row["line"] else "loss"
                else:
                    status = "win" if actual_value < row["line"] else "loss"
                if row["latest_status"] == status and row["latest_actual_value"] == actual_value:
                    continue
                settlements.append((row["forecast_id"], settled_at, actual_value, status, source))
            conn.executemany(
                """
                INSERT INTO settlement_revisions (forecast_id, settled_at, actual_value, status, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                settlements,
            )
        return len(settlements)

    def report(
        self,
        *,
        start_at: Optional[str] = None,
        end_at: Optional[str] = None,
        bins: int = 10,
    ) -> EvaluationReport:
        """Return proper scores and reliability bins for settled win/loss offers."""
        clauses = ["s.status IN ('win', 'loss')"]
        parameters: list[Any] = []
        if start_at:
            clauses.append("f.observed_at >= ?")
            parameters.append(start_at)
        if end_at:
            clauses.append("f.observed_at < ?")
            parameters.append(end_at)
        where = " AND ".join(clauses)

        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT f.true_probability, f.american_odds, s.status
                FROM forecasts f JOIN settlement_revisions s ON s.revision_id = (
                    SELECT r.revision_id FROM settlement_revisions r
                    WHERE r.forecast_id = f.forecast_id
                    ORDER BY r.revision_id DESC LIMIT 1
                )
                WHERE {where}
                ORDER BY f.observed_at
                """,
                parameters,
            ).fetchall()

        if not rows:
            return EvaluationReport(0, None, None, None, [])

        outcomes = [1.0 if row["status"] == "win" else 0.0 for row in rows]
        probabilities = [float(row["true_probability"]) for row in rows]
        brier = sum((probability - outcome) ** 2 for probability, outcome in zip(probabilities, outcomes)) / len(rows)
        eps = 1e-12
        log_loss = -sum(
            outcome * math.log(max(eps, probability))
            + (1.0 - outcome) * math.log(max(eps, 1.0 - probability))
            for probability, outcome in zip(probabilities, outcomes)
        ) / len(rows)
        returns = [
            (
                (row["american_odds"] / 100.0 if row["american_odds"] > 0 else 100.0 / abs(row["american_odds"]))
                if outcome
                else -1.0
            )
            for row, outcome in zip(rows, outcomes)
        ]

        buckets: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
        for probability, outcome in zip(probabilities, outcomes):
            buckets[min(bins - 1, int(probability * bins))].append((probability, outcome))
        reliability = [
            {
                "lower_probability": index / bins,
                "upper_probability": (index + 1) / bins,
                "count": len(bucket),
                "mean_predicted": sum(item[0] for item in bucket) / len(bucket),
                "win_rate": sum(item[1] for item in bucket) / len(bucket),
            }
            for index, bucket in enumerate(buckets)
            if bucket
        ]
        return EvaluationReport(
            settled_count=len(rows),
            brier_score=brier,
            log_loss=log_loss,
            quoted_return_per_unit=sum(returns) / len(returns),
            reliability=reliability,
        )
