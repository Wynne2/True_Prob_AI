"""
Can matchup, usage, pace and opportunity together correct the market's price?

For each real sportsbook line (saved Jan-Feb 2026), the market's no-vig
probability is the starting point. A small logistic correction is learned
from point-in-time context:

    logit(p) = logit(no-vig) + b . x

    x (standardized), all from games before the line's date:
      model      v1's projection vs the line, in standard deviations
      matchup    opponent defensive-rating rank, rank vs the player's G/F/C
                 group for this stat, opponent volume allowed for this stat,
                 and weak-defense x usage / opportunity interactions
      pace       opponent pace vs league
      role       usage trend, touches trend, minutes trend
      spread     pregame spread size (blowout risk)

With b = 0 this is exactly the market. Every date is predicted by a fit that
never saw that date (leave-one-date-out), so the scores are out of sample.
Penalty: lambda = 100 is the headline, fixed before running; others shown.

Teammate absences are left out: in history they come from the box score,
which would leak.

Run: python -m engine.candidate.context_edges
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from engine.backtest_grade import realized_roi
from engine.candidate import compare as ccmp
from engine.candidate import matchups as mf
from engine.candidate import minutes as cm
from engine.candidate import model as cmodel
from engine.candidate import v2 as cv2
from engine.candidate import volume as cvol

REPORT_PATH = cmodel.ROOT / "data" / "backtests" / "context_edges.json"
HEADLINE_LAMBDA = 100.0
LAMBDAS = (10.0, 100.0, 1000.0)
FAV_BAND = (200 / 300, 600 / 700)        # -200 .. -600 implied
BANDS = (("-600 to -200", FAV_BAND[0], FAV_BAND[1]), ("-199 to +149", 0.40, FAV_BAND[0]),
         ("+150 to +299", 0.25, 0.40), ("+300 or longer", 0.0, 0.25))

GROUPS = {
    "model": ("z_model",),
    "matchup": ("weak_defense", "weak_vs_position", "allowed_gap", "weak_def_x_usage", "weak_pos_x_minutes"),
    "pace": ("pace_gap",),
    "role": ("usage_trend", "touches_trend", "minutes_trend"),
    "spread": ("abs_spread",),
}
FEATURES = tuple(f for g in GROUPS.values() for f in g)


def _logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def _weak(rank) -> float:
    """+1 for the worst-ranked defense, -1 for the best, 0 when unranked."""
    return 0.0 if rank is None else (15.5 - float(rank)) / 14.5


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

def build() -> list[dict]:
    from engine.backtest.advanced_history import load_historical_logs

    lines = ccmp.saved_lines()
    logs, _ = load_historical_logs()
    spreads = cv2.spreads_for(logs)
    items = cmodel.build_items(logs, target_end="2026-03-01", spreads=spreads)
    train = [it for it in items if it["game_date"] < "2026-01-01"]
    test = [it for it in items if it["game_date"] >= "2026-01-01"]
    v1, _ = cmodel.fit_params(train, settings=cmodel.load_params()["settings"])
    preds = cmodel.predict(test, dict(v1, calibration={}))
    pred_of = {(it["game_date"], str(it["player_id"]), it["stat"]): p for it, p in zip(test, preds)}
    vol_of = {(r["game_date"], r["player_id"], r["stat"]): r["features"]
              for r in cvol.build_rows(logs, target_end="2026-03-01", emit_from="2026-01-01")}
    min_of = {(r["game_date"], r["player_id"]): r["features"]
              for r in cm.build_rows(logs, target_end="2026-03-01", emit_from="2026-01-01", spreads=spreads)}
    mf.screen_lines(logs, lines, "2025-26")

    out = []
    for r in lines:
        stat = str(r["prop_type"]).lower()
        key = (r["game_date"], str(r["player_id"]), stat)
        pred, vol, mins, s = pred_of.get(key), vol_of.get(key), min_of.get((r["game_date"], str(r["player_id"]))), r.get("screen")
        if pred is None or vol is None or mins is None or not s or not s.get("averages"):
            continue
        avg = s["averages"]
        base = max(vol["base_rate"], 1e-6)
        usage = avg["usage"] / 100 if avg["usage"] > 1.5 else avg["usage"]
        weak_def, weak_pos = _weak(s["defense_rank"]), _weak(s["position_stat_rank"])
        x = {
            "z_model": (pred["mean"] - float(r["line"])) / math.sqrt(max(pred["variance"], 0.05)),
            "weak_defense": weak_def,
            "weak_vs_position": weak_pos,
            "allowed_gap": vol["allowed_gap_x_rate"] / base,
            "weak_def_x_usage": weak_def * (usage - 0.20) * 10,
            "weak_pos_x_minutes": weak_pos * (avg["minutes"] - 26) / 10,
            "pace_gap": vol["pace_gap_x_rate"] / base,
            "usage_trend": vol["usage_delta_x_rate"] / base,
            "touches_trend": vol["touches_delta_x_rate"] / base,
            "minutes_trend": (mins["ewm3"] - mins["last10"]) / 5,
            "abs_spread": mins.get("abs_spread", 0.0) / 10,
        }
        out.append({**r, "x": x})
    return out


# ---------------------------------------------------------------------------
# Logistic correction with the market as offset
# ---------------------------------------------------------------------------

def fit(offset, X, y, lam, iters=25):
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(offset + X @ beta)))
        w = p * (1 - p)
        grad = X.T @ (p - y) + lam * beta
        hess = (X * w[:, None]).T @ X + lam * np.eye(X.shape[1])
        step = np.linalg.solve(hess, grad)
        beta -= step
        if np.max(np.abs(step)) < 1e-7:
            break
    return beta


def lodo(rows: list[dict], features: tuple[str, ...], lam: float) -> np.ndarray:
    """Out-of-sample P(over) for every over row, each date predicted without itself."""
    dates = np.array([r["game_date"] for r in rows])
    X = np.array([[r["x"][f] for f in features] for r in rows]) if features else np.zeros((len(rows), 0))
    off = _logit(np.array([float(r["novig_probability"]) for r in rows]))
    y = np.array([float(r["outcome"]) for r in rows])
    pred = np.empty(len(rows))
    for d in sorted(set(dates)):
        test = dates == d
        if not features:
            pred[test] = 1 / (1 + np.exp(-off[test]))
            continue
        mu, sd = X[~test].mean(0), X[~test].std(0)
        sd[sd == 0] = 1
        b = fit(off[~test], (X[~test] - mu) / sd, y[~test], lam)
        pred[test] = 1 / (1 + np.exp(-(off[test] + ((X[test] - mu) / sd) @ b)))
    return pred


def _scores(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(np.mean((p - y) ** 2)), float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _date_se(rows, values):
    by = defaultdict(list)
    for r, v in zip(rows, values):
        by[r["game_date"]].append(v)
    means = [statistics.fmean(v) for v in by.values()]
    return statistics.stdev(means) / len(means) ** 0.5 if len(means) > 1 else None


def bets(all_rows, p_over_of, lo, hi, threshold):
    """Both sides: over rows use p_over; main-line unders use 1 - p_over of the same line."""
    picks = []
    for r in all_rows:
        key = (r["game_id"], r["player_id"], r["prop_type"], r["line"])
        po = p_over_of.get(key)
        if po is None:
            continue
        p = po if r["side"] == "over" else 1 - po
        implied = float(r["implied_probability"])
        if lo <= implied < hi and p - implied >= threshold:
            picks.append(r)
    if not picks:
        return {"n": 0}
    rois = [realized_roi(None, int(r["american_odds"]), "win" if r["outcome"] == 1 else "loss") for r in picks]
    se = _date_se(picks, rois)
    return {
        "n": len(picks), "dates": len({r["game_date"] for r in picks}),
        "hit_rate": round(statistics.fmean(float(r["outcome"]) for r in picks), 4),
        "avg_implied": round(statistics.fmean(float(r["implied_probability"]) for r in picks), 4),
        "roi_per_bet": round(statistics.fmean(rois), 4),
        "roi_date_se": None if se is None else round(se, 4),
        "units": round(sum(rois), 1),
    }


def run() -> dict:
    rows = build()
    overs = [r for r in rows if r["side"] == "over"]
    y = np.array([float(r["outcome"]) for r in overs])
    market = np.array([float(r["novig_probability"]) for r in overs])
    mk_brier, mk_ll = _scores(market, y)

    variants = {"all_factors": FEATURES, **{f"only_{g}": f for g, f in GROUPS.items()},
                **{f"all_but_{g}": tuple(x for x in FEATURES if x not in f) for g, f in GROUPS.items()}}
    results, headline_pred = {}, None
    for lam in LAMBDAS:
        for name, feats in variants.items():
            if lam != HEADLINE_LAMBDA and name != "all_factors":
                continue
            p = lodo(overs, feats, lam)
            b, ll = _scores(p, y)
            results[f"{name}@{lam:g}"] = {
                "brier": round(b, 5), "log_loss": round(ll, 5),
                "brier_vs_market": round(b - mk_brier, 5),
                "brier_vs_market_date_se": round(_date_se(overs, ((p - y) ** 2) - ((market - y) ** 2)), 5),
            }
            if name == "all_factors" and lam == HEADLINE_LAMBDA:
                headline_pred = p

    p_over_of = {(r["game_id"], r["player_id"], r["prop_type"], r["line"]): float(p) for r, p in zip(overs, headline_pred)}
    by_band = {}
    for name, lo, hi in BANDS:
        g = [i for i, r in enumerate(overs) if lo <= float(r["implied_probability"]) < hi]
        if g:
            bm, _ = _scores(market[g], y[g])
            bc, _ = _scores(headline_pred[g], y[g])
            by_band[name] = {"n_overs": len(g), "brier_market": round(bm, 5), "brier_corrected": round(bc, 5)}
    bet_results = {
        name: {f">={t:.0%}": bets(rows, p_over_of, lo, hi, t) for t in (0.0, 0.01, 0.02, 0.03)}
        for name, lo, hi in BANDS
    }
    X = np.array([[r["x"][f] for f in FEATURES] for r in overs])
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1
    beta = fit(_logit(market), (X - mu) / sd, y, HEADLINE_LAMBDA)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lines": len(rows), "over_lines": len(overs), "dates": len({r["game_date"] for r in overs}),
        "market_brier": round(mk_brier, 5), "market_log_loss": round(mk_ll, 5),
        "variants": results,
        "headline": f"all_factors@{HEADLINE_LAMBDA:g}",
        "accuracy_by_band": by_band,
        "bets_by_band": bet_results,
        "full_fit_coefficients_per_sd": {f: round(float(b), 4) for f, b in zip(FEATURES, beta)},
        "notes": [
            "Negative brier_vs_market means the correction beat the market on dates it did not see.",
            "Coefficients are in log-odds per standard deviation of each factor; positive pushes toward the over.",
            "Bets use the offered odds (vig included); under bets only exist on main lines.",
        ],
    }
    REPORT_PATH.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def main() -> None:
    r = run()
    print(f"{r['lines']} lines ({r['over_lines']} overs, {r['dates']} dates). Market Brier {r['market_brier']}")
    print("\nOut-of-sample accuracy (Brier vs market; negative = better than market):")
    for k, v in r["variants"].items():
        print(f"  {k:<26} Brier {v['brier']}  diff {v['brier_vs_market']:+.5f} ± {v['brier_vs_market_date_se']}")
    print("\nHeadline by odds band (overs):")
    for k, v in r["accuracy_by_band"].items():
        print(f"  {k:<16} n={v['n_overs']:<5} market {v['brier_market']}  corrected {v['brier_corrected']}")
    print("\nBets at offered odds (headline correction):")
    for band, d in r["bets_by_band"].items():
        for t, s in d.items():
            if s["n"]:
                print(f"  {band:<16} edge {t:<5} n={s['n']:<5} hit {s['hit_rate']:.3f} implied {s['avg_implied']:.3f} "
                      f"ROI {s['roi_per_bet']:+.3f} ± {s['roi_date_se']}  units {s['units']:+.1f}")
    print("\nCoefficients (log-odds per SD):", r["full_fit_coefficients_per_sd"])


if __name__ == "__main__":
    main()
