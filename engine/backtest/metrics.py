"""Scoring summaries. Pushes are excluded from Brier and log loss. They are not losses."""

from __future__ import annotations

import math
from collections import defaultdict

from engine.backtest_grade import brier_score, realized_roi

PROBABILITY_BINS = (
    (0.40, 0.50),
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.90),
    (0.90, 1.01),
)

ODDS_BANDS = (
    ("-600 to -400", -600, -400),
    ("-399 to -200", -399, -200),
    ("-199 to -110", -199, -110),
    ("-109 to +100", -109, 100),
    ("above +100", 101, 100000),
)

EDGE_THRESHOLDS = (0.03, 0.05, 0.07, 0.10)


def _clip(probability: float) -> float:
    return min(max(probability, 1e-6), 1.0 - 1e-6)


def log_loss(probability: float, outcome: int) -> float:
    p = _clip(probability)
    if outcome == 1:
        return -math.log(p)
    return -math.log(1.0 - p)


def select_primary_lines(rows: list[dict]) -> list[dict]:
    """One line per variant, player, prop, and side: the line closest to the projection.

    Alternate lines on the same stat are the same observation. They stay in the
    log, but they are not extra evidence.
    """
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (
            row.get("variant"),
            row.get("player_id"),
            row.get("prop_type"),
            row.get("side"),
        )
        groups.setdefault(key, []).append(row)
    chosen = []
    for group in groups.values():
        chosen.append(min(
            group,
            key=lambda row: abs(float(row.get("line") or 0) - float(row.get("projected") or 0)),
        ))
    return chosen


def settled_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("result") in ("win", "loss")]


def brier_and_log_loss(rows: list[dict], probability_key: str = "raw_probability") -> dict:
    graded = settled_rows(rows)
    if not graded:
        return {"n": 0, "brier": None, "log_loss": None, "note": "No settled non-push results."}
    brier = sum(brier_score(float(row[probability_key]), int(row["outcome"])) for row in graded) / len(graded)
    loss = sum(log_loss(float(row[probability_key]), int(row["outcome"])) for row in graded) / len(graded)
    return {"n": len(graded), "brier": brier, "log_loss": loss}


def calibration_bins(rows: list[dict], probability_key: str = "raw_probability") -> list[dict]:
    graded = settled_rows(rows)
    out = []
    for low, high in PROBABILITY_BINS:
        bucket = [
            row for row in graded
            if low <= float(row[probability_key]) < high
        ]
        n = len(bucket)
        if n == 0:
            out.append({
                "bin": f"{int(low * 100)}–{min(int(high * 100), 100)}%",
                "n": 0,
                "predicted_avg": None,
                "actual_rate": None,
                "uncertainty": None,
            })
            continue
        predicted = sum(float(row[probability_key]) for row in bucket) / n
        actual = sum(int(row["outcome"]) for row in bucket) / n
        # Binomial standard error with a small floor so 0/n is not reported as
        # certain. Rows from the same game are still not independent.
        uncertainty = math.sqrt((actual * (1.0 - actual) + 0.25 / n) / n)
        out.append({
            "bin": f"{int(low * 100)}–{min(int(high * 100), 100)}%",
            "n": n,
            "predicted_avg": predicted,
            "actual_rate": actual,
            "uncertainty": uncertainty,
        })
    return out


def mean_absolute_error(rows: list[dict]) -> dict:
    seen = {}
    for row in rows:
        if row.get("actual") is None or row.get("result") == "void":
            continue
        key = (row.get("game_id"), row.get("player_id"), row.get("prop_type"))
        seen[key] = abs(float(row["projected"]) - float(row["actual"]))
    if not seen:
        return {"n": 0, "mae": None}
    return {"n": len(seen), "mae": sum(seen.values()) / len(seen)}


def _in_band(odds: int, low: int, high: int) -> bool:
    return low <= int(odds) <= high


def roi_summary(rows: list[dict], edge_key: str = "edge_calibrated") -> dict:
    """One unit per selected wager. Game-level means are the uncertainty unit."""
    by_band = []
    for label, low, high in ODDS_BANDS:
        chosen = [
            row for row in rows
            if row.get("bet_side") and _in_band(int(row.get("american_odds") or 0), low, high)
            and row.get("result") in ("win", "loss", "push")
        ]
        by_band.append({"band": label, **_roi_block(chosen)})
    by_threshold = []
    for threshold in EDGE_THRESHOLDS:
        chosen = [
            row for row in rows
            if row.get("bet_side")
            and float(row.get(edge_key) or 0) >= threshold
            and row.get("result") in ("win", "loss", "push")
        ]
        by_threshold.append({"min_edge": threshold, **_roi_block(chosen)})
    return {"by_odds_band": by_band, "by_edge_threshold": by_threshold}


def _roi_block(rows: list[dict]) -> dict:
    if not rows:
        return {"n_bets": 0, "roi": None, "game_cluster_se": None, "n_games": 0}
    profits = []
    by_game: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        profit = realized_roi(None, int(row["american_odds"]), row["result"])
        if profit is None:
            continue
        profits.append(profit)
        by_game[str(row.get("game_id") or "")].append(profit)
    if not profits:
        return {"n_bets": 0, "roi": None, "game_cluster_se": None, "n_games": 0}
    game_means = [sum(vals) / len(vals) for vals in by_game.values() if vals]
    n_games = len(game_means)
    if n_games >= 2:
        mean_g = sum(game_means) / n_games
        var = sum((g - mean_g) ** 2 for g in game_means) / (n_games - 1)
        se = math.sqrt(var / n_games)
    else:
        se = None
    return {
        "n_bets": len(profits),
        "roi": sum(profits) / len(profits),
        "game_cluster_se": se,
        "n_games": n_games,
    }


def group_metric(rows: list[dict], key: str, probability_key: str = "raw_probability") -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in settled_rows(rows):
        groups[str(row.get(key) or "unknown")].append(row)
    out = []
    for name, bucket in sorted(groups.items()):
        scores = brier_and_log_loss(bucket, probability_key)
        out.append({"group": name, **scores})
    return out


def chronological_split(dates: list[str]) -> dict:
    """
    Last date is the untouched test period. The date before that is validation.
    Everything earlier is training. Fewer than three dates cannot support a retune.
    """
    ordered = sorted(set(dates))
    if len(ordered) < 3:
        return {
            "can_retune": False,
            "reason": "Fewer than three chronological dates. Production parameters stay unchanged.",
            "train": ordered[:-1] if len(ordered) > 1 else [],
            "validation": [],
            "test": ordered[-1:] if ordered else [],
        }
    return {
        "can_retune": True,
        "reason": "Train, validation, and test are separate dates. Do not fit on the test date.",
        "train": ordered[:-2],
        "validation": [ordered[-2]],
        "test": [ordered[-1]],
    }


def _standard_error(values: list[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    center = sum(values) / n
    variance = sum((value - center) ** 2 for value in values) / (n - 1)
    return math.sqrt(variance / n)


def profit_if_win(american: int) -> float:
    odds = int(american)
    if odds >= 0:
        return odds / 100.0
    return 100.0 / abs(odds)


def expected_roi(probability: float, american: int) -> float:
    """Expected profit on one unit staked. Pushes are not given separate probability."""
    return float(probability) * profit_if_win(american) - (1.0 - float(probability))


def clustered_scores(rows: list[dict], probability_key: str = "raw_probability") -> dict:
    """Mean Brier across games, with uncertainty across games and across dates."""
    by_game: dict[str, list[dict]] = defaultdict(list)
    game_date: dict[str, str] = {}
    for row in settled_rows(rows):
        game_id = str(row.get("game_id") or "")
        by_game[game_id].append(row)
        game_date[game_id] = str(row.get("game_date") or "")
    game_briers = []
    by_date: dict[str, list[float]] = defaultdict(list)
    for game_id, bucket in by_game.items():
        score = brier_and_log_loss(bucket, probability_key).get("brier")
        if score is None:
            continue
        game_briers.append(score)
        by_date[game_date[game_id]].append(score)
    date_means = [sum(values) / len(values) for values in by_date.values() if values]
    overall = brier_and_log_loss(rows, probability_key)
    return {
        **overall,
        "n_games": len(game_briers),
        "n_dates": len(date_means),
        "game_cluster_se": _standard_error(game_briers),
        "date_cluster_se": _standard_error(date_means),
    }


def paired_brier_difference(
    rows: list[dict],
    left: str,
    right: str,
    probability_key: str = "raw_probability",
) -> dict:
    """
    Game-level Brier of ``right`` minus ``left`` on the same props.

    A negative difference means ``right`` assigned probabilities more accurately.
    """
    def _index(variant: str) -> dict[tuple, dict]:
        indexed = {}
        for row in settled_rows(rows):
            if row.get("variant") != variant:
                continue
            key = (
                row.get("game_id"), row.get("player_id"), row.get("prop_type"),
                row.get("side"), row.get("line"),
            )
            indexed[key] = row
        return indexed

    left_rows = _index(left)
    right_rows = _index(right)
    by_game: dict[str, list[float]] = defaultdict(list)
    game_date: dict[str, str] = {}
    shared = 0
    for key, left_row in left_rows.items():
        right_row = right_rows.get(key)
        if right_row is None:
            continue
        shared += 1
        left_brier = brier_score(float(left_row[probability_key]), int(left_row["outcome"]))
        right_brier = brier_score(float(right_row[probability_key]), int(right_row["outcome"]))
        game_id = str(key[0])
        by_game[game_id].append(right_brier - left_brier)
        game_date[game_id] = str(left_row.get("game_date") or "")
    game_diffs = [sum(values) / len(values) for values in by_game.values() if values]
    by_date: dict[str, list[float]] = defaultdict(list)
    for game_id, diff in zip(by_game.keys(), game_diffs):
        by_date[game_date.get(game_id, "")].append(diff)
    date_means = [sum(values) / len(values) for values in by_date.values() if values]
    mean_diff = sum(game_diffs) / len(game_diffs) if game_diffs else None
    return {
        "left": left,
        "right": right,
        "n_shared_rows": shared,
        "n_games": len(game_diffs),
        "n_dates": len(date_means),
        "mean_brier_difference": mean_diff,
        "game_cluster_se": _standard_error(game_diffs),
        "date_cluster_se": _standard_error(date_means),
        "reading": (
            "Negative means the second model has the lower Brier."
            if mean_diff is not None else "No shared settled rows."
        ),
    }


def compare_variants(rows: list[dict]) -> list[dict]:
    """Lower Brier is better. A small sample is not evidence of a stable edge."""
    out = []
    for variant in sorted({row.get("variant") for row in rows if row.get("variant")}):
        subset = [row for row in rows if row.get("variant") == variant]
        scores = brier_and_log_loss(subset)
        out.append({
            "variant": variant,
            **scores,
            "mae": mean_absolute_error(subset).get("mae"),
        })
    return out
