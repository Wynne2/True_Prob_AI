"""
Fitted outcome spread for player props (experiment; production is not changed).

Question: given a forecast mean, how wide should the outcome distribution be?

Candidates write the variance as a non-negative combination of terms:

    mean          Var grows with the mean (Poisson-like, scaled)
    mean_sq       Var grows with the mean squared (negative-binomial-like)
    starter/bench separate mean scale by role
    recent_var    the player's own last-10 variance (player-specific spread)
    minutes_var   (mean / forecast minutes)^2 x variance of last-10 minutes
                  (spread that comes from unstable playing time)

Each formula is paired with a distribution family: normal, or a count
distribution (negative binomial when Var > mean, else Poisson).

Production is reproduced for reference with its own families:
    points   normal, SD = last-10 SD x 1.55 with floors
    rebounds negative binomial, Var = 2.45 x mean
    assists  negative binomial, Var = 2.0 x mean
    threes   binomial(n = forecast 3PA, p = forecast 3P%)
and again with the legacy 0.80 pull toward 50%.

Splits (all pre-2026 development data):
    fit     2024-25 season           -> variance coefficients
    select  2025-10-01 .. 2025-11-30 -> pick one formula + family per stat
    check   2025-12-01 .. 2025-12-31 -> reported once for every candidate

Scoring uses synthetic lines (forecast mean x 0.6 .. 1.4, rounded to .5).
They are correlated within a player-game and are not sportsbook lines.

Run: python -m engine.accuracy.variance_fit
"""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import optimize, stats

from engine.accuracy import decompose as dec

SELECT_START, CHECK_START, CHECK_END = "2025-10-01", "2025-12-01", "2026-01-01"
PARAMS_PATH = Path(__file__).resolve().parents[2] / "data" / "models" / "variance_fit_experimental.json"
REPORT_PATH = Path(__file__).resolve().parents[2] / "data" / "backtests" / "variance_fit.json"
BOOTSTRAP_REPS = 300
EPS = 1e-6

FORMULAS = {
    "mean": ("mean",),
    "mean_quad": ("mean", "mean_sq"),
    "role": ("starter", "bench"),
    "player": ("mean", "recent_var"),
    "player_quad": ("mean", "mean_sq", "recent_var"),
    "minutes": ("mean", "mean_sq", "minutes_var"),
    "player_minutes": ("mean", "mean_sq", "recent_var", "minutes_var"),
}
FAMILIES = ("normal", "count")

PRODUCTION_NB_INFLATION = {"rebounds": 2.45, "assists": 2.0}
LEGACY_SHRINK = 0.80


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def _var(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / (len(values) - 1)


def terms(row: dict) -> dict[str, float]:
    mu = max(dec.chain_prediction(row, frozenset()), 0.05)
    minutes_hat = max(row["forecast"]["minutes"], 1.0)
    starter = row.get("role") == "starter"
    return {
        "mean": mu,
        "mean_sq": mu * mu,
        "starter": mu if starter else 0.0,
        "bench": 0.0 if starter else mu,
        "recent_var": _var(row.get("recent_values") or []),
        "minutes_var": (mu / minutes_hat) ** 2 * _var(row.get("recent_minutes") or []),
    }


def design(rows: list[dict], names: tuple[str, ...]) -> np.ndarray:
    return np.array([[terms(row)[name] for name in names] for row in rows])


def fit_formula(rows: list[dict], names: tuple[str, ...]) -> list[float]:
    """Non-negative least squares of squared residual on the variance terms."""
    x = design(rows, names)
    actual = np.array([row["actual"] for row in rows])
    mu = np.array([terms(r)["mean"] for r in rows])
    target = (actual - mu) ** 2
    coef, _ = optimize.nnls(x, target)
    return [float(c) for c in coef]


def variance(row: dict, names: tuple[str, ...], coef: list[float]) -> float:
    t = terms(row)
    return max(sum(c * t[n] for n, c in zip(names, coef)), 0.05)


# ---------------------------------------------------------------------------
# Probabilities
# ---------------------------------------------------------------------------

def over_normal(mean: float, var: float, line: float) -> float:
    return float(stats.norm.sf(line, loc=mean, scale=math.sqrt(max(var, EPS))))


def over_count(mean: float, var: float, line: float) -> float:
    k = math.floor(line)
    mean = max(mean, 0.05)
    if var <= mean * 1.0001:
        return float(stats.poisson.sf(k, mean))
    r = mean * mean / (var - mean)
    p = r / (r + mean)
    return float(stats.nbinom.sf(k, r, p))


def production_over(row: dict, line: float) -> float:
    stat = row["stat"]
    mean = max(dec.chain_prediction(row, frozenset()), 0.05)
    if stat == "points":
        return over_normal(mean, dec.production_sd(row, mean) ** 2, line)
    if stat in PRODUCTION_NB_INFLATION:
        return over_count(mean, mean * PRODUCTION_NB_INFLATION[stat], line)
    if stat == "threes":
        n = max(1, int(round(row["forecast"]["minutes"] * row["forecast"]["volume"])))
        p = min(0.60, max(0.01, row["forecast"]["conversion"]))
        return float(stats.binom.sf(math.floor(line), n, p))
    raise KeyError(stat)


def synthetic_lines(row: dict) -> list[float]:
    mean = dec.chain_prediction(row, frozenset())
    return sorted({math.floor(mean * m) + 0.5 for m in dec.LINE_MULTIPLES})


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score(rows: list[dict], over_fn) -> dict:
    """Brier, log loss, favored-side reliability, and per-date sums for bootstrap."""
    probs, outcomes, dates = [], [], []
    for row in rows:
        for line in synthetic_lines(row):
            p = min(0.999, max(0.001, over_fn(row, line)))
            probs.append(p)
            outcomes.append(1.0 if row["actual"] > line else 0.0)
            dates.append(row["game_date"])
    p = np.array(probs)
    o = np.array(outcomes)
    brier = (p - o) ** 2
    logloss = -(o * np.log(p) + (1 - o) * np.log(1 - p))
    fav_p = np.where(p >= 0.5, p, 1 - p)
    fav_o = np.where(p >= 0.5, o, 1 - o)
    bins, ece = [], 0.0
    for low in (0.5, 0.6, 0.7, 0.8, 0.9):
        mask = (fav_p >= low) & (fav_p < low + 0.1 + (1e-9 if low == 0.9 else 0))
        if mask.sum():
            gap = float(fav_o[mask].mean() - fav_p[mask].mean())
            ece += mask.sum() / len(p) * abs(gap)
            bins.append({
                "bin": f"{int(low * 100)}-{int(low * 100) + 10}%",
                "n": int(mask.sum()),
                "predicted": float(fav_p[mask].mean()),
                "actual": float(fav_o[mask].mean()),
            })
    return {
        "n_lines": len(p),
        "brier": float(brier.mean()),
        "log_loss": float(logloss.mean()),
        "ece": float(ece),
        "bins": bins,
        "_brier": brier,
        "_dates": dates,
    }


def date_clustered_diff(a: dict, b: dict, seed: int = 11) -> dict:
    """Mean and 95% interval of (b - a) Brier, resampling dates."""
    by_date = defaultdict(lambda: [0.0, 0])
    for day, x, y in zip(a["_dates"], a["_brier"], b["_brier"]):
        by_date[day][0] += y - x
        by_date[day][1] += 1
    days = list(by_date)
    rng = random.Random(seed)
    draws = []
    for _ in range(BOOTSTRAP_REPS):
        pick = [days[rng.randrange(len(days))] for _ in days]
        total = sum(by_date[d][0] for d in pick)
        n = sum(by_date[d][1] for d in pick)
        draws.append(total / n)
    draws.sort()
    mean = float((b["_brier"] - a["_brier"]).mean())
    return {
        "brier_diff": mean,
        "ci95": [draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]],
        "n_dates": len(days),
    }


def _public(s: dict) -> dict:
    return {k: v for k, v in s.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Experiment
# ---------------------------------------------------------------------------

def split(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    fit = [r for r in rows if r["season"] == "2024-25"]
    select = [r for r in rows if SELECT_START <= r["game_date"] < CHECK_START]
    check = [r for r in rows if CHECK_START <= r["game_date"] < CHECK_END]
    if any(r["game_date"] >= CHECK_END for r in rows):
        raise ValueError("Variance fit saw a game on or after 2026-01-01")
    return fit, select, check


def run_stat(rows: list[dict]) -> dict:
    fit, select, check = split(rows)
    candidates: dict[str, dict] = {}
    for name, names in FORMULAS.items():
        coef = fit_formula(fit, names)
        for family in FAMILIES:
            tail = over_normal if family == "normal" else over_count

            def over(row, line, names=names, coef=coef, tail=tail):
                mean = max(dec.chain_prediction(row, frozenset()), 0.05)
                return tail(mean, variance(row, names, coef), line)

            candidates[f"{name}/{family}"] = {"terms": list(names), "coef": coef, "family": family, "over": over}

    def legacy(row, line):
        return 0.5 + LEGACY_SHRINK * (production_over(row, line) - 0.5)

    reference = {"production": production_over, "production_with_0.80_shrink": legacy}

    selection = {key: score(select, c["over"]) for key, c in candidates.items()}
    chosen = min(selection, key=lambda key: selection[key]["log_loss"])

    checked = {key: score(check, c["over"]) for key, c in candidates.items()}
    for key, fn in reference.items():
        selection[key] = score(select, fn)
        checked[key] = score(check, fn)

    base = checked["production"]
    return {
        "n_rows": {"fit": len(fit), "select": len(select), "check": len(check)},
        "chosen": chosen,
        "chosen_params": {k: v for k, v in candidates[chosen].items() if k != "over"},
        "all_params": {key: {k: v for k, v in c.items() if k != "over"} for key, c in candidates.items()},
        "select_scores": {k: {"brier": v["brier"], "log_loss": v["log_loss"], "ece": v["ece"]} for k, v in selection.items()},
        "check_scores": {k: _public(v) for k, v in checked.items()},
        "check_vs_production": {
            key: date_clustered_diff(base, checked[key])
            for key in (chosen, "production_with_0.80_shrink", "mean/count", "mean/normal")
            if key in checked
        },
    }


def main() -> None:
    from engine.backtest.advanced_history import load_historical_logs

    logs, _ = load_historical_logs()
    rows = dec.build_rows(logs)
    by_stat = defaultdict(list)
    for row in rows:
        by_stat[row["stat"]].append(row)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "splits": {
            "fit": "2024-25 season",
            "select": f"{SELECT_START} .. 2025-11-30",
            "check": f"{CHECK_START} .. 2025-12-31",
        },
        "caveats": [
            "Forecast mean is the walk-forward chain from engine/accuracy/decompose.py, not the production projection.",
            "Synthetic lines are correlated within a player-game and are not sportsbook lines.",
            "Production spread is reproduced from its constants; the production mean is not.",
            "Nothing here is read by the live app.",
        ],
        "stats": {stat: run_stat(stat_rows) for stat, stat_rows in sorted(by_stat.items())},
    }
    REPORT_PATH.write_text(json.dumps(report, indent=1), encoding="utf-8")
    PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(json.dumps({
        "experimental": True,
        "read_by_production": False,
        "generated_at": report["generated_at"],
        "splits": report["splits"],
        "by_stat": {stat: entry["chosen_params"] | {"chosen": entry["chosen"]} for stat, entry in report["stats"].items()},
    }, indent=1), encoding="utf-8")

    print(f"{'stat':<9}{'model':<30}{'select LL':>10}{'check LL':>10}{'check Brier':>12}{'ECE':>7}")
    for stat, entry in report["stats"].items():
        for key in (entry["chosen"], "mean/count", "mean/normal", "production", "production_with_0.80_shrink"):
            c = entry["check_scores"][key]
            s = entry["select_scores"][key]
            mark = " *" if key == entry["chosen"] else ""
            print(f"{stat:<9}{key + mark:<30}{s['log_loss']:>10.4f}{c['log_loss']:>10.4f}{c['brier']:>12.4f}{c['ece']:>7.3f}")
        d = entry["check_vs_production"][entry["chosen"]]
        print(f"{'':<9}chosen - production Brier {d['brier_diff']:+.4f} [{d['ci95'][0]:+.4f}, {d['ci95'][1]:+.4f}] over {d['n_dates']} dates")
        print(f"{'':<9}params {entry['chosen_params']['terms']} = {[round(c, 4) for c in entry['chosen_params']['coef']]}")
    print(f"\nSaved {REPORT_PATH}\nSaved {PARAMS_PATH}")


if __name__ == "__main__":
    main()
