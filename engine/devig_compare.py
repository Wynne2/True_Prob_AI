"""
Offline experiment: which way of removing the margin from a two-sided price predicts best?

The board takes each venue's over/under prices, removes the margin proportionally
(multiplicative: p_over = q_over / (q_over + q_under)) and uses the median across
venues. Bettors on r/algobetting and r/EVbetting argue that proportional removal
leaves long shots too high and that Shin's or the power method are better there.
This compares four methods on the same pairs, each scored against what happened:

    multiplicative  q / (q_over + q_under)                  (what the board uses)
    additive        q - (q_over + q_under - 1) / 2
    power           q ** k with k chosen so both sides sum to 1
    shin            Shin (1993) with z chosen so both sides sum to 1

None of them has a fitted parameter, so nothing is learned from the dates it is
scored on. Rows are every decided over/under pair quoted by one venue:

    data/backtests/expanded_report.json     Jan-Feb 2026 sportsbook lines
    data/predictions/*.graded.json          2026-27 live boards (sportsbooks and Kalshi)

A method "passes" only if its mean per-date Brier change against multiplicative is
below zero by more than two date-clustered standard errors; the report also gives
the stricter Bonferroni bar for three tests. Even a pass only justifies a retest on
later dates at the consensus level. Nothing reads the report; the board is unchanged.

Run: python -m engine.devig_compare
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from odds.normalizer import american_to_raw_implied_prob

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data" / "backtests" / "devig_methods.json"
BASELINE = "multiplicative"
# Two-sided 5% with a Bonferroni split over the three methods tested against the baseline.
BONFERRONI_Z = 2.394
_EPS = 1e-4


def multiplicative(qo: float, qu: float) -> float:
    return qo / (qo + qu)


def additive(qo: float, qu: float) -> float:
    return min(max(qo - (qo + qu - 1) / 2, _EPS), 1 - _EPS)


def _solve(f: Callable[[float], float], lo: float, hi: float) -> float:
    """Root of an increasing function on [lo, hi] by bisection (60 halvings is far below float precision)."""
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return (lo + hi) / 2


def power(qo: float, qu: float) -> float:
    if qo + qu <= 1:
        return multiplicative(qo, qu)
    k = _solve(lambda k: 1 - (qo ** k + qu ** k), 1.0, 50.0)
    return qo ** k


def _shin_one(q: float, total: float, z: float) -> float:
    return (math.sqrt(z * z + 4 * (1 - z) * q * q / total) - z) / (2 * (1 - z))


def shin(qo: float, qu: float) -> float:
    total = qo + qu
    if total <= 1:
        return multiplicative(qo, qu)
    z = _solve(lambda z: 1 - (_shin_one(qo, total, z) + _shin_one(qu, total, z)), 0.0, 0.999)
    return _shin_one(qo, total, z)


METHODS: dict[str, Callable[[float, float], float]] = {
    "multiplicative": multiplicative, "additive": additive, "power": power, "shin": shin,
}


def _pair(over_odds, under_odds) -> Optional[tuple[float, float]]:
    try:
        qo, qu = american_to_raw_implied_prob(int(over_odds)), american_to_raw_implied_prob(int(under_odds))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return (qo, qu) if 0 < qo < 1 and 0 < qu < 1 else None


def load_pairs(root: Path = ROOT) -> list[dict]:
    """[{date, qo, qu, y, venue}] one per decided (date, player, stat, line, venue) with both sides quoted."""
    out, seen = [], set()
    src = root / "data" / "backtests" / "expanded_report.json"
    if src.exists():
        sides: dict[tuple, dict] = defaultdict(dict)
        for r in json.loads(src.read_text(encoding="utf-8")).get("rows") or []:
            if r.get("variant") == "A_opportunity":
                sides[(r["game_date"], r["player_id"], r["prop_type"], r["line"], r["book"])][r["side"]] = r
        for key, s in sides.items():
            if "over" in s and "under" in s and s["over"].get("outcome") is not None and key not in seen:
                q = _pair(s["over"].get("american_odds"), s["under"].get("american_odds"))
                if q:
                    seen.add(key)
                    out.append({"date": key[0], "qo": q[0], "qu": q[1], "y": float(s["over"]["outcome"]), "venue": key[4]})
    for path in sorted((root / "data" / "predictions").glob("????-??-??.graded.json")):
        day = path.name[:10]
        sides = defaultdict(dict)
        for r in json.loads(path.read_text(encoding="utf-8")).get("rows") or []:
            parts = str(r.get("key", "")).split("|")
            if len(parts) == 6:
                game, player, prop, side, line, book = parts
                sides[(day, game, player, prop, line, book)][side] = r
        for key, s in sides.items():
            if "over" in s and "under" in s and s["over"].get("result") in ("win", "loss") and key not in seen:
                q = _pair(s["over"].get("odds"), s["under"].get("odds"))
                if q:
                    seen.add(key)
                    out.append({"date": day, "qo": q[0], "qu": q[1], "y": 1.0 if s["over"]["result"] == "win" else 0.0,
                                "venue": key[5]})
    return out


def _brier(rows: list[dict], method: str) -> float:
    f = METHODS[method]
    return statistics.fmean((f(r["qo"], r["qu"]) - r["y"]) ** 2 for r in rows)


def _log_loss(rows: list[dict], method: str) -> float:
    f = METHODS[method]

    def one(r):
        p = min(max(f(r["qo"], r["qu"]), _EPS), 1 - _EPS)
        return -(r["y"] * math.log(p) + (1 - r["y"]) * math.log(1 - p))
    return statistics.fmean(one(r) for r in rows)


def compare(rows: list[dict]) -> dict:
    dates = sorted({r["date"] for r in rows})
    by_date = {d: [r for r in rows if r["date"] == d] for d in dates}
    result = {}
    for m in METHODS:
        deltas = [_brier(by_date[d], m) - _brier(by_date[d], BASELINE) for d in dates]
        mean = statistics.fmean(deltas) if deltas else 0.0
        se = statistics.stdev(deltas) / math.sqrt(len(deltas)) if len(deltas) > 1 else None
        result[m] = {
            "brier": round(_brier(rows, m), 6) if rows else None,
            "log_loss": round(_log_loss(rows, m), 6) if rows else None,
            "mean_date_brier_delta": round(mean, 7),
            "date_se": None if se is None else round(se, 7),
            "dates_better": sum(d < 0 for d in deltas),
            "passes_two_se": m != BASELINE and se is not None and mean < -2 * se,
            "passes_bonferroni": m != BASELINE and se is not None and mean < -BONFERRONI_Z * se,
        }
    return result


def run(root: Path = ROOT, save: bool = True) -> dict:
    rows = load_pairs(root)
    # Long shots are where the methods differ: one side priced at 70% or more before the margin comes out.
    lopsided = [r for r in rows if max(r["qo"], r["qu"]) / (r["qo"] + r["qu"]) >= 0.70]
    overall, tails = compare(rows), compare(lopsided)
    passing = sorted(m for m, v in overall.items() if v["passes_two_se"])
    corrected = sorted(m for m, v in overall.items() if v["passes_bonferroni"])
    result = {
        "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "four parameter-free de-vig methods on the same single-venue over/under pairs; Brier per date "
                  "against multiplicative, date-clustered standard errors; offline only, not used by the board",
        "pairs": len(rows),
        "dates": sorted({r["date"] for r in rows}),
        "venues": dict(sorted(((v, sum(r["venue"] == v for r in rows)) for v in {r["venue"] for r in rows}), key=lambda x: -x[1])),
        "all_pairs": overall,
        "lopsided_pairs": {"pairs": len(lopsided), "dates": len({r["date"] for r in lopsided}), "by_method": tails},
        "passing": passing,
        "passing_bonferroni": corrected,
        "decision": (
            "No method beats multiplicative by more than two date-clustered standard errors. The board keeps "
            "proportional de-vig." if not passing else
            f"{', '.join(passing)} beat multiplicative by more than two date-clustered standard errors"
            + (f", and {', '.join(corrected)} also clear the Bonferroni bar for three tests" if corrected else
               ", but none clears the Bonferroni bar for three tests")
            + ". The pairs are single-venue prices, not the cross-venue median the board uses, so this is a lead for a "
              "retest on later graded boards at the consensus level, not grounds to change the board."),
    }
    if save:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


if __name__ == "__main__":
    r = run()
    print(f"{r['pairs']} pairs on {len(r['dates'])} dates; venues {r['venues']}")
    for label, block in (("all pairs", r["all_pairs"]), (f"lopsided ({r['lopsided_pairs']['pairs']} pairs)", r["lopsided_pairs"]["by_method"])):
        print(label)
        for m, v in block.items():
            print(f"  {m:<15} Brier {v['brier']}  log loss {v['log_loss']}  delta {v['mean_date_brier_delta']:+.7f} "
                  f"+/- {v['date_se']}  better on {v['dates_better']} dates  {'passes 2 SE' if v['passes_two_se'] else ''}"
                  f"{' and Bonferroni' if v['passes_bonferroni'] else ''}")
    print(r["decision"])
