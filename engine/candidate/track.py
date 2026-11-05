"""
Live track record: grade each night's saved board and keep a running scorecard.

Every scan of today's (or a future) slate saves a pregame snapshot of every
priced prop to data/predictions/{date}.json (engine/prediction_ledger.py). The
first save of a prop keeps its price and probabilities; later scans never
overwrite them. This module grades past dates against BALLDONTLIE box scores
(engine/clv.py) and summarizes every graded row from the candidate's holdout
start on:

    Brier    candidate vs production blend vs no-vig market, on the same props
    bets     props whose candidate edge (candidate - book implied) clears a
             threshold: count, hit rate, average predicted chance, flat-stake ROI

Lines can be correlated (alternate lines, same player, same game), so counts
overstate independent evidence; per-date spread is reported as a guide.

Run: python -m engine.candidate.track            (grade pending dates + print scorecard)
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from engine import clv
from engine.backtest_grade import brier_score, realized_roi

EDGE_BUCKETS = ((0.0, 0.03), (0.03, 0.06), (0.06, 0.10), (0.10, 1.0))
DEFAULT_START = date(2026, 11, 4)   # candidate v2 holdout start


def _dates(directory: Path) -> list[date]:
    out = []
    for p in directory.glob("????-??-??.json"):
        try:
            out.append(date.fromisoformat(p.stem))
        except ValueError:
            continue
    return sorted(out)


def grade_pending(today: date | None = None, directory: Path | str = clv.DEFAULT_DIR, since: date = DEFAULT_START) -> list[dict]:
    """Grade every saved date before today whose graded file is missing or has non-final games."""
    today = today or date.today()
    directory = Path(directory)
    results = []
    for d in _dates(directory):
        if d < since or d >= today:
            continue
        graded = directory / f"{d.isoformat()}.graded.json"
        if graded.exists():
            summary = json.loads(graded.read_text(encoding="utf-8")).get("summary") or {}
            ledger_games = {r.get("game_id") for r in clv.load_ledger(d, directory)}
            if summary.get("final_games", 0) >= len(ledger_games):
                continue
        results.append(clv.grade_predictions(d, directory))
    return results


def _decided(rows):
    return [r for r in rows if r.get("result") in ("win", "loss")]


def _brier(rows, key):
    pairs = [(float(r[key]), 1 if r["result"] == "win" else 0) for r in rows if r.get(key) is not None]
    return round(statistics.fmean(brier_score(p, y) for p, y in pairs), 5) if pairs else None


def _bets(rows):
    if not rows:
        return {"n": 0}
    y = [1 if r["result"] == "win" else 0 for r in rows]
    rois = [realized_roi(None, int(r.get("odds") or 0), r["result"]) for r in rows]
    by_date = defaultdict(list)
    for r, roi in zip(rows, rois):
        by_date[r["date"]].append(roi)
    date_means = [statistics.fmean(v) for v in by_date.values()]
    return {
        "n": len(rows),
        "dates": len(by_date),
        "hit_rate": round(sum(y) / len(y), 4),
        "avg_candidate_probability": round(statistics.fmean(float(r["candidate_probability"]) for r in rows), 4),
        "avg_implied": round(statistics.fmean(float(r["implied_probability"]) for r in rows), 4),
        "flat_roi_per_bet": round(statistics.fmean(rois), 4),
        "roi_date_se": round(statistics.stdev(date_means) / len(date_means) ** 0.5, 4) if len(date_means) > 1 else None,
    }


def load_graded(directory: Path | str = clv.DEFAULT_DIR, since: date = DEFAULT_START) -> list[dict]:
    rows = []
    for p in sorted(Path(directory).glob("????-??-??.graded.json")):
        d = date.fromisoformat(p.name[:10])
        if d < since:
            continue
        for r in json.loads(p.read_text(encoding="utf-8")).get("rows") or []:
            rows.append(dict(r, date=d.isoformat()))
    return rows


def scorecard(directory: Path | str = clv.DEFAULT_DIR, since: date = DEFAULT_START, version: str | None = None) -> dict:
    """Scorecard for graded rows since ``since``; ``version`` keeps only rows the given candidate scored."""
    rows = _decided(load_graded(directory, since))
    if version:
        rows = [r for r in rows if r.get("candidate_version") == version]
    with_cand = [r for r in rows if r.get("candidate_probability") is not None and r.get("implied_probability") is not None]
    same = [r for r in with_cand if r.get("market_probability") is not None and r.get("production_probability") is not None]
    edge_rows = [r for r in with_cand if not r.get("candidate_outside_range")]
    for r in edge_rows:
        r["candidate_edge"] = float(r["candidate_probability"]) - float(r["implied_probability"])
    buckets = {}
    for lo, hi in EDGE_BUCKETS:
        buckets[f"{lo:.0%}-{hi:.0%}" if hi < 1 else f"{lo:.0%}+"] = _bets(
            [r for r in edge_rows if lo <= r["candidate_edge"] < hi]
        )
    tagged = [r for r in with_cand if isinstance(r.get("matchup"), dict)]
    for r in tagged:
        r.setdefault("candidate_edge", float(r["candidate_probability"]) - float(r["implied_probability"]))
    matchup_groups = {
        "fade_picks": _bets([r for r in tagged if r["matchup"].get("fade_pick")]),
        "screened_overs": _bets([r for r in tagged if r["matchup"].get("screened_over")]),
        "screened_rows": len(tagged),
    }
    return {
        "matchup": matchup_groups,
        "version": version or "all",
        "since": since.isoformat(),
        "dates": sorted({r["date"] for r in rows}),
        "decided_rows": len(rows),
        "rows_with_candidate": len(with_cand),
        "brier_same_props": {
            "n": len(same),
            "candidate": _brier(same, "candidate_probability"),
            "production": _brier(same, "production_probability"),
            "market_novig": _brier(same, "market_probability"),
        },
        "candidate_edge_bets": buckets,
        "note": "Lower Brier is better. Lines for one player and game are correlated; read counts as rough.",
    }


def _print(card: dict, graded: list[dict]) -> None:
    for g in graded:
        print(f"graded {g.get('date')}: {g.get('final_games', 0)} final games, {g.get('all', {}).get('decided', 0)} decided rows")
    print(f"\nScorecard [{card['version']}] since {card['since']} ({len(card['dates'])} dates, {card['decided_rows']} decided props)")
    b = card["brier_same_props"]
    print(f"  Brier on {b['n']} shared props: candidate {b['candidate']}  production {b['production']}  market {b['market_novig']}")
    m = card.get("matchup") or {}
    if m.get("screened_rows"):
        for name in ("fade_picks", "screened_overs"):
            s = m[name]
            print(f"  Matchup {name}: " + (f"n={s['n']} hit {s['hit_rate']:.1%} implied {s['avg_implied']:.1%} "
                                          f"ROI {s['flat_roi_per_bet']:+.3f}" if s["n"] else "n=0"))
    print("  Candidate-edge bets (flat stake):")
    for name, s in card["candidate_edge_bets"].items():
        if s["n"]:
            print(f"    edge {name:<8} n={s['n']:<5} hit {s['hit_rate']:.1%}  predicted {s['avg_candidate_probability']:.1%}  "
                  f"implied {s['avg_implied']:.1%}  ROI {s['flat_roi_per_bet']:+.3f}"
                  + (f" (date SE {s['roi_date_se']:.3f})" if s.get("roi_date_se") is not None else ""))
        else:
            print(f"    edge {name:<8} n=0")


def holdout_start(version: str) -> date:
    """A version's own holdout start (from its frozen file); earlier games do not count for it."""
    from engine.candidate import model as cmodel

    short = version.replace("candidate-", "")
    path = cmodel.ROOT / "data" / "models" / f"candidate_{short}.json"
    params = cmodel.load_params(path) or {}
    try:
        return max(DEFAULT_START, date.fromisoformat(params.get("holdout_start") or DEFAULT_START.isoformat()))
    except ValueError:
        return DEFAULT_START


def versions(directory: Path | str = clv.DEFAULT_DIR, since: date = DEFAULT_START) -> list[str]:
    return sorted({r.get("candidate_version") for r in load_graded(directory, since) if r.get("candidate_version")})


def main() -> None:
    graded = grade_pending()
    found = versions()
    if not found:
        _print(scorecard(), graded)
    for i, v in enumerate(found):
        _print(scorecard(since=holdout_start(v), version=v), graded if i == 0 else [])


if __name__ == "__main__":
    main()
