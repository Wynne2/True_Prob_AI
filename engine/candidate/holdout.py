"""
Score the FROZEN candidate on 2026-27 games. Nothing here is fitted or tuned.

Two checks on the same holdout games:

1. Box-score check (free): projection error and synthetic-line probabilities
   for every eligible player-game, candidate vs the Model A chain.
2. Sportsbook check (Odds API credits, capped): the exact pregame lines that
   were offered about two hours before tip, scored for
       A   production Model A code (engine/backtest, opportunity model)
       B   production with matchup context (engine/backtest variant C)
       cand  frozen candidate v1
       market  no-vig sportsbook probability
   on identical props. Over side, evaluation line per player/stat, pushes excluded.

The production backtest code labels its current season "2025-26" and its prior
"2024-25". For the holdout its copy of the logs is relabeled (2026-27 -> 2025-26,
2025-26 -> 2024-25). The candidate reads the real labels.

Re-running later adds newer games. The parameter file is never rewritten here.

Run: python -m engine.candidate.holdout [--credit-cap 1250]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from engine.candidate import model as cmodel

REPORT_PATH = cmodel.ROOT / "data" / "backtests" / "candidate_v1_holdout.json"
HOLDOUT_START = date(2026, 10, 1)
CREDITS_PER_EVENT = 40
DEFAULT_CREDIT_CAP = 1250
MAX_GAMES = 30
_PROP_STAT = {"points": "points", "rebounds": "rebounds", "assists": "assists", "threes": "threes"}


def holdout_windows(through: date) -> tuple:
    windows = []
    start = HOLDOUT_START
    while start <= through:
        month_end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        end = min(month_end, through)
        windows.append((start, end, "2026-27"))
        start = end + timedelta(days=1)
    return tuple(windows)


def load_all_logs(through: date) -> list[dict]:
    from engine.backtest.advanced_history import _load_windows, load_historical_logs

    logs, _ = load_historical_logs()
    extra, _ = _load_windows(cmodel.EXTRA_2025_26_WINDOWS + holdout_windows(through))
    return logs + extra


def relabel_for_production(logs: list[dict]) -> list[dict]:
    """Production backtest helpers expect current = 2025-26 and prior = 2024-25."""
    mapping = {"2026-27": "2025-26", "2025-26": "2024-25"}
    return [dict(r, season=mapping[r["season"]]) for r in logs if r.get("season") in mapping]


def _events_for_dates(dates: list[date]) -> tuple[list[dict], int]:
    """Events tipping on each holdout date (US evening), newest dates first, capped."""
    from engine.backtest.experiment import _list_dates

    listed, _quota = _list_dates(tuple(dates))
    chosen, seen = [], set()
    for day, events in sorted(listed, key=lambda x: x[0], reverse=True):
        lo = datetime(day.year, day.month, day.day, 18, 0, tzinfo=timezone.utc)
        hi = lo + timedelta(hours=18)
        for event in sorted(events, key=lambda e: str(e.get("commence_time") or "")):
            tip = event.get("commence_time")
            if not tip or event.get("id") in seen:
                continue
            when = datetime.fromisoformat(str(tip).replace("Z", "+00:00"))
            if lo <= when < hi and len(chosen) < MAX_GAMES:
                chosen.append({**event, "slate_date": day.isoformat()})
                seen.add(event.get("id"))
    return chosen, len(dates)


def score_box(items: list[dict], params: dict) -> dict:
    return cmodel.evaluate(items, params)


def score_priced(items: list[dict], params: dict, logs: list[dict], credit_cap: int) -> dict:
    from engine.accuracy.run import _high, _pack, _paired
    from engine.backtest.experiment import _odds_cached, _score_events
    from engine.backtest.variants import VARIANT_A, VARIANT_C

    dates = sorted({date.fromisoformat(it["game_date"]) for it in items})
    if not dates:
        return {"status": "not_run", "reason": "No eligible holdout games yet."}
    events, list_credits = _events_for_dates(dates)
    cold = sum(CREDITS_PER_EVENT for e in events if not _odds_cached(e))
    cost = {"events_list_credits": list_credits, "event_odds_credits": cold, "total": list_credits + cold, "cap": credit_cap}
    if list_credits + cold > credit_cap:
        return {"status": "not_run", "reason": "Estimated credits exceed the cap.", "cost": cost}

    scored = _score_events(events, relabel_for_production(logs), period="candidate_v1_holdout")
    preds = cmodel.predict(items, params)
    by_key = {(it["game_date"], it["player_id"], it["stat"]): p for it, p in zip(items, preds)}
    base_by_key = {(it["game_date"], it["player_id"], it["stat"]): it for it in items}

    production = {
        (r["game_date"], r["player_id"], r["prop_type"], r["line"]): r
        for r in scored
        if r.get("variant") == VARIANT_C and r.get("evaluation_line") and r.get("side") == "over"
    }
    usable = []
    for r in scored:
        if r.get("variant") != VARIANT_A or not r.get("evaluation_line") or r.get("side") != "over":
            continue
        if r.get("result") not in ("win", "loss"):
            continue
        key = (r["game_date"], str(r["player_id"]), _PROP_STAT.get(r["prop_type"]))
        pred = by_key.get(key)
        prod = production.get((r["game_date"], r["player_id"], r["prop_type"], r["line"]))
        if pred is None or prod is None or r.get("novig_probability") is None:
            continue
        line = float(r["line"])
        over = cmodel.over_probability(pred, line)
        push = cmodel.push_probability(pred, line)
        cand = min(0.999, max(0.001, over / max(1.0 - push, 1e-6)))  # graded rows exclude pushes
        row = dict(r)
        row["cand_probability"] = cand
        row["cand_projected"] = pred["mean"]
        row["b_probability"] = prod.get("raw_probability")
        row["chain_probability"] = cmodel.baseline_over(base_by_key[key], line)
        usable.append(row)

    if not usable:
        return {"status": "no_overlap", "cost": cost, "scored_rows": len(scored)}
    mae = defaultdict(lambda: defaultdict(list))
    for row in usable:
        mae[row["prop_type"]]["A_production"].append(abs(float(row["projected"]) - float(row["actual"])))
        mae[row["prop_type"]]["candidate"].append(abs(float(row["cand_projected"]) - float(row["actual"])))
    return {
        "status": "scored",
        "cost": cost,
        "n": len(usable),
        "n_games": len({r["game_id"] for r in usable}),
        "n_dates": len({r["game_date"] for r in usable}),
        "models": {
            "A_production_model_a": _pack(usable, "raw_probability"),
            "B_production_with_context": _pack(usable, "b_probability"),
            "chain_baseline": _pack(usable, "chain_probability"),
            "candidate_v1": _pack(usable, "cand_probability"),
            "sportsbook_novig": _pack(usable, "novig_probability"),
        },
        "candidate_minus_A": _paired(usable, "raw_probability", "cand_probability"),
        "candidate_minus_B": _paired(usable, "b_probability", "cand_probability"),
        "candidate_minus_market": _paired(usable, "novig_probability", "cand_probability"),
        "high_probability_70_90": {
            "A_production_model_a": _high(usable, "raw_probability"),
            "candidate_v1": _high(usable, "cand_probability"),
            "sportsbook_novig": _high(usable, "novig_probability"),
        },
        "projection_mae_on_priced_props": {
            prop: {k: sum(v) / len(v) for k, v in d.items()} | {"n": len(d["candidate"])} for prop, d in mae.items()
        },
        "rows": [
            {k: r.get(k) for k in (
                "game_date", "game_id", "player", "prop_type", "line", "american_odds", "actual", "outcome",
                "raw_probability", "b_probability", "chain_probability", "cand_probability", "novig_probability",
                "projected", "cand_projected",
            )}
            for r in usable
        ],
    }


def run(credit_cap: int = DEFAULT_CREDIT_CAP, through: date | None = None) -> dict:
    params = cmodel.load_params()
    if params is None:
        raise SystemExit("No frozen parameters. Run python -m engine.candidate.model first.")
    through = through or (date.today() - timedelta(days=1))
    logs = load_all_logs(through)
    items = [
        it for it in cmodel.build_items(logs, target_end=(through + timedelta(days=1)).isoformat())
        if it["season"] == "2026-27"
    ]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": params.get("version"),
        "frozen_at": params.get("frozen_at"),
        "frozen_code_commit": params.get("frozen_code_commit"),
        "holdout_window": f"{HOLDOUT_START.isoformat()} .. {through.isoformat()}",
        "eligible_rows": len(items),
        "eligible_dates": sorted({it["game_date"] for it in items}),
        "rule": "Parameters frozen before this window was scored. Do not refit on these results.",
        "box_score_check": score_box(items, params),
        "sportsbook_check": score_priced(items, params, logs, credit_cap),
    }
    REPORT_PATH.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


def _print(report: dict) -> None:
    print(f"Holdout {report['holdout_window']}: {report['eligible_rows']} rows on {len(report['eligible_dates'])} dates")
    for stat, e in report["box_score_check"].items():
        s, d = e["synthetic"], e["candidate_minus_baseline_brier"]
        print(f"  box {stat:<9} n={e['n']:<5} MAE {e['mae']['baseline']:.3f} -> {e['mae']['candidate']:.3f} | "
              f"Brier {s['baseline']['brier']:.4f} -> {s['candidate']['brier']:.4f} "
              f"(diff {d['brier_diff']:+.4f} [{d['ci95'][0]:+.4f}, {d['ci95'][1]:+.4f}], {d['n_dates']} dates)")
    p = report["sportsbook_check"]
    print("\nSportsbook check:", p.get("status"), p.get("cost"))
    if p.get("status") == "scored":
        print(f"  n={p['n']} props, {p['n_games']} games, {p['n_dates']} dates")
        for name, m in p["models"].items():
            print(f"  {name:<28} Brier {m.get('brier'):.4f}  log loss {m.get('log_loss'):.4f}  date SE {m.get('date_cluster_se') or 0:.4f}")
        for k in ("candidate_minus_A", "candidate_minus_B", "candidate_minus_market"):
            d = p[k]
            print(f"  {k:<28} {d.get('mean_brier_difference'):+.4f}  game SE {d.get('game_cluster_se') or 0:.4f}")
    print(f"\nSaved {REPORT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credit-cap", type=int, default=DEFAULT_CREDIT_CAP)
    args = parser.parse_args()
    _print(run(credit_cap=args.credit_cap))


if __name__ == "__main__":
    main()
