"""
Candidate v3: v2 as it was meant to be, without calibration.

    volume       v1's frozen settings per stat (v2's development run re-chose them
                 by mistake; threes moved to the 20-game ratio and got worse)
    minutes      v2's model: recency, rest, last season, own missed games,
                 pregame spread, and teammate absences (injury reports live)
    conversion   unchanged
    spread       variance refit around this mean (same formulas as v1/v2)
    calibration  none. On 8,911 Jan-Feb sportsbook lines the per-stat Platt step
                 improved Brier slightly but its edge bets lost 3-15% per bet,
                 because it pulls every chance toward 50% and so favors underdogs.

Evidence (engine/candidate/compare.py, Jan-Feb 2026 lines, models fit on games
before 2026-01-01): this configuration's Brier 0.2434 (v1 0.2444, v2 0.2426,
no-vig market 0.2397); edge bets >= 3% inside 25-75% implied: 0.0% ROI per bet
(v2 -3.8%). Those lines chose v3, so they are not its test.

Freeze: refit on all of 2024-25 and 2025-26 -> data/models/candidate_v3.json.
Holdout: 2026-27 games from 2026-11-05 (first full slate after the freeze).

Run: python -m engine.candidate.v3
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from engine.candidate import model as cmodel
from engine.candidate import v2 as cv2

VERSION = "candidate-v3"
PARAMS_PATH = cmodel.ROOT / "data" / "models" / "candidate_v3.json"
HOLDOUT_START = "2026-11-05"
COMPARE_PATH = cmodel.ROOT / "data" / "backtests" / "candidate_compare.json"


def settings() -> dict:
    v1 = cmodel.load_params()["settings"]
    v2 = json.loads(cv2.DEV_REPORT_PATH.read_text(encoding="utf-8"))["settings"]
    out = json.loads(json.dumps(v1))
    out["minutes_features"] = v2["minutes_features"]
    out["minutes_lambda"] = v2["minutes_lambda"]
    out["calibration"] = {}
    return out


def freeze() -> dict:
    from engine.backtest.advanced_history import _load_windows, load_historical_logs

    chosen = settings()
    logs, _ = load_historical_logs()
    extra, _ = _load_windows(cmodel.EXTRA_2025_26_WINDOWS)
    logs = logs + extra
    items = cmodel.build_items(logs, target_end=cmodel.FREEZE_END, spreads=cv2.spreads_for(logs))
    params, _ = cmodel.fit_params(items, settings=chosen)
    evidence = {}
    if COMPARE_PATH.exists():
        a = json.loads(COMPARE_PATH.read_text(encoding="utf-8"))["set_a"]
        evidence = {
            "brier": {k: a["accuracy_over_side"]["all"][k]["brier"] for k in ("v1", "v2", "fixed_raw", "market")},
            "bets_edge_3pct": {k: a["bets_25_to_75_implied"][k][">=3%"] for k in ("v1", "v2", "fixed_raw")},
            "note": "fixed_raw is this configuration. Jan-Feb 2026 lines chose v3; they are not its test.",
        }
    params.update({
        "version": VERSION,
        "calibration": {},
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frozen_code_commit": cmodel._git_head(),
        "trained_on": "2024-25 and 2025-26 regular seasons (through 2026-04-13); spreads 2025-10-21..2026-02-28",
        "train_rows": len(items),
        "holdout": f"2026-27 season games from {HOLDOUT_START} on. Earlier 2026-27 windows were used for v1/v2.",
        "holdout_start": HOLDOUT_START,
        "settings": chosen,
        "selection_evidence": evidence,
    })
    PARAMS_PATH.write_text(json.dumps(params, indent=1), encoding="utf-8")
    return params


def main() -> None:
    params = freeze()
    print(f"Frozen {VERSION} on {params['train_rows']} rows at commit {params['frozen_code_commit']}")
    print("Volume choices:", {s: v["choice"] for s, v in params["volume"].items()})
    print(f"Saved {PARAMS_PATH}")


if __name__ == "__main__":
    main()
