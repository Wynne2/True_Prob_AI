"""
How much the candidate should move the market price, per stat.

    logit(p) = logit(no-vig market) + w * clip(logit(candidate) - logit(market), -c, c)

w = 1 with no clip is the pure candidate; w = 0 is the market. Same form as
engine/market_blend.py, fitted for the candidate instead of the old model.

Fit: January-February 2026 sportsbook lines saved in data/backtests (no new
credits). Candidate probabilities there come from the DEVELOPMENT parameters
(trained through October 2025), so every fitted line is out of sample for the
model. w and c are chosen by leave-one-date-out Brier; a stat whose
cross-validated blend does not beat the market gets w = 0.

Check: the 2026-27 holdout lines (frozen parameters), scored once.

Run: python -m engine.candidate.blend
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from engine import market_blend_fit as mbf
from engine.candidate import model as cmodel

BLEND_PATH = cmodel.ROOT / "data" / "models" / "candidate_blend.json"
SOURCES = (
    cmodel.ROOT / "data" / "backtests" / "expanded_report.json",
    cmodel.ROOT / "data" / "backtests" / "interaction_experiment.json",
)
HOLDOUT_PATH = cmodel.ROOT / "data" / "backtests" / "candidate_v1_holdout.json"
WEIGHT_GRID = tuple(round(x * 0.05, 2) for x in range(0, 21))   # 0 ... 1.0
STATS = ("points", "rebounds", "assists", "threes")


def priced_rows() -> list[dict]:
    """Saved Jan-Feb 2026 over lines: one per (game, player, prop, line), graded, with no-vig price."""
    seen, rows = set(), []
    for src in SOURCES:
        if not src.exists():
            continue
        for r in json.loads(src.read_text(encoding="utf-8")).get("rows") or []:
            if r.get("variant") != mbf.TRAIN_VARIANT or str(r.get("side")).lower() != "over":
                continue
            if r.get("outcome") is None or r.get("novig_probability") is None:
                continue
            key = (r.get("game_id"), r.get("player_id"), r.get("prop_type"), r.get("line"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
    return rows


def candidate_probability(pred: dict, line: float) -> float:
    """P(over) given the line was not a push, matching how graded rows exclude pushes."""
    over = cmodel.over_probability(pred, line)
    push = cmodel.push_probability(pred, line)
    return min(0.999, max(0.001, over / max(1.0 - push, 1e-6)))


def attach_development_candidate(rows: list[dict]) -> list[dict]:
    from engine.backtest.advanced_history import load_historical_logs

    dev = json.loads((cmodel.ROOT / "data" / "backtests" / "candidate_v1_development.json").read_text(encoding="utf-8"))
    logs, _ = load_historical_logs()
    # Same frozen settings, refit on everything before January 2026, so the
    # model is as fresh for Jan-Feb as the live model is for tonight.
    train = cmodel.build_items(logs, target_end="2026-01-01")
    params, _ = cmodel.fit_params(train, settings=dev["settings"])
    dates = sorted({r["game_date"] for r in rows})
    end = (datetime.fromisoformat(dates[-1]).date().toordinal() + 1)
    items = cmodel.build_items(logs, target_end=datetime.fromordinal(end).date().isoformat(), emit_from=dates[0])
    preds = cmodel.predict(items, params)
    by_key = {(it["game_date"], str(it["player_id"]), it["stat"]): p for it, p in zip(items, preds)}
    out = []
    for r in rows:
        pred = by_key.get((r["game_date"], str(r["player_id"]), str(r["prop_type"]).lower()))
        if pred is None:
            continue
        out.append({
            "date": r["game_date"],
            "prop": str(r["prop_type"]).lower(),
            "market": float(r["novig_probability"]),
            "model": candidate_probability(pred, float(r["line"])),
            "old_model": float(r["raw_probability"]),
            "y": 1.0 if r["outcome"] in (1, True, "1", "win") else 0.0,
        })
    return out


def fit(rows: list[dict]) -> dict:
    original = mbf.WEIGHT_GRID
    mbf.WEIGHT_GRID = WEIGHT_GRID
    try:
        by_prop = defaultdict(list)
        for r in rows:
            by_prop[r["prop"]].append(r)
        weights, metrics = {}, {}
        for prop in STATS:
            group = by_prop.get(prop) or []
            if len(group) < mbf.MIN_ROWS_PER_PROP:
                weights[prop] = {"weight": 0.0, "clip": 0.25}
                metrics[prop] = {"n": len(group), "note": "too few rows; market only"}
                continue
            res = mbf.fit_prop(group)
            weights[prop] = {"weight": res["weight"], "clip": res["clip"]}
            old = np.array([g["old_model"] for g in group])
            y = np.array([g["y"] for g in group])
            res["old_model_brier"] = round(float(np.mean((old - y) ** 2)), 6)
            metrics[prop] = res
    finally:
        mbf.WEIGHT_GRID = original
    return {"weights": weights, "metrics": metrics}


def _blend(model, market, w, c):
    lm = np.log(np.clip(market, 1e-4, 1 - 1e-4) / (1 - np.clip(market, 1e-4, 1 - 1e-4)))
    lc = np.log(np.clip(model, 1e-4, 1 - 1e-4) / (1 - np.clip(model, 1e-4, 1 - 1e-4)))
    return 1 / (1 + np.exp(-(lm + w * np.clip(lc - lm, -c, c))))


def check_holdout(weights: dict) -> dict:
    """Score the fitted weights once on the 2026-27 holdout lines (frozen parameters)."""
    if not HOLDOUT_PATH.exists():
        return {"status": "no_holdout"}
    rows = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))["sportsbook_check"].get("rows") or []
    out = {}
    groups = defaultdict(list)
    for r in rows:
        groups[str(r["prop_type"]).lower()].append(r)
    groups["all"] = rows
    for prop, bucket in groups.items():
        y = np.array([float(r["outcome"]) for r in bucket])
        market = np.array([r["novig_probability"] for r in bucket])
        cand = np.array([r["cand_probability"] for r in bucket])
        old = np.array([r["raw_probability"] for r in bucket])
        if prop == "all":
            blended = np.concatenate([
                _blend(np.array([r["cand_probability"] for r in groups[p]]),
                       np.array([r["novig_probability"] for r in groups[p]]),
                       weights[p]["weight"], weights[p]["clip"]) for p in STATS if p in groups
            ])
            y = np.concatenate([np.array([float(r["outcome"]) for r in groups[p]]) for p in STATS if p in groups])
            market = np.concatenate([np.array([r["novig_probability"] for r in groups[p]]) for p in STATS if p in groups])
            cand = np.concatenate([np.array([r["cand_probability"] for r in groups[p]]) for p in STATS if p in groups])
            old = np.concatenate([np.array([r["raw_probability"] for r in groups[p]]) for p in STATS if p in groups])
        else:
            blended = _blend(cand, market, weights[prop]["weight"], weights[prop]["clip"])
        brier = lambda p: round(float(np.mean((p - y) ** 2)), 5)
        out[prop] = {
            "n": int(len(y)),
            "market": brier(market),
            "candidate": brier(cand),
            "candidate_blend": brier(blended),
            "production_model_a": brier(old),
        }
    return out


def run() -> dict:
    rows = attach_development_candidate(priced_rows())
    fitted = fit(rows)
    result = {
        "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": cmodel.VERSION,
        "method": "logit(p) = logit(no-vig) + w * clip(logit(candidate) - logit(no-vig), -c, c); "
                  "w in 0..1 and c chosen by Brier, leave-one-date-out; w = 0 unless CV beats the market",
        "fit_window": "Jan-Feb 2026 saved sportsbook lines; candidate refit with frozen settings on games before 2026-01-01",
        "n_rows": len(rows),
        "n_dates": len({r["date"] for r in rows}),
        **fitted,
        "holdout_check_2026_27": check_holdout(fitted["weights"]),
    }
    BLEND_PATH.parent.mkdir(parents=True, exist_ok=True)
    BLEND_PATH.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def load_weights(path: Path = BLEND_PATH) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))["weights"]
    except (OSError, ValueError, KeyError):
        return None


def main() -> None:
    r = run()
    print(f"Fit on {r['n_rows']} lines over {r['n_dates']} dates")
    for prop, m in r["metrics"].items():
        if "market_brier" not in m:
            print(f"  {prop:<9} {m}")
            continue
        print(f"  {prop:<9} w={m['weight']:.2f} c={m['clip']:<5} n={m['n']:<5} market {m['market_brier']:.4f} "
              f"candidate {m['model_brier']:.4f} old {m['old_model_brier']:.4f} CV blend {m['cv_blend_brier']:.4f} "
              f"(vs market {m['cv_delta_vs_market']:+.4f} +/- {m['cv_delta_date_se']}, {m['dates_improved']}/{m['n_dates']} dates better)")
    print("\n2026-27 holdout (Brier):")
    for prop, m in r["holdout_check_2026_27"].items():
        print(f"  {prop:<9} {m}")
    print(f"\nSaved {BLEND_PATH}")


if __name__ == "__main__":
    main()
