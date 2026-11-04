"""
Fit projection weights before 2026, then score March once.

January and February are not used to choose weights. Production models are not edited.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from engine.accuracy.audit import MISSING_HISTORICAL_FIELDS, PRODUCTION_FACTORS
from engine.accuracy.dataset import build_player_games
from engine.accuracy.fit import (
    FEATURES,
    TARGET_STATS,
    TRAIN_END,
    ablate,
    apply_calibration,
    calibration_slope,
    fit_stat,
    predict,
    residual_scale,
)
from engine.accuracy.probability import over_probability, scale_for
from engine.backtest.advanced_history import HISTORY_WINDOWS, load_historical_logs, load_march_2026_logs
from engine.backtest.experiment import (
    CREDIT_APPROVAL_LIMIT,
    _list_dates,
    _odds_cached,
    _query_at,
    _score_events,
    select_february,
)
from engine.backtest.metrics import (
    brier_and_log_loss,
    calibration_bins,
    clustered_scores,
    group_metric,
    paired_brier_difference,
)
from engine.backtest.odds_history import SAMPLE_MARKETS, SAMPLE_REGION, estimate_event_odds_cost
from engine.backtest.variants import VARIANT_A, VARIANT_C

_REPORT = Path("data/backtests/accuracy_experiment.json")
_MARCH_DATES = (
    date(2026, 3, 2), date(2026, 3, 4), date(2026, 3, 6), date(2026, 3, 9),
    date(2026, 3, 11), date(2026, 3, 13), date(2026, 3, 16), date(2026, 3, 18),
    date(2026, 3, 20), date(2026, 3, 23), date(2026, 3, 25), date(2026, 3, 27),
)
_MARCH_BACKUP = (date(2026, 3, 1), date(2026, 3, 3), date(2026, 3, 8), date(2026, 3, 30))
_PROP_STAT = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "threes": "threes",
}


def estimate_march_cost(n_dates: int = 12, n_games: int = 30) -> dict:
    odds = estimate_event_odds_cost(n_games, SAMPLE_MARKETS, SAMPLE_REGION)
    return {
        "events_list_credits": n_dates,
        "event_odds_credits": odds,
        "total_credits": n_dates + odds,
        "n_dates": n_dates,
        "n_games": n_games,
        "approval_limit": CREDIT_APPROVAL_LIMIT,
        "weights_already_frozen": True,
    }


def run_accuracy_experiment(credit_cap: int = CREDIT_APPROVAL_LIMIT) -> dict:
    print("Loading cached logs through February 2026.", flush=True)
    logs, coverage = load_historical_logs()
    print(f"Building pre-January rows from {coverage.get('joined_rows')} logs.", flush=True)
    training = build_player_games(logs, date(2025, 10, 1), date(2025, 12, 31))
    print(f"Fitting {len(training)} player-stat rows. March is not loaded yet.", flush=True)
    frozen = _freeze(training)
    if any(str(row["game_date"]) >= TRAIN_END for row in training):
        raise RuntimeError("Training rows crossed into 2026")

    cost = estimate_march_cost()
    print("Weights frozen. Loading March box scores.", flush=True)
    march_logs = load_march_2026_logs()
    combined = logs + march_logs
    march_rows = build_player_games(combined, date(2026, 3, 1), date(2026, 3, 31))
    projection = _projection_report(march_rows, frozen)

    report = {
        "production_change": {
            "applied": False,
            "reason": (
                "Accuracy weights were not written into the production models. "
                "Betting thresholds were not tuned."
            ),
        },
        "audit": list(PRODUCTION_FACTORS),
        "missing": list(MISSING_HISTORICAL_FIELDS),
        "freeze": frozen,
        "march_projection": projection,
        "coverage": {
            "history_windows": [f"{start}_{end}" for start, end, _season in HISTORY_WINDOWS],
            "feature_rows_before_march": coverage.get("joined_rows"),
            "potential_assists": (coverage.get("features") or {}).get("potential_assists"),
            "estimated_cost": cost,
            "january_february": "Not used to choose weights. Both periods were already examined.",
        },
    }
    if cost["total_credits"] > credit_cap:
        report["march_probability"] = {
            "status": "not_run",
            "reason": f"Estimate {cost['total_credits']} exceeds the {credit_cap} credit cap.",
        }
        _write(report, [])
        return report

    priced = _price_march(combined, credit_cap)
    report["march_probability"] = _probability_report(priced, frozen, march_rows)
    report["coverage"]["priced_games"] = len({row.get("game_id") for row in priced})
    _write(report, priced)
    return report


def _freeze(rows: list[dict]) -> dict:
    by_stat: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_stat[row["stat"]].append(row)
    fits = {}
    scales = {}
    slopes = {}
    ablation = {}
    for stat in TARGET_STATS:
        sample = by_stat.get(stat) or []
        fit = fit_stat(sample)
        fits[stat] = {
            "lambda": fit.get("lambda"),
            "coefficients": {name: weight for name, weight in zip(FEATURES, fit["coefficients"])},
            "defense_channel": fit.get("defense_channel"),
            "december_mae": fit.get("december_mae"),
            "baseline_mae": fit.get("baseline_mae"),
            "n_train": fit.get("n_train"),
            "n_december": fit.get("n_december"),
            "reason": fit.get("reason"),
        }
        scales[stat] = residual_scale(sample, fit["coefficients"])
        ablation[stat] = ablate(sample, fit["lambda"]) if fit.get("lambda") is not None else []
        slopes[stat] = _december_slope(sample, fit["coefficients"], scales[stat], stat)
    return {
        "selected_on": "October-November fit, December 2025 selection",
        "train_end": TRAIN_END,
        "january_used": False,
        "february_used": False,
        "march_used": False,
        "blowout_added": False,
        "market_blended": False,
        "fits": fits,
        "residual_sd": scales,
        "calibration_slope": slopes,
        "ablation_december": ablation,
        "features": list(FEATURES),
    }


def _december_slope(rows: list[dict], coef: list[float], scales: dict, stat: str) -> float:
    probabilities = []
    outcomes = []
    for row in rows:
        if not ("2025-12-01" <= str(row["game_date"]) < TRAIN_END):
            continue
        line = round(float(row["baseline"]) * 2.0) / 2.0
        actual = float(row["actual"])
        if actual == line:
            continue
        mean = predict(row["baseline"], row["x"], coef)
        probability = over_probability(mean, scale_for(scales, row["role"], stat), line)
        probabilities.append(probability)
        outcomes.append(1 if actual > line else 0)
    return calibration_slope(probabilities, outcomes)


def _projection_report(rows: list[dict], frozen: dict) -> dict:
    by_stat: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_stat[row["stat"]].append(row)
    stats = {}
    for stat, sample in by_stat.items():
        coef = _coef(frozen, stat)
        prior_errors = []
        model_errors = []
        by_date_prior: dict[str, list[float]] = defaultdict(list)
        by_date_model: dict[str, list[float]] = defaultdict(list)
        by_role: dict[str, list[float]] = defaultdict(list)
        for row in sample:
            prior = abs(float(row["actual"]) - float(row["baseline"]))
            model = abs(float(row["actual"]) - predict(row["baseline"], row["x"], coef))
            prior_errors.append(prior)
            model_errors.append(model)
            by_date_prior[row["game_date"]].append(prior)
            by_date_model[row["game_date"]].append(model)
            by_role[row["role"]].append(model)
        stats[stat] = {
            "n": len(sample),
            "prior_mae": _avg(prior_errors),
            "context_mae": _avg(model_errors),
            "prior_date_se": _date_se(by_date_prior),
            "context_date_se": _date_se(by_date_model),
            "by_role_mae": {role: _avg(errors) for role, errors in by_role.items()},
        }
    return {
        "window": "March 2026 box scores. Weights were already frozen.",
        "n_player_games": len({(row["game_id"], row["player_id"]) for row in rows}),
        "by_stat": stats,
    }


def _price_march(logs: list[dict], credit_cap: int) -> list[dict]:
    listed, _quota = _list_dates(_MARCH_DATES)
    selected = select_february(listed, max_games=30)
    if len(selected) < 30:
        extra, _quota = _list_dates(_MARCH_BACKUP)
        selected = select_february(listed + extra, max_games=30)
    cold = sum(40 for event in selected if not _odds_cached(event))
    list_credits = len(_MARCH_DATES) + (len(_MARCH_BACKUP) if len(selected) > 0 else 0)
    if list_credits + cold > credit_cap:
        return []
    return _score_events(selected, logs, period="march_accuracy")


def _probability_report(rows: list[dict], frozen: dict, march_rows: list[dict]) -> dict:
    if not rows:
        return {"status": "not_run", "reason": "No March prices were scored."}
    index = {
        (row["game_date"], row["player_id"], row["stat"]): row
        for row in march_rows
    }
    matched = []
    for row in rows:
        if row.get("variant") != VARIANT_A or not row.get("evaluation_line"):
            continue
        if row.get("side") != "over" or row.get("result") not in ("win", "loss"):
            continue
        stat = _PROP_STAT.get(row.get("prop_type"))
        source = index.get((row.get("game_date"), str(row.get("player_id")), stat))
        if source is None:
            continue
        coef = _coef(frozen, stat)
        mean = predict(source["baseline"], source["x"], coef)
        probability = over_probability(
            mean, scale_for(frozen["residual_sd"].get(stat) or {}, source["role"], stat), float(row["line"]),
        )
        slope = float((frozen.get("calibration_slope") or {}).get(stat) or 1.0)
        copy = dict(row)
        copy["c_projected"] = mean
        copy["c_probability"] = probability
        copy["d_probability"] = apply_calibration(probability, slope)
        copy["role"] = source["role"]
        copy["matchup_band"] = _band(source.get("pos_gap"))
        copy["accuracy_baseline"] = source["baseline"]
        matched.append(copy)

    full = {
        (row.get("game_date"), row.get("player_id"), row.get("prop_type"), row.get("line")): row
        for row in rows
        if row.get("variant") == VARIANT_C and row.get("evaluation_line") and row.get("side") == "over"
    }
    for row in matched:
        production = full.get((row.get("game_date"), row.get("player_id"), row.get("prop_type"), row.get("line")))
        row["b_probability"] = None if production is None else production.get("raw_probability")
        row["b_projected"] = None if production is None else production.get("projected")

    usable = [row for row in matched if row.get("b_probability") is not None]
    return {
        "status": "scored",
        "n": len(usable),
        "n_games": len({row.get("game_id") for row in usable}),
        "n_dates": len({row.get("game_date") for row in usable}),
        "models": {
            "A_opportunity": _pack(usable, "raw_probability"),
            "B_full_production": _pack(usable, "b_probability"),
            "C_context_projection": _pack(usable, "c_probability"),
            "D_calibrated": _pack(usable, "d_probability"),
            "E_sportsbook_novig": _pack(usable, "novig_probability"),
        },
        "c_minus_a": _paired(usable, "raw_probability", "c_probability"),
        "d_minus_a": _paired(usable, "raw_probability", "d_probability"),
        "projection_mae": _line_mae(usable),
        "by_prop": {name: _pack(bucket, "c_probability") for name, bucket in _groups(usable, "prop_type")},
        "by_role": {name: _pack(bucket, "c_probability") for name, bucket in _groups(usable, "role")},
        "by_matchup": {name: _pack(bucket, "c_probability") for name, bucket in _groups(usable, "matchup_band")},
        "high_probability_70_90": {
            "A_opportunity": _high(usable, "raw_probability"),
            "C_context_projection": _high(usable, "c_probability"),
            "D_calibrated": _high(usable, "d_probability"),
            "E_sportsbook_novig": _high(usable, "novig_probability"),
        },
        "note": (
            "B is the production model with measured pace, defensive rating, and positional defense. "
            "Historical injuries were not available, so usage and minutes vacuums stay neutral. "
            "No-vig prices are not blended into C or D."
        ),
    }


def _pack(rows: list[dict], key: str) -> dict:
    if not rows:
        return {"n": 0}
    scores = clustered_scores(rows, key)
    scores["log_loss"] = brier_and_log_loss(rows, key).get("log_loss")
    scores["calibration"] = calibration_bins(rows, key)
    scores["by_prop"] = group_metric(rows, "prop_type", key)
    return scores


def _paired(rows: list[dict], left: str, right: str) -> dict:
    prepared = []
    for row in rows:
        base = dict(row)
        base["raw_probability"] = row[left]
        base["variant"] = "left"
        other = dict(row)
        other["raw_probability"] = row[right]
        other["variant"] = "right"
        prepared.extend((base, other))
    return paired_brier_difference(prepared, "left", "right")


def _line_mae(rows: list[dict]) -> dict:
    out = {}
    for prop, bucket in _groups(rows, "prop_type"):
        out[prop] = {
            "n": len(bucket),
            "A_opportunity": _avg([abs(float(row["projected"]) - float(row["actual"])) for row in bucket]),
            "B_full_production": _avg([
                abs(float(row["b_projected"]) - float(row["actual"])) for row in bucket if row.get("b_projected") is not None
            ]),
            "C_context_projection": _avg([abs(float(row["c_projected"]) - float(row["actual"])) for row in bucket]),
            "player_prior": _avg([abs(float(row["accuracy_baseline"]) - float(row["actual"])) for row in bucket]),
        }
    return out


def _high(rows: list[dict], key: str) -> dict:
    sample = [row for row in rows if 0.70 <= float(row[key]) < 0.90]
    if not sample:
        return {"n": 0}
    predicted = sum(float(row[key]) for row in sample) / len(sample)
    actual = sum(int(row["outcome"]) for row in sample) / len(sample)
    return {"n": len(sample), "predicted_avg": predicted, "actual_rate": actual, "gap": predicted - actual}


def _groups(rows: list[dict], key: str):
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "unknown")].append(row)
    return sorted(groups.items())


def _band(gap) -> str:
    value = float(gap or 0)
    if value > 0.05:
        return "favorable"
    if value < -0.05:
        return "unfavorable"
    return "neutral"


def _coef(frozen: dict, stat: str) -> list[float]:
    weights = ((frozen.get("fits") or {}).get(stat) or {}).get("coefficients") or {}
    return [float(weights.get(name) or 0.0) for name in FEATURES]


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _date_se(by_date: dict[str, list[float]]) -> float | None:
    means = [sum(values) / len(values) for values in by_date.values() if values]
    if len(means) < 2:
        return None
    center = sum(means) / len(means)
    var = sum((value - center) ** 2 for value in means) / (len(means) - 1)
    return (var / len(means)) ** 0.5


def _write(report: dict, rows: list[dict]) -> None:
    _REPORT.parent.mkdir(parents=True, exist_ok=True)
    keep = [
        row for row in rows
        if row.get("evaluation_line") and row.get("side") == "over" and row.get("variant") == VARIANT_A
    ]
    _REPORT.write_text(json.dumps({"report": report, "rows": keep}, indent=2, default=str), encoding="utf-8")


def load_accuracy_report(path: Path = _REPORT) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
