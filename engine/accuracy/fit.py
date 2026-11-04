"""
Context weights for the accuracy experiment.

Fit only on games before 2026-01-01. December selects the penalty.
January, February, and March are not inputs.
"""

from __future__ import annotations

from collections import defaultdict

FEATURES = ("pace", "defensive_rating", "positional", "usage")
TARGET_STATS = ("minutes", "points", "rebounds", "assists", "threes", "fga", "fg3a", "fta")
TRAIN_END = "2026-01-01"
SELECT_START = "2025-12-01"
LAMBDAS = (0.0, 1.0, 10.0, 100.0, 1_000.0, 100_000.0)


def _solve(matrix: list[list[float]], target: list[float]) -> list[float]:
    size = len(target)
    work = [row[:] + [target[index]] for index, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(work[row][col]))
        work[col], work[pivot] = work[pivot], work[col]
        if abs(work[col][col]) < 1e-12:
            return [0.0] * size
        scale = work[col][col]
        for col_index in range(col, size + 1):
            work[col][col_index] /= scale
        for row in range(size):
            if row == col:
                continue
            factor = work[row][col]
            for col_index in range(col, size + 1):
                work[row][col_index] -= factor * work[col][col_index]
    return [work[row][size] for row in range(size)]


def ridge(rows: list[dict], lam: float, drop: tuple[str, ...] = ()) -> list[float]:
    width = len(FEATURES)
    dropped = {FEATURES.index(name) for name in drop}
    gram = [[lam if i == j else 0.0 for j in range(width)] for i in range(width)]
    side = [0.0] * width
    for row in rows:
        features = [0.0 if index in dropped else value for index, value in enumerate(row["x"])]
        residual = float(row["actual"]) - float(row["baseline"])
        for i in range(width):
            side[i] += features[i] * residual
            for j in range(width):
                gram[i][j] += features[i] * features[j]
    coef = _solve(gram, side)
    for index in dropped:
        coef[index] = 0.0
    return coef


def predict(baseline: float, features: list[float], coef: list[float]) -> float:
    shift = sum(weight * value for weight, value in zip(coef, features))
    return max(0.0, float(baseline) + shift)


def _mae(rows: list[dict], coef: list[float], drop: tuple[str, ...] = ()) -> float:
    if not rows:
        return 0.0
    total = 0.0
    for row in rows:
        features = list(row["x"])
        if drop:
            for name in drop:
                features[FEATURES.index(name)] = 0.0
        total += abs(float(row["actual"]) - predict(row["baseline"], features, coef))
    return total / len(rows)


def _split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    train = [row for row in rows if str(row["game_date"]) < SELECT_START]
    select = [row for row in rows if SELECT_START <= str(row["game_date"]) < TRAIN_END]
    return train, select


def fit_stat(rows: list[dict]) -> dict:
    """December chooses the penalty, including a penalty large enough to zero the fit."""
    leaked = [row for row in rows if str(row["game_date"]) >= TRAIN_END]
    if leaked:
        raise ValueError("Accuracy fit saw a game on or after 2026-01-01")
    train, select = _split(rows)
    if len(train) < 30 or len(select) < 10:
        return {
            "lambda": None,
            "coefficients": [0.0] * len(FEATURES),
            "reason": "Not enough pre-January games. Coefficients stay at 0.",
            "december_mae": None,
            "baseline_mae": _mae(select, [0.0] * len(FEATURES)) if select else None,
        }
    null_mae = _mae(select, [0.0] * len(FEATURES))
    best = (null_mae, None, [0.0] * len(FEATURES), "none")
    for lam in LAMBDAS:
        for channel, dropped in _DEFENSE_CHANNELS:
            coef = _pool_toward_zero(ridge(train, lam, drop=dropped))
            mae = _mae(select, coef)
            if mae < best[0] - 1e-9:
                best = (mae, lam, coef, channel)
    mae, lam, coef, channel = best
    coef = prune(select, coef)
    return {
        "lambda": lam,
        "coefficients": coef,
        "defense_channel": channel,
        "reason": (
            "December preferred the player prior alone. Context weights are zero."
            if lam is None
            else (
                "December chose the penalty and one defensive channel. "
                "Pace and defensive rating are not kept together. January through March were not used."
            )
        ),
        "december_mae": mae,
        "baseline_mae": null_mae,
        "n_train": len(train),
        "n_december": len(select),
    }


# Pace and defensive rating stay mutually exclusive so those two team ratings are not stacked.
_DEFENSE_CHANNELS = (
    ("none", ("pace", "defensive_rating", "positional", "usage")),
    ("usage", ("pace", "defensive_rating", "positional")),
    ("pace", ("defensive_rating", "positional")),
    ("defensive_rating", ("pace", "positional")),
    ("positional", ("pace", "defensive_rating")),
    ("pace_positional", ("defensive_rating",)),
    ("rating_positional", ("pace",)),
)


def prune(rows: list[dict], coef: list[float]) -> list[float]:
    """Drop a weight when December error falls without it."""
    current = list(coef)
    changed = True
    while changed:
        changed = False
        base = _mae(rows, current)
        for index in range(len(current)):
            if current[index] == 0.0:
                continue
            trial = list(current)
            trial[index] = 0.0
            if _mae(rows, trial) < base - 1e-9:
                current = trial
                changed = True
                break
    return current


def _pool_toward_zero(coef: list[float]) -> list[float]:
    """Shrink tiny weights to zero so a noisy factor is not kept."""
    return [0.0 if abs(weight) < 1e-4 else weight for weight in coef]


def ablate(rows: list[dict], lam: float) -> list[dict]:
    train, select = _split(rows)
    if lam is None or not train or not select:
        return []
    full = ridge(train, lam)
    full_mae = _mae(select, full)
    out = []
    for name in FEATURES:
        dropped = ridge(train, lam, drop=(name,))
        mae = _mae(select, dropped)
        out.append({
            "feature": name,
            "december_mae_without": mae,
            "december_mae_with_all": full_mae,
            "mae_increase_when_removed": mae - full_mae,
        })
    return out


def residual_scale(rows: list[dict], coef: list[float]) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if str(row["game_date"]) >= SELECT_START:
            continue
        error = float(row["actual"]) - predict(row["baseline"], row["x"], coef)
        grouped[str(row.get("role") or "bench")].append(error * error)
    scales = {}
    for role, squares in grouped.items():
        if len(squares) < 20:
            continue
        scales[role] = (sum(squares) / len(squares)) ** 0.5
    return scales


def calibration_slope(probabilities: list[float], outcomes: list[int]) -> float:
    """
    Shrink probability toward 0.5. The grid is fixed. Sportsbook prices are not an input.
    """
    if len(probabilities) < 30:
        return 1.0
    best_slope = 1.0
    best_brier = _brier(probabilities, outcomes, 1.0)
    for slope in (0.5, 0.7, 0.85, 1.0):
        score = _brier(probabilities, outcomes, slope)
        if score < best_brier - 1e-6:
            best_brier = score
            best_slope = slope
    return best_slope


def apply_calibration(probability: float, slope: float) -> float:
    return min(0.999, max(0.001, 0.5 + slope * (float(probability) - 0.5)))


def _brier(probabilities: list[float], outcomes: list[int], slope: float) -> float:
    total = 0.0
    for probability, outcome in zip(probabilities, outcomes):
        calibrated = apply_calibration(probability, slope)
        total += (calibrated - outcome) ** 2
    return total / len(probabilities)
