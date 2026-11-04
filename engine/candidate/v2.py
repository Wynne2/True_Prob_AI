"""
Candidate v2: v1 plus blowout risk, teammate absences, and a calibration test.

Changes tested against v1 (each kept only if it helps on data it was not fit on):
    blowout    pregame spread size, and spread x starter, in the minutes model
    absences   rotation teammates missing tonight, in the minutes model.
               Historical absences come from the box score (an upper bound);
               live, they come from BALLDONTLIE injury reports (Out / Doubtful).
    calibration  P(over) recalibrated per stat, logit(p') = a + b*logit(p), fitted on
               saved Jan-Feb 2026 sportsbook lines with leave-one-date-out CV;
               kept for a stat only if CV Brier beats the uncalibrated candidate.
               No sportsbook price is an input; the lines only supply outcomes.

Splits (development data only; nothing from 2026-27):
    fit     2024-25 + Oct-Dec 2025   (spreads exist from 2025-10-21)
    select  January 2026             (minutes feature set and penalties)
    check   February 2026            (reported once)

Freeze: chosen settings refit on all of 2024-25 and 2025-26 -> data/models/candidate_v2.json.
Holdout: 2026-27 games from the freeze date on (v1's holdout window is not reused).

Run: python -m engine.candidate.v2
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from engine.candidate import blend as cblend
from engine.candidate import minutes as cm
from engine.candidate import model as cmodel
from engine.candidate import spreads as csp

VERSION = "candidate-v2"
PARAMS_PATH = cmodel.ROOT / "data" / "models" / "candidate_v2.json"
DEV_REPORT_PATH = cmodel.ROOT / "data" / "backtests" / "candidate_v2_development.json"
FIT_END, SELECT_END, CHECK_END = "2026-01-01", "2026-02-01", "2026-03-01"
SPREAD_START, SPREAD_END = date(2025, 10, 21), date(2026, 2, 28)

MINUTE_SETS = {
    "v1_clean": cm.CLEAN_FEATURES,
    "plus_spread": cm.CLEAN_FEATURES + cm.SPREAD_FEATURES,
    "plus_spread_absence": cm.CLEAN_FEATURES + cm.SPREAD_FEATURES + cm.ABSENCE_FEATURES,
}
CAL_A = tuple(x / 20 for x in range(-6, 7))       # -0.30 .. 0.30
CAL_B = tuple(x / 20 for x in range(12, 23))      # 0.60 .. 1.10


def spreads_for(logs: list[dict]) -> dict:
    days = csp.game_dates(logs, SPREAD_START, SPREAD_END)
    return csp.spread_by_team_date(days, logs)


def _minutes_mae(items, params) -> float:
    rows = cmodel._unique_minutes(items)
    model = cmodel.ridge_from_dict(params["minutes"], cm.Ridge)
    return cm._mae(model.predict(rows), rows)


# ---------------------------------------------------------------------------
# Calibration on priced lines
# ---------------------------------------------------------------------------

def _platt(p, a, b):
    lp = np.log(p / (1 - p))
    return 1 / (1 + np.exp(-(a + b * lp)))


def _best_ab(p, y):
    best = (math.inf, 0.0, 1.0)
    for a in CAL_A:
        for b in CAL_B:
            s = float(np.mean((_platt(p, a, b) - y) ** 2))
            if s < best[0]:
                best = (s, a, b)
    return best[1], best[2]


def fit_calibration(rows: list[dict]) -> dict:
    """Per stat: leave-one-date-out Platt. Kept only if CV Brier beats uncalibrated."""
    out = {}
    by_prop = defaultdict(list)
    for r in rows:
        by_prop[r["prop"]].append(r)
    for prop in cblend.STATS:
        group = by_prop.get(prop) or []
        if len(group) < 200:
            out[prop] = {"kept": False, "reason": "too few rows", "n": len(group)}
            continue
        p = np.clip(np.array([g["model"] for g in group]), 1e-3, 1 - 1e-3)
        y = np.array([g["y"] for g in group])
        dates = np.array([g["date"] for g in group])
        cv = np.empty_like(p)
        for d in sorted(set(dates)):
            test = dates == d
            a, b = _best_ab(p[~test], y[~test])
            cv[test] = _platt(p[test], a, b)
        raw_brier = float(np.mean((p - y) ** 2))
        cv_brier = float(np.mean((cv - y) ** 2))
        a, b = _best_ab(p, y)
        hi = (p >= 0.6) & (p < 0.8)
        out[prop] = {
            "kept": cv_brier < raw_brier,
            "a": a, "b": b, "n": int(len(y)), "n_dates": int(len(set(dates))),
            "raw_brier": round(raw_brier, 6), "cv_calibrated_brier": round(cv_brier, 6),
            "over_60_80": {
                "n": int(hi.sum()),
                "predicted": round(float(p[hi].mean()), 4) if hi.any() else None,
                "calibrated": round(float(cv[hi].mean()), 4) if hi.any() else None,
                "actual": round(float(y[hi].mean()), 4) if hi.any() else None,
            },
        }
    return out


def priced_rows_for(params: dict, items: list[dict]) -> list[dict]:
    """Saved Jan-Feb sportsbook over lines with the candidate's (uncalibrated) probability."""
    plain = dict(params, calibration={})
    preds = cmodel.predict(items, plain)
    by_key = {(it["game_date"], str(it["player_id"]), it["stat"]): p for it, p in zip(items, preds)}
    out = []
    for r in cblend.priced_rows():
        pred = by_key.get((r["game_date"], str(r["player_id"]), str(r["prop_type"]).lower()))
        if pred is None:
            continue
        out.append({
            "date": r["game_date"], "prop": str(r["prop_type"]).lower(),
            "model": cblend.candidate_probability(pred, float(r["line"])),
            "market": float(r["novig_probability"]),
            "y": 1.0 if r["outcome"] in (1, True, "1", "win") else 0.0,
        })
    return out


# ---------------------------------------------------------------------------
# Develop, freeze
# ---------------------------------------------------------------------------

def develop() -> tuple[dict, dict]:
    from engine.backtest.advanced_history import load_historical_logs

    logs, _ = load_historical_logs()
    spreads = spreads_for(logs)
    items = cmodel.build_items(logs, target_end=CHECK_END, spreads=spreads)
    fit = [it for it in items if it["game_date"] < FIT_END]
    select = [it for it in items if FIT_END <= it["game_date"] < SELECT_END]
    check = [it for it in items if SELECT_END <= it["game_date"] < CHECK_END]

    variants = {}
    for name, features in MINUTE_SETS.items():
        params, settings = cmodel.fit_params(fit, settings={"minutes_features": list(features)}, select_items=select)
        variants[name] = {
            "params": params, "settings": settings,
            "jan_minutes_mae": _minutes_mae(select, params),
            "feb_minutes_mae": _minutes_mae(check, params),
        }
    chosen = min(variants, key=lambda k: variants[k]["jan_minutes_mae"])
    params, settings = variants[chosen]["params"], variants[chosen]["settings"]

    # Calibration: priced Jan-Feb lines, candidate fit through December.
    calibration = fit_calibration(priced_rows_for(params, select + check))
    kept = {k: {"a": v["a"], "b": v["b"]} for k, v in calibration.items() if v.get("kept")}
    settings["calibration"] = kept
    params_cal = dict(params, calibration=kept)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": VERSION,
        "splits": {"fit": f"2024-25 + Oct-Dec 2025 (n={len(fit)})", "select": f"2026-01 (n={len(select)})", "check": f"2026-02 (n={len(check)})"},
        "spreads": {"games_with_spread": len(spreads) // 2, "window": f"{SPREAD_START} .. {SPREAD_END}"},
        "minutes_variants": {k: {"jan_minutes_mae": v["jan_minutes_mae"], "feb_minutes_mae": v["feb_minutes_mae"],
                                 "lambda": v["settings"]["minutes_lambda"]} for k, v in variants.items()},
        "chosen_minutes": chosen,
        "minutes_coefficients": cmodel.ridge_from_dict(params["minutes"], cm.Ridge).describe(),
        "calibration": calibration,
        "check_feb_2026": {
            "v1_settings": cmodel.evaluate(check, variants["v1_clean"]["params"]),
            "v2": cmodel.evaluate(check, params_cal),
        },
        "caveats": [
            "Absences in history are inferred from who missed the box score (upper bound); live they come from injury reports.",
            "Calibration is fitted on sportsbook lines' outcomes only; no price enters the candidate probability.",
            "Feb priced lines are used inside the calibration's leave-one-date-out CV, so the Feb synthetic check is the clean one for the model change.",
            "2024-25 has no spreads; the spread features are zero with has_spread = 0 there.",
        ],
    }
    return report, settings


def freeze(settings: dict) -> dict:
    from engine.backtest.advanced_history import _load_windows, load_historical_logs

    logs, _ = load_historical_logs()
    extra, _ = _load_windows(cmodel.EXTRA_2025_26_WINDOWS)
    logs = logs + extra
    spreads = spreads_for(logs)
    items = cmodel.build_items(logs, target_end=cmodel.FREEZE_END, spreads=spreads)
    params, _ = cmodel.fit_params(items, settings=settings)
    params.update({
        "version": VERSION,
        "calibration": settings.get("calibration") or {},
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frozen_code_commit": cmodel._git_head(),
        "trained_on": "2024-25 and 2025-26 regular seasons (through 2026-04-13); spreads 2025-10-21..2026-02-28",
        "train_rows": len(items),
        "holdout": f"2026-27 season games from {date.today().isoformat()} on. v1's Oct 29-Nov 2 window is not reused.",
        "holdout_start": date.today().isoformat(),
        "settings": settings,
    })
    PARAMS_PATH.write_text(json.dumps(params, indent=1), encoding="utf-8")
    return params


def _print(report: dict) -> None:
    print("Minutes variants (MAE):")
    for k, v in report["minutes_variants"].items():
        mark = " *" if k == report["chosen_minutes"] else ""
        print(f"  {k:<22} Jan {v['jan_minutes_mae']:.3f}  Feb {v['feb_minutes_mae']:.3f}{mark}")
    print("Minutes coefficients (spread/absence):", {k: round(v, 3) for k, v in report["minutes_coefficients"].items()
                                                     if "spread" in k or "absent" in k})
    print("\nCalibration on priced Jan-Feb lines:")
    for k, v in report["calibration"].items():
        print(f"  {k:<9} kept={v.get('kept')} a={v.get('a')} b={v.get('b')} raw {v.get('raw_brier')} cv {v.get('cv_calibrated_brier')} 60-80%: {v.get('over_60_80')}")
    print("\nFebruary 2026 check (synthetic lines):")
    for stat in report["check_feb_2026"]["v2"]:
        a = report["check_feb_2026"]["v1_settings"][stat]
        b = report["check_feb_2026"]["v2"][stat]
        print(f"  {stat:<9} MAE v1 {a['mae']['candidate']:.3f} v2 {b['mae']['candidate']:.3f} | "
              f"Brier chain {a['synthetic']['baseline']['brier']:.4f} v1 {a['synthetic']['candidate']['brier']:.4f} "
              f"v2 {b['synthetic']['candidate']['brier']:.4f} | ECE v1 {a['synthetic']['candidate']['ece']:.3f} v2 {b['synthetic']['candidate']['ece']:.3f}")


def main() -> None:
    report, settings = develop()
    DEV_REPORT_PATH.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    _print(report)
    print(f"\nSaved {DEV_REPORT_PATH}")


if __name__ == "__main__":
    main()
