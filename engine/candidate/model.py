"""
Candidate v1: one opportunity chain with learned pieces and a fitted spread.

    mean = minutes x opportunities per minute x conversion per opportunity

    minutes     ridge on recency, rest, last season and own missed games
                (engine/candidate/minutes.py, pregame-only "ridge_clean")
    volume      per stat, the 20-game ratio or the selected ridge
                (engine/candidate/volume.py), whichever had lower November
                stat error
    conversion  the chain's shrunk recent rate (engine/accuracy/decompose.py);
                conversion is mostly night-to-night noise, so it is not modeled
    spread      variance = non-negative mix of mean, mean^2, the player's own
                last-10 variance and minutes volatility, refit around THIS mean.
                Formula and family per stat are the ones chosen in
                engine/accuracy/variance_fit.py.

No sportsbook price is an input. Production is not changed by this module.

Steps
    develop()  fit on 2024-25 + Oct 2025, choose on Nov 2025, report Dec 2025
    freeze()   refit the chosen settings on all of 2024-25 and 2025-26 and
               write data/models/candidate_v1.json. After this, nothing is tuned.

Run: python -m engine.candidate.model
"""

from __future__ import annotations

import json
import math
import subprocess
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
from scipy import optimize, stats

from engine.accuracy import decompose as dec
from engine.accuracy import variance_fit as vf
from engine.candidate import minutes as cm
from engine.candidate import volume as cv

VERSION = "candidate-v1"
ROOT = Path(__file__).resolve().parents[2]
PARAMS_PATH = ROOT / "data" / "models" / "candidate_v1.json"
DEV_REPORT_PATH = ROOT / "data" / "backtests" / "candidate_v1_development.json"
FREEZE_END = "2026-10-01"   # first day of 2026-27; the holdout starts here

# Chosen in engine/accuracy/variance_fit.py on November 2025 (see data/models/variance_fit_experimental.json).
SPREAD_FORMULA = {
    "points": (("mean", "mean_sq", "minutes_var"), "normal"),
    "rebounds": (("mean", "mean_sq", "recent_var", "minutes_var"), "count"),
    "assists": (("mean", "mean_sq", "minutes_var"), "count"),
    "threes": (("mean", "mean_sq", "minutes_var"), "count"),
}
EXTRA_2025_26_WINDOWS = (
    (date(2026, 3, 1), date(2026, 3, 31), "2025-26"),
    (date(2026, 4, 1), date(2026, 4, 13), "2025-26"),
)


# ---------------------------------------------------------------------------
# Serializable ridge models
# ---------------------------------------------------------------------------

def ridge_to_dict(model) -> dict:
    return {
        "names": list(model.names), "lam": model.lam, "mu": model.mu.tolist(),
        "sd": model.sd.tolist(), "intercept": model.intercept, "beta": model.beta.tolist(),
    }


def ridge_from_dict(data: dict, cls):
    model = cls(tuple(data["names"]), data["lam"])
    model.mu = np.array(data["mu"])
    model.sd = np.array(data["sd"])
    model.intercept = float(data["intercept"])
    model.beta = np.array(data["beta"])
    return model


# ---------------------------------------------------------------------------
# Items: one per player-game-stat, joining the three walk-forward builders
# ---------------------------------------------------------------------------

def build_items(logs: list[dict], target_end: str, emit_from: str = "", spreads: dict | None = None) -> list[dict]:
    chain = dec.build_rows(logs, target_end=target_end, emit_from=emit_from)
    minutes = {
        (r["player_id"], r["game_id"]): r
        for r in cm.build_rows(logs, target_end=target_end, emit_from=emit_from, spreads=spreads)
    }
    volume = {(r["player_id"], r["game_id"], r["stat"]): r for r in cv.build_rows(logs, target_end=target_end, emit_from=emit_from)}
    items = []
    for c in chain:
        m = minutes.get((c["player_id"], c["game_id"]))
        v = volume.get((c["player_id"], c["game_id"], c["stat"]))
        if m is None or v is None:
            continue
        items.append({
            "stat": c["stat"], "season": c["season"], "game_date": c["game_date"],
            "game_id": c["game_id"], "player_id": c["player_id"], "role": c["role"],
            "actual": c["actual"], "chain": c, "minutes_row": m, "volume_row": v,
        })
    return items


def _by_stat(items):
    groups = defaultdict(list)
    for it in items:
        groups[it["stat"]].append(it)
    return groups


# ---------------------------------------------------------------------------
# Prediction with a parameter set
# ---------------------------------------------------------------------------

def predict(items: list[dict], params: dict) -> list[dict]:
    """Mean, variance and components for each item. Order matches ``items``."""
    if not items:
        return []
    minutes_model = ridge_from_dict(params["minutes"], cm.Ridge)
    minutes = minutes_model.predict([it["minutes_row"] for it in items])
    volume = np.zeros(len(items))
    for stat, idx in _index_by_stat(items).items():
        rows = [items[i]["volume_row"] for i in idx]
        choice = params["volume"][stat]
        if choice["choice"] == "ridge":
            rates = ridge_from_dict(choice["ridge"], cv.WeightedRidge).predict(rows)
        else:
            rates = np.array([r["features"]["base_rate"] for r in rows])
        volume[idx] = rates
    out = []
    for i, it in enumerate(items):
        conversion = it["chain"]["forecast"]["conversion"]
        mean = max(float(minutes[i] * volume[i] * conversion), 0.05)
        spread = params["spread"][it["stat"]]
        var = _variance(it, mean, float(minutes[i]), spread["terms"], spread["coef"])
        out.append({
            "minutes": float(minutes[i]), "volume": float(volume[i]), "conversion": float(conversion),
            "mean": mean, "variance": var, "family": spread["family"],
            "calibration": (params.get("calibration") or {}).get(it["stat"]),
        })
    return out


def _index_by_stat(items):
    idx = defaultdict(list)
    for i, it in enumerate(items):
        idx[it["stat"]].append(i)
    return {k: np.array(v) for k, v in idx.items()}


def _var(values) -> float:
    values = list(values)
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    return sum((v - m) ** 2 for v in values) / (len(values) - 1)


def spread_terms(item: dict, mean: float, minutes: float) -> dict[str, float]:
    recent_minutes = item["chain"].get("recent_minutes") or item["minutes_row"]["history"][-10:]
    return {
        "mean": mean,
        "mean_sq": mean * mean,
        "recent_var": _var(item["chain"].get("recent_values") or []),
        "minutes_var": (mean / max(minutes, 1.0)) ** 2 * _var(recent_minutes),
    }


def _variance(item, mean, minutes, names, coef) -> float:
    t = spread_terms(item, mean, minutes)
    return max(sum(c * t[n] for n, c in zip(names, coef)), 0.05)


def over_probability(pred: dict, line: float) -> float:
    """P(stat > line). Half-point lines have no push. Applies the stat's calibration when present."""
    if pred["family"] == "normal":
        p = vf.over_normal(pred["mean"], pred["variance"], line)
    else:
        p = vf.over_count(pred["mean"], pred["variance"], line)
    p = min(0.999, max(0.001, p))
    cal = pred.get("calibration")
    if cal:
        z = cal["a"] + cal["b"] * math.log(p / (1.0 - p))
        p = 1.0 / (1.0 + math.exp(-z))
    return min(0.999, max(0.001, p))


def push_probability(pred: dict, line: float) -> float:
    """P(stat == line) for whole-number lines on count stats; 0 for half-points and normal."""
    if float(line) != math.floor(line) or pred["family"] == "normal":
        return 0.0
    k = int(line)
    mean, var = max(pred["mean"], 0.05), pred["variance"]
    if var <= mean * 1.0001:
        return float(stats.poisson.pmf(k, mean))
    r = mean * mean / (var - mean)
    return float(stats.nbinom.pmf(k, r, r / (r + mean)))


# ---------------------------------------------------------------------------
# Baseline (Model A chain) and fitting
# ---------------------------------------------------------------------------

def baseline_over(item: dict, line: float) -> float:
    """Last-10 minutes x 20-game volume x conversion, with production's spread rules."""
    return min(0.999, max(0.001, vf.production_over(item["chain"], line)))


def fit_spread(items: list[dict], preds: list[dict]) -> dict:
    out = {}
    for stat, (names, family) in SPREAD_FORMULA.items():
        pairs = [(it, p) for it, p in zip(items, preds) if it["stat"] == stat]
        x = np.array([[spread_terms(it, p["mean"], p["minutes"])[n] for n in names] for it, p in pairs])
        y = np.array([(it["actual"] - p["mean"]) ** 2 for it, p in pairs])
        coef, _ = optimize.nnls(x, y)
        out[stat] = {"terms": list(names), "coef": [float(c) for c in coef], "family": family}
    return out


def _placeholder_spread() -> dict:
    return {s: {"terms": list(n), "coef": [1.0] + [0.0] * (len(n) - 1), "family": f} for s, (n, f) in SPREAD_FORMULA.items()}


def fit_params(fit_items: list[dict], settings: dict | None = None, select_items: list[dict] | None = None) -> tuple[dict, dict]:
    """
    Fit every component on ``fit_items``.

    With ``select_items`` the settings (penalties, feature groups, volume choice)
    are chosen on them. With ``settings`` they are reused as given (freeze).
    """
    choosing = settings is None or "minutes_lambda" not in settings
    settings = dict(settings or {})
    minute_rows_fit = _unique_minutes(fit_items)

    minute_features = tuple(settings.get("minutes_features") or cm.CLEAN_FEATURES)
    if choosing:
        minute_rows_sel = _unique_minutes(select_items)
        fitted = [cm.Ridge(minute_features, lam).fit(minute_rows_fit) for lam in cm.LAMBDAS]
        minutes_model = min(fitted, key=lambda m: cm._mae(m.predict(minute_rows_sel), minute_rows_sel))
        settings["minutes_lambda"] = minutes_model.lam
        settings["minutes_features"] = list(minute_features)
    else:
        minutes_model = cm.Ridge(minute_features, settings["minutes_lambda"]).fit(minute_rows_fit)

    params = {"version": VERSION, "minutes": ridge_to_dict(minutes_model), "volume": {}, "spread": _placeholder_spread()}
    fit_by, sel_by = _by_stat(fit_items), _by_stat(select_items or [])
    settings.setdefault("volume", {})
    for stat in dec.CHAINS:
        vrows_fit = [it["volume_row"] for it in fit_by[stat]]
        if choosing:
            vrows_sel = [it["volume_row"] for it in sel_by[stat]]
            fitted = cv.fit_stat(vrows_fit, vrows_sel)
            ridge = fitted["selected"]
            settings["volume"][stat] = {
                "lambda": ridge.lam,
                "dropped_groups": fitted["dropped_groups"],
                "features": list(ridge.names),
            }
        else:
            spec = settings["volume"][stat]
            ridge = cv.WeightedRidge(tuple(spec["features"]), spec["lambda"]).fit(vrows_fit)
        params["volume"][stat] = {"choice": "ridge", "ridge": ridge_to_dict(ridge)}

    if choosing:
        # Ridge volume or the plain 20-game ratio: whichever gives lower November stat error.
        for stat in dec.CHAINS:
            sel = sel_by[stat]
            errors = {}
            for choice in ("ridge", "ratio20"):
                trial = json.loads(json.dumps(params))
                trial["volume"][stat]["choice"] = choice
                preds = predict(sel, trial)
                errors[choice] = float(np.mean([abs(p["mean"] - it["actual"]) for p, it in zip(preds, sel)]))
            settings["volume"][stat]["choice"] = min(errors, key=errors.get)
            settings["volume"][stat]["november_stat_mae"] = errors
    for stat in dec.CHAINS:
        params["volume"][stat]["choice"] = settings["volume"][stat]["choice"]

    params["spread"] = fit_spread(fit_items, predict(fit_items, params))
    return params, settings


def _unique_minutes(items):
    seen, rows = set(), []
    for it in items or []:
        key = (it["player_id"], it["game_id"])
        if key not in seen:
            seen.add(key)
            rows.append(it["minutes_row"])
    return rows


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def evaluate(items: list[dict], params: dict) -> dict:
    """Baseline chain vs candidate on identical synthetic lines (from the baseline mean)."""
    preds = predict(items, params)
    pred_of = {id(it["chain"]): p for it, p in zip(items, preds)}
    chains = [it["chain"] for it in items]

    def candidate_over(row, line):
        return over_probability(pred_of[id(row)], line)

    def candidate_mean_prod_spread(row, line):
        p = pred_of[id(row)]
        stat, mean = row["stat"], p["mean"]
        if stat == "points":
            return vf.over_normal(mean, dec.production_sd(row, mean) ** 2, line)
        if stat in vf.PRODUCTION_NB_INFLATION:
            return vf.over_count(mean, mean * vf.PRODUCTION_NB_INFLATION[stat], line)
        n = max(1, int(round(p["minutes"] * p["volume"])))
        return float(stats.binom.sf(math.floor(line), n, min(0.6, max(0.01, p["conversion"]))))

    result = {}
    for stat in dec.CHAINS:
        idx = [i for i, it in enumerate(items) if it["stat"] == stat]
        if not idx:
            continue
        rows = [chains[i] for i in idx]
        actual = np.array([items[i]["actual"] for i in idx])
        base_mean = np.array([dec.chain_prediction(r, frozenset()) for r in rows])
        cand_mean = np.array([preds[i]["mean"] for i in idx])
        base = vf.score(rows, lambda r, line: vf.production_over(r, line))
        cand = vf.score(rows, candidate_over)
        mixed = vf.score(rows, candidate_mean_prod_spread)
        result[stat] = {
            "n": len(idx),
            "n_dates": len({items[i]["game_date"] for i in idx}),
            "mae": {"baseline": float(np.mean(np.abs(base_mean - actual))), "candidate": float(np.mean(np.abs(cand_mean - actual)))},
            "bias": {"baseline": float(np.mean(actual - base_mean)), "candidate": float(np.mean(actual - cand_mean))},
            "synthetic": {
                "baseline": vf._public(base),
                "candidate_mean_production_spread": vf._public(mixed),
                "candidate": vf._public(cand),
            },
            "candidate_minus_baseline_brier": vf.date_clustered_diff(base, cand),
        }
    return result


def _git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def develop(logs: list[dict]) -> tuple[dict, dict]:
    items = build_items(logs, target_end=cm.TARGET_END)
    fit = [it for it in items if it["game_date"] < cm.FIT_END]
    select = [it for it in items if cm.FIT_END <= it["game_date"] < cm.SELECT_END]
    check = [it for it in items if cm.SELECT_END <= it["game_date"] < cm.TARGET_END]
    params, settings = fit_params(fit, select_items=select)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": VERSION,
        "splits": {"fit": f"2024-25 + 2025-10 (n={len(fit)})", "select": f"2025-11 (n={len(select)})", "check": f"2025-12 (n={len(check)})"},
        "settings": settings,
        "development_params": params,
        "check_december_2025": evaluate(check, params),
        "caveats": [
            "Baseline is the Model A chain: last-10 minutes, 20-game volume, shrunk conversion, production spread rules.",
            "Synthetic lines come from the baseline mean, so both models face identical lines. They are not sportsbook lines.",
            "No sportsbook price is an input to either model.",
        ],
    }
    return report, settings


def freeze(settings: dict) -> dict:
    """Refit the chosen settings on all of 2024-25 and 2025-26. Writes the frozen parameter file."""
    from engine.backtest.advanced_history import _load_windows, load_historical_logs

    logs, _ = load_historical_logs()
    extra, _ = _load_windows(EXTRA_2025_26_WINDOWS)
    logs = logs + extra
    items = build_items(logs, target_end=FREEZE_END)
    params, _ = fit_params(items, settings=settings)
    params.update({
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frozen_code_commit": _git_head(),
        "trained_on": "2024-25 and 2025-26 regular seasons (through 2026-04-13)",
        "train_rows": len(items),
        "holdout": "2026-27 season, from 2026-10-01. Score with engine/candidate/holdout.py; do not refit.",
        "settings": settings,
    })
    PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(json.dumps(params, indent=1), encoding="utf-8")
    return params


def load_params(path: Path = PARAMS_PATH) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _print(report: dict) -> None:
    print("Settings:", json.dumps(report["settings"], indent=1))
    print(f"\nDecember 2025 check ({VERSION} vs Model A chain)")
    for stat, e in report["check_december_2025"].items():
        s = e["synthetic"]
        d = e["candidate_minus_baseline_brier"]
        print(
            f"  {stat:<9} MAE {e['mae']['baseline']:.3f} -> {e['mae']['candidate']:.3f} | "
            f"Brier {s['baseline']['brier']:.4f} -> mean-only {s['candidate_mean_production_spread']['brier']:.4f} -> full {s['candidate']['brier']:.4f} "
            f"(diff {d['brier_diff']:+.4f} [{d['ci95'][0]:+.4f}, {d['ci95'][1]:+.4f}]) | "
            f"ECE {s['baseline']['ece']:.3f} -> {s['candidate']['ece']:.3f}"
        )


def main() -> None:
    from engine.backtest.advanced_history import load_historical_logs

    logs, _ = load_historical_logs()
    report, settings = develop(logs)
    DEV_REPORT_PATH.write_text(json.dumps(report, indent=1), encoding="utf-8")
    _print(report)
    params = freeze(settings)
    print(f"\nFrozen {VERSION} on {params['train_rows']} rows at commit {params['frozen_code_commit']}")
    print(f"Saved {DEV_REPORT_PATH}\nSaved {PARAMS_PATH}")


if __name__ == "__main__":
    main()
