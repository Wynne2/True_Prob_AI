"""
Head-to-head on real sportsbook lines: is v2 better than v1, and where?

Set A (clean): Jan-Feb 2026 lines saved in data/backtests. Every model is refit
with its frozen SETTINGS on games before 2026-01-01, so all lines are out of
sample. v2's calibration is fitted leave-one-date-out (each date is calibrated
by a fit on the other dates), so it is out of sample too.

Set B (biased, for reference): the 2026-27 Oct 29-Nov 2 holdout lines, scored
with the frozen files. v2's calibration was motivated by this window.

Models
    v1        candidate v1 (clean minutes, no calibration)
    v2_raw    v2 minutes (spread + absences), no calibration
    v2        v2_raw + per-stat Platt calibration (what the board uses)
    market    no-vig sportsbook probability
    book      raw implied probability of the offered price (vig included)

Accuracy: over side, every line. Bets: both sides of each main line plus
alternate overs; a bet is placed when model chance - book implied >= edge,
flat 1 unit, graded at the offered odds.

Run: python -m engine.candidate.compare
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from engine.backtest_grade import realized_roi
from engine.candidate import blend as cb
from engine.candidate import model as cmodel
from engine.candidate import v2 as cv2

REPORT_PATH = cmodel.ROOT / "data" / "backtests" / "candidate_compare.json"
BANDS = ((0.0, 0.25), (0.25, 0.40), (0.40, 0.60), (0.60, 0.75), (0.75, 1.01))
EDGES = (0.0, 0.03, 0.06, 0.10)


def saved_lines() -> list[dict]:
    seen, rows = set(), []
    for src in cb.SOURCES:
        for r in json.loads(Path(src).read_text(encoding="utf-8")).get("rows") or []:
            if r.get("variant") != "A_opportunity" or r.get("outcome") is None or r.get("novig_probability") is None:
                continue
            key = (r.get("game_id"), r.get("player_id"), r.get("prop_type"), r.get("line"), r.get("side"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
    return rows


def _plain(params):
    return dict(params, calibration={})


def side_probability(pred: dict, line: float, side: str) -> float:
    """Chance this side wins given no push (graded rows exclude pushes)."""
    over = cmodel.over_probability(pred, line)
    push = cmodel.push_probability(pred, line)
    p_over = over / max(1.0 - push, 1e-6)
    p = p_over if side == "over" else 1.0 - p_over
    return min(0.999, max(0.001, p))


def _platt(p, a, b):
    lp = math.log(p / (1 - p))
    return 1 / (1 + math.exp(-(a + b * lp)))


def attach(rows: list[dict], name: str, params: dict, items: list[dict]) -> None:
    preds = cmodel.predict(items, _plain(params))
    by_key = {(it["game_date"], str(it["player_id"]), it["stat"]): p for it, p in zip(items, preds)}
    for r in rows:
        pred = by_key.get((r["game_date"], str(r["player_id"]), str(r["prop_type"]).lower()))
        r[name] = None if pred is None else side_probability(pred, float(r["line"]), r["side"])


def cv_calibrate(rows: list[dict], raw_key: str, out_key: str) -> dict:
    """Leave-one-date-out Platt per stat, fitted on over rows, applied to both sides."""
    fits = {}
    by_stat = defaultdict(list)
    for r in rows:
        if r.get(raw_key) is not None:
            by_stat[str(r["prop_type"]).lower()].append(r)
    for stat, group in by_stat.items():
        overs = [g for g in group if g["side"] == "over"]
        dates = sorted({g["game_date"] for g in group})
        for d in dates:
            train = [g for g in overs if g["game_date"] != d]
            p = np.clip(np.array([g[raw_key] for g in train]), 1e-3, 1 - 1e-3)
            y = np.array([float(g["outcome"]) for g in train])
            a, b = cv2._best_ab(p, y)
            for g in group:
                if g["game_date"] != d:
                    continue
                p_over = g[raw_key] if g["side"] == "over" else 1 - g[raw_key]
                cal = _platt(min(max(p_over, 1e-3), 1 - 1e-3), a, b)
                g[out_key] = cal if g["side"] == "over" else 1 - cal
        p = np.clip(np.array([g[raw_key] for g in overs]), 1e-3, 1 - 1e-3)
        fits[stat] = cv2._best_ab(p, np.array([float(g["outcome"]) for g in overs]))
    return fits


def _brier(rows, key):
    v = [(float(r[key]) - float(r["outcome"])) ** 2 for r in rows if r.get(key) is not None]
    return round(statistics.fmean(v), 5) if v else None


def _logloss(rows, key):
    v = []
    for r in rows:
        if r.get(key) is None:
            continue
        p = min(max(float(r[key]), 1e-4), 1 - 1e-4)
        y = float(r["outcome"])
        v.append(-(y * math.log(p) + (1 - y) * math.log(1 - p)))
    return round(statistics.fmean(v), 5) if v else None


def accuracy(rows, models) -> dict:
    overs = [r for r in rows if r["side"] == "over" and all(r.get(m) is not None for m in models)]
    out = {"n": len(overs), "all": {m: {"brier": _brier(overs, m), "log_loss": _logloss(overs, m)} for m in models}}
    by_stat = defaultdict(list)
    for r in overs:
        by_stat[str(r["prop_type"]).lower()].append(r)
    out["by_stat"] = {s: {"n": len(g), **{m: _brier(g, m) for m in models}} for s, g in sorted(by_stat.items())}
    out["by_price_band"] = {}
    for lo, hi in BANDS:
        g = [r for r in overs if lo <= float(r["implied_probability"]) < hi]
        if g:
            out["by_price_band"][f"{lo:.0%}-{min(hi, 1):.0%}"] = {
                "n": len(g), "actual": round(statistics.fmean(float(r["outcome"]) for r in g), 4),
                **{m: {"brier": _brier(g, m), "avg": round(statistics.fmean(float(r[m]) for r in g), 4)} for m in models},
            }
    return out


def bets(rows, model, lo=0.25, hi=0.75) -> dict:
    """Flat bets where model chance - book implied clears each edge threshold, inside the tested price range."""
    out = {}
    usable = [r for r in rows if r.get(model) is not None and lo <= float(r["implied_probability"]) <= hi]
    for e in EDGES:
        picks = [r for r in usable if float(r[model]) - float(r["implied_probability"]) >= e]
        if not picks:
            out[f">={e:.0%}"] = {"n": 0}
            continue
        rois = [realized_roi(None, int(r["american_odds"]), "win" if r["outcome"] == 1 else "loss") for r in picks]
        by_date = defaultdict(list)
        for r, roi in zip(picks, rois):
            by_date[r["game_date"]].append(roi)
        means = [statistics.fmean(v) for v in by_date.values()]
        out[f">={e:.0%}"] = {
            "n": len(picks), "dates": len(by_date),
            "hit_rate": round(statistics.fmean(float(r["outcome"]) for r in picks), 4),
            "avg_model": round(statistics.fmean(float(r[model]) for r in picks), 4),
            "avg_implied": round(statistics.fmean(float(r["implied_probability"]) for r in picks), 4),
            "roi_per_bet": round(statistics.fmean(rois), 4),
            "roi_date_se": round(statistics.stdev(means) / len(means) ** 0.5, 4) if len(means) > 1 else None,
        }
    return out


def run() -> dict:
    from engine.backtest.advanced_history import load_historical_logs

    rows = saved_lines()
    logs, _ = load_historical_logs()
    spreads = cv2.spreads_for(logs)
    items = cmodel.build_items(logs, target_end="2026-03-01", spreads=spreads)
    train = [it for it in items if it["game_date"] < "2026-01-01"]
    test = [it for it in items if it["game_date"] >= "2026-01-01"]

    v1_settings = cmodel.load_params()["settings"]
    v2_settings = json.loads(cv2.DEV_REPORT_PATH.read_text(encoding="utf-8"))["settings"]
    v1, _ = cmodel.fit_params(train, settings=v1_settings)
    v2, _ = cmodel.fit_params(train, settings={k: v for k, v in v2_settings.items() if k != "calibration"})
    # v2 as intended: v1's volume settings, v2's minutes model (spread + absences).
    fixed_settings = json.loads(json.dumps(v1_settings))
    fixed_settings["minutes_features"] = v2_settings["minutes_features"]
    fixed_settings["minutes_lambda"] = v2_settings["minutes_lambda"]
    fixed, _ = cmodel.fit_params(train, settings=fixed_settings)
    attach(rows, "v1", v1, test)
    attach(rows, "v2_raw", v2, test)
    attach(rows, "fixed_raw", fixed, test)
    cv_fits = cv_calibrate(rows, "v2_raw", "v2")
    cv_calibrate(rows, "v1", "v1_cal")
    cv_calibrate(rows, "fixed_raw", "fixed_cal")
    for r in rows:
        r["market"] = float(r["novig_probability"])
        r["book"] = float(r["implied_probability"])
    scored = [r for r in rows if r.get("v1") is not None and r.get("v2") is not None and r.get("fixed_raw") is not None]
    models = ("v1", "v1_cal", "v2_raw", "v2", "fixed_raw", "fixed_cal", "market", "book")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "set_a": {
            "window": "Jan-Feb 2026 saved sportsbook lines; models fit on games before 2026-01-01",
            "lines": len(scored),
            "accuracy_over_side": accuracy(scored, models),
            "bets_25_to_75_implied": {m: bets(scored, m) for m in ("v1", "v1_cal", "v2_raw", "v2", "fixed_raw", "fixed_cal")},
            "fixed_settings": fixed_settings,
            "full_sample_calibration": {k: {"a": v[0], "b": v[1]} for k, v in cv_fits.items()},
        },
        "notes": [
            "Calibrated columns are leave-one-date-out; the frozen v2 calibration was fitted on all of Jan-Feb.",
            "Alternate lines are overs only; main lines have both sides.",
            "Lines for one player-game are correlated; read bet counts as rough.",
        ],
    }
    REPORT_PATH.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def _print(report: dict) -> None:
    a = report["set_a"]
    acc = a["accuracy_over_side"]
    print(f"Set A: {a['lines']} lines; over-side accuracy on {acc['n']} lines")
    for m, s in acc["all"].items():
        print(f"  {m:<8} Brier {s['brier']}  log loss {s['log_loss']}")
    print("\nBrier by stat:")
    for stat, s in acc["by_stat"].items():
        print(f"  {stat:<9} n={s['n']:<5} " + "  ".join(f"{m} {s[m]}" for m in acc["all"]))
    print("\nBy book price band (avg predicted vs actual, Brier):")
    for band, s in acc["by_price_band"].items():
        print(f"  {band:<8} n={s['n']:<5} actual {s['actual']:.3f} | " +
              "  ".join(f"{m} {s[m]['avg']:.3f}/{s[m]['brier']:.4f}" for m in ("v1", "v2", "fixed_raw", "fixed_cal", "market")))
    print("\nFlat bets, book implied 25-75%:")
    for m, bucket in a["bets_25_to_75_implied"].items():
        for e, s in bucket.items():
            if s["n"]:
                print(f"  {m:<7} edge {e:<5} n={s['n']:<5} hit {s['hit_rate']:.3f} model {s['avg_model']:.3f} "
                      f"implied {s['avg_implied']:.3f} ROI {s['roi_per_bet']:+.3f} (date SE {s['roi_date_se']})")
    print("\nCalibration fitted on all Jan-Feb overs (a, b):", a["full_sample_calibration"])


def main() -> None:
    _print(run())
    print(f"\nSaved {REPORT_PATH}")


if __name__ == "__main__":
    main()
