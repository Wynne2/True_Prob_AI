"""
Does the matchup workflow find sportsbook mistakes? Backtest on saved lines.

Lines: Jan-Feb 2026 saved sportsbook props (both sides of the main line,
plus alternate overs). For each line, the Matchup Finder steps 1-3 are run
with games before that date only, and the candidate v3 configuration (fit on
games before 2026-01-01) supplies step 4.

The question is whether props that pass the screen beat the market:
    market miss = actual hit rate - average no-vig probability
A positive market miss on screened OVERS means books underprice players with
real opportunity against weak defenses. ROI is flat 1 unit at the offered odds.
Uncertainty is clustered by date (lines on one night are correlated).

Run: python -m engine.candidate.matchup_backtest
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from engine.backtest_grade import realized_roi
from engine.candidate import compare as ccmp
from engine.candidate import matchups as mf
from engine.candidate import model as cmodel
from engine.candidate import v2 as cv2
from engine.candidate import v3 as cv3

REPORT_PATH = cmodel.ROOT / "data" / "backtests" / "matchup_screen_backtest.json"


def _summary(rows: list[dict], prob_key: str = "v3") -> dict:
    if not rows:
        return {"n": 0}
    by_date = defaultdict(list)
    for r in rows:
        roi = realized_roi(None, int(r["american_odds"]), "win" if r["outcome"] == 1 else "loss")
        miss = float(r["outcome"]) - float(r["novig_probability"])
        by_date[r["game_date"]].append((roi, miss))
    flat = [x for v in by_date.values() for x in v]
    date_roi = [statistics.fmean(x[0] for x in v) for v in by_date.values()]
    date_miss = [statistics.fmean(x[1] for x in v) for v in by_date.values()]
    se = lambda xs: round(statistics.stdev(xs) / len(xs) ** 0.5, 4) if len(xs) > 1 else None
    out = {
        "n": len(rows), "dates": len(by_date),
        "hit_rate": round(statistics.fmean(float(r["outcome"]) for r in rows), 4),
        "avg_novig": round(statistics.fmean(float(r["novig_probability"]) for r in rows), 4),
        "avg_implied": round(statistics.fmean(float(r["implied_probability"]) for r in rows), 4),
        "market_miss": round(statistics.fmean(x[1] for x in flat), 4), "market_miss_date_se": se(date_miss),
        "roi_per_bet": round(statistics.fmean(x[0] for x in flat), 4), "roi_date_se": se(date_roi),
    }
    probs = [float(r[prob_key]) for r in rows if r.get(prob_key) is not None]
    if probs:
        out["avg_candidate"] = round(statistics.fmean(probs), 4)
    return out


def run() -> dict:
    from engine.backtest.advanced_history import load_historical_logs

    lines = ccmp.saved_lines()
    logs, _ = load_historical_logs()
    items = cmodel.build_items(logs, target_end="2026-03-01", spreads=cv2.spreads_for(logs))
    train = [it for it in items if it["game_date"] < "2026-01-01"]
    test = [it for it in items if it["game_date"] >= "2026-01-01"]
    v3_params, _ = cmodel.fit_params(train, settings=cv3.settings())
    ccmp.attach(lines, "v3", v3_params, test)
    mf.screen_lines(logs, lines, "2025-26")
    lines = [r for r in lines if r.get("screen") and r.get("v3") is not None]
    for r in lines:
        r["v3_edge"] = float(r["v3"]) - float(r["implied_probability"])

    def pick(side, cond):
        return [r for r in lines if r["side"] == side and cond(r["screen"])]

    full = lambda s: s["passes_screen"]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": "Jan-Feb 2026 saved sportsbook lines; ranks and averages from earlier 2025-26 games only",
        "lines": len(lines),
        "overs": {
            "all": _summary(pick("over", lambda s: True)),
            "passes_screen": _summary(pick("over", full)),
            "fails_screen": _summary(pick("over", lambda s: not s["passes_screen"])),
            "step1_only_bottom10_defense": _summary(pick("over", lambda s: s["step1_bottom10_defense"])),
            "step2_only_bottom10_vs_position": _summary(pick("over", lambda s: s["step2_bottom10_vs_position"])),
            "steps2_3_position_and_opportunity": _summary(pick("over", lambda s: s["step2_bottom10_vs_position"] and s["step3_opportunity"])),
            "passes_screen_and_v3_edge_3pct": _summary(
                [r for r in pick("over", full) if r["v3_edge"] >= 0.03]),
        },
        "unders": {
            "passes_screen": _summary(pick("under", full)),
            "fails_screen": _summary(pick("under", lambda s: not s["passes_screen"])),
        },
        "by_stat_overs_passing": {
            stat: _summary([r for r in pick("over", full) if str(r["prop_type"]).lower() == stat]) for stat in mf.STATS
        },
        "notes": [
            "Positive market_miss means the outcome beat the no-vig price; ROI is at the offered odds (vig included).",
            "Alternate lines are overs only, so overs outnumber unders.",
            "Thresholds were fixed before this run; nothing here was tuned on these lines.",
        ],
    }
    REPORT_PATH.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def _line(name, s):
    if not s.get("n"):
        return f"  {name:<36} n=0"
    return (f"  {name:<36} n={s['n']:<5} hit {s['hit_rate']:.3f} no-vig {s['avg_novig']:.3f} "
            f"miss {s['market_miss']:+.3f}±{s['market_miss_date_se']}  ROI {s['roi_per_bet']:+.3f}±{s['roi_date_se']}"
            + (f"  v3 {s['avg_candidate']:.3f}" if "avg_candidate" in s else ""))


def main() -> None:
    r = run()
    print(f"{r['lines']} lines")
    print("OVERS")
    for k, s in r["overs"].items():
        print(_line(k, s))
    print("UNDERS")
    for k, s in r["unders"].items():
        print(_line(k, s))
    print("OVERS passing the screen, by stat")
    for k, s in r["by_stat_overs_passing"].items():
        print(_line(k, s))
    print(f"Saved {REPORT_PATH}")


if __name__ == "__main__":
    main()
