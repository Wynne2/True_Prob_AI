"""
Does the no-vig market price need a favourite/long-shot correction?

    logit(p_corrected) = b * logit(p_market)

b = 1 is the market as it stands. b > 1 says long shots are overpriced (their
no-vig chance is too high) and favourites underpriced; b < 1 the reverse. One
parameter, symmetric, so the two sides of a line still sum to 1.

Evidence is walk-forward only: for each graded date, b is fitted on every
earlier date and scored on that date. Nothing is fitted on the date it is
scored on. Rows are every graded line with a no-vig price:

    data/backtests/expanded_report.json     Jan-Feb 2026 sportsbook lines (both sides)
    data/predictions/*.graded.json          2026-27 live boards (both sides)

The result is saved to data/models/market_slope.json with the evidence and is
published on the Performance page. ``load_slope`` returns b only when the
walk-forward Brier improvement is more than two date-clustered standard errors,
otherwise 1. The board does not apply it yet: the blend weights were fitted
against the uncorrected price and would need refitting first.

Run: python -m engine.market_slope
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARAMS_PATH = ROOT / "data" / "models" / "market_slope.json"
GRID = tuple(round(0.70 + 0.01 * i, 2) for i in range(71))   # 0.70 ... 1.40
MIN_TRAIN_DATES = 3
_EPS = 1e-4


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1 - _EPS)
    return math.log(p / (1 - p))


def apply_slope(p: float, b: float) -> float:
    return 1 / (1 + math.exp(-b * _logit(p)))


def load_rows(root: Path = ROOT) -> list[dict]:
    """[{date, p, y, prop}] for every decided line with a no-vig price, one per (date, line, side)."""
    rows, seen = [], set()
    src = root / "data" / "backtests" / "expanded_report.json"
    if src.exists():
        for r in json.loads(src.read_text(encoding="utf-8")).get("rows") or []:
            if r.get("variant") != "A_opportunity" or r.get("outcome") is None or r.get("novig_probability") is None:
                continue
            key = (r["game_date"], r["player_id"], r["prop_type"], r["line"], r["side"])
            if key not in seen:
                seen.add(key)
                rows.append({"date": r["game_date"], "p": float(r["novig_probability"]),
                             "y": float(r["outcome"]), "prop": str(r["prop_type"]).lower()})
    for path in sorted((root / "data" / "predictions").glob("????-??-??.graded.json")):
        day = path.name[:10]
        for r in json.loads(path.read_text(encoding="utf-8")).get("rows") or []:
            if r.get("result") not in ("win", "loss") or r.get("market_probability") is None:
                continue
            key = (day, r.get("key"))
            if key not in seen:
                seen.add(key)
                rows.append({"date": day, "p": float(r["market_probability"]),
                             "y": 1.0 if r["result"] == "win" else 0.0,
                             "prop": str(r.get("prop", "")).split(" ")[0].lower()})
    return rows


def brier(rows: list[dict], b: float = 1.0) -> float:
    return statistics.fmean((apply_slope(r["p"], b) - r["y"]) ** 2 for r in rows)


def log_loss(rows: list[dict], b: float = 1.0) -> float:
    def one(r):
        q = min(max(apply_slope(r["p"], b), _EPS), 1 - _EPS)
        return -(r["y"] * math.log(q) + (1 - r["y"]) * math.log(1 - q))
    return statistics.fmean(one(r) for r in rows)


def fit_slope(rows: list[dict]) -> float:
    """Grid value with the lowest log loss (a proper score; the grid keeps it reproducible)."""
    return min(GRID, key=lambda b: (log_loss(rows, b), abs(b - 1)))


def walk_forward(rows: list[dict]) -> dict:
    dates = sorted({r["date"] for r in rows})
    by_date = {d: [r for r in rows if r["date"] == d] for d in dates}
    folds = []
    for i, d in enumerate(dates):
        if i < MIN_TRAIN_DATES:
            continue
        train = [r for prev in dates[:i] for r in by_date[prev]]
        b = fit_slope(train)
        test = by_date[d]
        folds.append({
            "date": d, "n": len(test), "train_rows": len(train), "slope": b,
            "brier_market": round(brier(test), 6), "brier_corrected": round(brier(test, b), 6),
            "logloss_market": round(log_loss(test), 6), "logloss_corrected": round(log_loss(test, b), 6),
        })
    deltas = [f["brier_corrected"] - f["brier_market"] for f in folds]
    n = sum(f["n"] for f in folds)
    mean = statistics.fmean(deltas) if deltas else 0.0
    se = statistics.stdev(deltas) / math.sqrt(len(deltas)) if len(deltas) > 1 else float("inf")
    pooled = lambda k: round(sum(f[k] * f["n"] for f in folds) / n, 6) if n else None
    return {
        "folds": folds,
        "test_rows": n,
        "test_dates": len(folds),
        "brier_market": pooled("brier_market"),
        "brier_corrected": pooled("brier_corrected"),
        "logloss_market": pooled("logloss_market"),
        "logloss_corrected": pooled("logloss_corrected"),
        "mean_date_brier_delta": round(mean, 6),
        "date_se": None if math.isinf(se) else round(se, 6),
        "dates_improved": sum(d < 0 for d in deltas),
    }


def calibration_table(rows: list[dict], bins: int = 10) -> list[dict]:
    """Predicted vs realised hit rate by market-probability decile (all rows, no fitting)."""
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        bucket = [r for r in rows if lo <= r["p"] < hi or (i == bins - 1 and r["p"] == 1.0)]
        if bucket:
            out.append({"bin": f"{lo:.0%}-{hi:.0%}", "n": len(bucket),
                        "predicted": round(statistics.fmean(r["p"] for r in bucket), 4),
                        "realized": round(statistics.fmean(r["y"] for r in bucket), 4)})
    return out


def run(root: Path = ROOT, save: bool = True) -> dict:
    rows = load_rows(root)
    wf = walk_forward(rows)
    final = fit_slope(rows) if rows else 1.0
    se = wf["date_se"]
    passed = bool(wf["folds"]) and se is not None and wf["mean_date_brier_delta"] < -2 * se
    result = {
        "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "logit(p) = b * logit(no-vig market); b fitted on earlier dates only, scored on the next date",
        "rows": len(rows),
        "dates": sorted({r["date"] for r in rows}),
        "walk_forward": wf,
        "slope_all_rows": final,
        "in_use": passed,
        "slope_in_use": final if passed else 1.0,
        "decision": (
            "Passes: walk-forward Brier beats the market by more than two date-clustered standard errors. "
            "Refit the blend weights against the corrected price before putting it on the board."
            if passed else
            "Not used (slope stays 1): the walk-forward improvement over the market is within two "
            "date-clustered standard errors, so it is not distinguishable from noise."
        ),
        "calibration_market": calibration_table(rows),
    }
    if save:
        PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PARAMS_PATH.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


BY_PROP_PATH = ROOT / "data" / "backtests" / "market_slope_by_prop.json"


def run_by_prop(root: Path = ROOT, save: bool = True) -> dict:
    """
    Offline experiment: the same walk-forward test run separately for each stat.

    Never read by the board. With one test per stat, one of them clearing two standard
    errors by luck is likely, so a pass here is a lead for a pre-registered retest on
    later dates, not grounds to apply it.
    """
    rows = load_rows(root)
    stats = {}
    for prop in sorted({r["prop"] for r in rows}):
        sub = [r for r in rows if r["prop"] == prop]
        wf = walk_forward(sub)
        se = wf["date_se"]
        stats[prop] = {
            "rows": len(sub), "dates": len({r["date"] for r in sub}), "slope_all_rows": fit_slope(sub),
            "test_rows": wf["test_rows"], "test_dates": wf["test_dates"],
            "brier_market": wf["brier_market"], "brier_corrected": wf["brier_corrected"],
            "mean_date_brier_delta": wf["mean_date_brier_delta"], "date_se": se, "dates_improved": wf["dates_improved"],
            "passes_two_se": bool(wf["folds"]) and se is not None and wf["mean_date_brier_delta"] < -2 * se,
        }
    result = {
        "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "engine.market_slope walk-forward, run separately per stat; offline only, not used by the board",
        "rows": len(rows),
        "dates": sorted({r["date"] for r in rows}),
        "tests": len(stats),
        "by_prop": stats,
        "passing": sorted(k for k, v in stats.items() if v["passes_two_se"]),
    }
    if save:
        BY_PROP_PATH.parent.mkdir(parents=True, exist_ok=True)
        BY_PROP_PATH.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def load_slope(path: Path = PARAMS_PATH) -> float:
    """The slope the engine should apply: 1.0 unless a saved fit passed its walk-forward test."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return float(data["slope_in_use"]) if data.get("in_use") else 1.0
    except (OSError, ValueError, KeyError):
        return 1.0


if __name__ == "__main__":
    import sys

    if "--by-prop" in sys.argv:
        r = run_by_prop()
        for prop, s in r["by_prop"].items():
            print(f"{prop:<9} rows {s['rows']:<5} test {s['test_rows']:<5} on {s['test_dates']} dates  slope {s['slope_all_rows']:.2f}  "
                  f"Brier {s['brier_market']} -> {s['brier_corrected']}  delta {s['mean_date_brier_delta']} +/- {s['date_se']}  "
                  f"{'PASSES' if s['passes_two_se'] else 'no'}")
        print(f"passing: {r['passing'] or 'none'} of {r['tests']} tests")
        sys.exit(0)
    r = run()
    wf = r["walk_forward"]
    print(f"{r['rows']} rows on {len(r['dates'])} dates; walk-forward on {wf['test_dates']} dates, {wf['test_rows']} rows")
    for f in wf["folds"]:
        print(f"  {f['date']} n={f['n']:<5} slope {f['slope']:.2f}  Brier market {f['brier_market']:.5f} corrected {f['brier_corrected']:.5f}")
    print(f"Brier market {wf['brier_market']} corrected {wf['brier_corrected']}; "
          f"mean date delta {wf['mean_date_brier_delta']} +/- {wf['date_se']} ({wf['dates_improved']}/{wf['test_dates']} dates better)")
    print(f"slope on all rows {r['slope_all_rows']}; {r['decision']}")
    for c in r["calibration_market"]:
        print(f"  {c['bin']:>9} n={c['n']:<5} predicted {c['predicted']:.3f} realized {c['realized']:.3f}")
