"""
Where projection error comes from: minutes, opportunity volume, or conversion.

Each stat is written as a chain:

    stat = minutes x (opportunities per minute) x (stat per opportunity)

    points   = min x (FGA + 0.44 FTA)/min x points per shooting attempt
    threes   = min x 3PA/min             x 3P%
    rebounds = min x rebound chances/min x rebounds per chance
    assists  = min x passes/min          x assists per pass   (passes stand in
                                                                 for potential assists,
                                                                 which BALLDONTLIE does not
                                                                 report per game)

The pregame forecast of each link uses only earlier games in the same season.
Swapping a forecast link for its actual value shows how much squared error that
link carries. Shares are Shapley values over the three links, so the order of
swapping does not matter.

The spread check asks whether the standard deviations used for probabilities
match how far results actually land from the forecast.

Development data only: the 2024-25 season and October-December 2025.
January-March 2026 are never targets here.

Run: python -m engine.accuracy.decompose
"""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from datetime import date, datetime, timezone
from itertools import combinations
from pathlib import Path

import numpy as np

TARGET_END = "2026-01-01"
MIN_HISTORY = 5
MIN_PROJECTED_MINUTES = 12.0
MINUTES_WINDOW = 10
RATE_WINDOW = 20
SPREAD_WINDOW = 10
BOOTSTRAP_REPS = 300
REPORT_PATH = Path(__file__).resolve().parents[2] / "data" / "backtests" / "error_decomposition.json"

LINKS = ("minutes", "volume", "conversion")

# stat -> (made field, pseudo-count of opportunities pulling conversion to league rate)
CHAINS = {
    "points": ("pts", 60.0),
    "threes": ("fg3m", 40.0),
    "rebounds": ("reb", 40.0),
    "assists": ("ast", 200.0),
}

# Production VarianceModel constants (domain/constants.py), copied so this
# report shows what the live model would do. Not imported so a later change to
# production does not silently change a saved diagnostic.
PRODUCTION_INFLATION = {"points": 1.55, "rebounds": 2.05, "assists": 1.65, "threes": 2.00}
PRODUCTION_MIN_FRACTION = {"points": 0.30, "rebounds": 0.35, "assists": 0.40, "threes": 0.50}
PRODUCTION_ABS_MIN = {"points": 2.5, "rebounds": 1.2, "assists": 1.0, "threes": 0.7}

# Synthetic lines as multiples of the forecast mean. They are correlated within
# a player-game and are not sportsbook lines.
LINE_MULTIPLES = (0.6, 0.8, 1.0, 1.2, 1.4)


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Tracking fields below this many per minute are treated as a failed feed, not
# a real game (for example 1 pass in 36 minutes). Median is ~1 pass/minute.
MIN_PASSES_PER_MINUTE = 0.2


def opportunities(row: dict, stat: str) -> float | None:
    """
    Opportunity count for one game, or None when the field is missing or impossible.

    Impossible means fewer opportunities than makes (assists without passes,
    rebounds without chances) or a pass count far below any real rotation game.
    """
    if stat == "points":
        fga, fta = _num(row.get("fga")), _num(row.get("fta"))
        if fga is None or fta is None:
            return None
        return fga + 0.44 * fta
    if stat == "threes":
        return _num(row.get("fg3a"))
    if stat == "rebounds":
        chances = _num(row.get("rebound_chances_total"))
        reb = _num(row.get("reb"))
        if chances is None or (reb is not None and chances < reb):
            return None
        return chances
    if stat == "assists":
        passes = _num(row.get("passes"))
        ast = _num(row.get("ast"))
        minutes = _num(row.get("min")) or 0.0
        if passes is None or (ast is not None and passes < ast):
            return None
        if minutes >= 10 and passes < MIN_PASSES_PER_MINUTE * minutes:
            return None
        return passes
    raise KeyError(stat)


# ---------------------------------------------------------------------------
# Walk-forward rows
# ---------------------------------------------------------------------------

def build_rows(logs: list[dict], target_end: str = TARGET_END) -> list[dict]:
    """
    One row per player-game-stat with a pregame forecast of each link.

    History resets each season. A game only sees rows from strictly earlier dates.
    """
    played = [
        row for row in logs
        if row.get("game_date") and row.get("season") and (_num(row.get("min")) or 0) > 0
    ]
    by_season_date: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in played:
        by_season_date[row["season"]][row["game_date"]].append(row)

    out: list[dict] = []
    for season in sorted(by_season_date):
        days = by_season_date[season]
        history: dict[str, list[dict]] = defaultdict(list)
        league = {stat: [0.0, 0.0] for stat in CHAINS}  # made, opportunities
        for game_day in sorted(days):
            if game_day < target_end:
                for row in days[game_day]:
                    out.extend(_emit(row, history, league, season, game_day))
            for row in days[game_day]:
                pid = str(row.get("player_id") or "")
                if pid:
                    history[pid].append(row)
                for stat, (made_field, _) in CHAINS.items():
                    opp = opportunities(row, stat)
                    made = _num(row.get(made_field))
                    if opp is not None and made is not None and opp > 0:
                        league[stat][0] += made
                        league[stat][1] += opp
    return out


def _emit(row, history, league, season, game_day) -> list[dict]:
    pid = str(row.get("player_id") or "")
    past = history.get(pid) or []
    if len(past) < MIN_HISTORY:
        return []
    recent_minutes = [_num(r.get("min")) or 0.0 for r in past[-MINUTES_WINDOW:]]
    minutes_hat = sum(recent_minutes) / len(recent_minutes)
    if minutes_hat < MIN_PROJECTED_MINUTES:
        return []
    minutes = _num(row.get("min")) or 0.0
    role = "starter" if minutes_hat >= 28 else "bench"
    rows = []
    for stat, (made_field, pseudo) in CHAINS.items():
        made = _num(row.get(made_field))
        opp = opportunities(row, stat)
        if made is None or opp is None:
            continue
        window = [
            r for r in past[-RATE_WINDOW:]
            if opportunities(r, stat) is not None and _num(r.get(made_field)) is not None
        ]
        window_minutes = sum(_num(r.get("min")) or 0.0 for r in window)
        window_opp = sum(opportunities(r, stat) for r in window)
        window_made = sum(_num(r.get(made_field)) for r in window)
        league_made, league_opp = league[stat]
        if window_minutes <= 0 or window_opp <= 0 or league_opp <= 0:
            continue
        league_rate = league_made / league_opp
        volume_hat = window_opp / window_minutes
        conversion_hat = (window_made + pseudo * league_rate) / (window_opp + pseudo)
        volume = opp / minutes
        # A game with no opportunities has no observed conversion. Keep the
        # forecast so the swap does not invent a rate.
        conversion = made / opp if opp > 0 else conversion_hat
        recent_values = [
            _num(r.get(made_field)) or 0.0 for r in past[-SPREAD_WINDOW:]
        ]
        rows.append({
            "season": season,
            "game_date": game_day,
            "game_id": str(row.get("game_id") or ""),
            "player_id": pid,
            "role": role,
            "stat": stat,
            "actual": made,
            "forecast": {"minutes": minutes_hat, "volume": volume_hat, "conversion": conversion_hat},
            "observed": {"minutes": minutes, "volume": volume, "conversion": conversion},
            "recent_values": recent_values,
            "recent_minutes": recent_minutes,
            "rebound_chances_inconsistent": bool(row.get("rebound_chances_inconsistent")),
        })
    return rows


# ---------------------------------------------------------------------------
# Error shares
# ---------------------------------------------------------------------------

def chain_prediction(row: dict, swapped: frozenset[str]) -> float:
    value = 1.0
    for link in LINKS:
        value *= row["observed"][link] if link in swapped else row["forecast"][link]
    return value


def _subsets():
    for size in range(len(LINKS) + 1):
        for combo in combinations(LINKS, size):
            yield frozenset(combo)


def shapley_shares(mse: dict[frozenset, float]) -> dict[str, float]:
    """Share of baseline squared error carried by each link. Shares sum to 1."""
    total = mse[frozenset()]
    n = len(LINKS)
    shares = {}
    for link in LINKS:
        value = 0.0
        for subset in _subsets():
            if link in subset:
                continue
            weight = math.factorial(len(subset)) * math.factorial(n - len(subset) - 1) / math.factorial(n)
            value += weight * (mse[subset] - mse[subset | {link}])
        shares[link] = value / total if total > 0 else 0.0
    return shares


def _squared_errors(rows: list[dict]) -> dict[frozenset, np.ndarray]:
    actual = np.array([row["actual"] for row in rows])
    return {
        subset: (actual - np.array([chain_prediction(row, subset) for row in rows])) ** 2
        for subset in _subsets()
    }


def decompose(rows: list[dict], seed: int = 7) -> dict:
    if not rows:
        return {"n": 0}
    errors = _squared_errors(rows)
    mse = {subset: float(values.mean()) for subset, values in errors.items()}
    actual = np.array([row["actual"] for row in rows])
    baseline = np.array([chain_prediction(row, frozenset()) for row in rows])

    dates = sorted({row["game_date"] for row in rows})
    index = {day: i for i, day in enumerate(dates)}
    date_of = np.array([index[row["game_date"]] for row in rows])
    sums = {s: np.bincount(date_of, weights=v, minlength=len(dates)) for s, v in errors.items()}
    counts = np.bincount(date_of, minlength=len(dates))
    rng = random.Random(seed)
    draws = defaultdict(list)
    for _ in range(BOOTSTRAP_REPS):
        pick = [rng.randrange(len(dates)) for _ in dates]
        n = counts[pick].sum()
        boot = {s: float(v[pick].sum() / n) for s, v in sums.items()}
        for link, share in shapley_shares(boot).items():
            draws[link].append(share)

    def interval(values):
        values = sorted(values)
        return [values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]]

    shares = shapley_shares(mse)
    return {
        "n": len(rows),
        "n_dates": len(dates),
        "bias": float((actual - baseline).mean()),
        "mae": {
            "forecast": float(np.abs(actual - baseline).mean()),
            "actual_minutes": float(np.sqrt(errors[frozenset({"minutes"})]).mean()),
            "actual_minutes_and_volume": float(np.sqrt(errors[frozenset({"minutes", "volume"})]).mean()),
        },
        "rmse_forecast": math.sqrt(mse[frozenset()]),
        "shares": shares,
        "share_95": {link: interval(draws[link]) for link in LINKS},
    }


# ---------------------------------------------------------------------------
# Spread check
# ---------------------------------------------------------------------------

def _sample_sd(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def production_sd(row: dict, mean: float) -> float:
    stat = row["stat"]
    raw = _sample_sd(row["recent_values"]) if len(row["recent_values"]) >= 3 else mean * 0.35
    floor = max(mean * PRODUCTION_MIN_FRACTION[stat], PRODUCTION_ABS_MIN[stat])
    return max(raw * PRODUCTION_INFLATION[stat], floor)


def raw_recent_sd(row: dict, mean: float) -> float:
    return max(_sample_sd(row["recent_values"]), 0.35)


def fit_dispersion(rows: list[dict]) -> float:
    """phi in Var = phi x mean, by method of moments on forecast residuals."""
    num = sum((row["actual"] - chain_prediction(row, frozenset())) ** 2 for row in rows)
    den = sum(max(chain_prediction(row, frozenset()), 0.1) for row in rows)
    return num / den if den > 0 else 1.0


def _normal_over(mean: float, sd: float, line: float) -> float:
    z = (line - mean) / (max(sd, 0.35) * math.sqrt(2.0))
    return min(0.999, max(0.001, 0.5 - 0.5 * math.erf(z)))


def spread_check(rows: list[dict], sd_fn) -> dict:
    """
    z-score spread, central interval coverage, and synthetic-line calibration.

    A well-sized SD gives z SD near 1, 50% coverage near 0.50, and 80% near 0.80.
    """
    if not rows:
        return {"n": 0}
    z_values, in50, in80 = [], 0, 0
    probs, outcomes = [], []
    for row in rows:
        mean = chain_prediction(row, frozenset())
        sd = sd_fn(row, mean)
        z = (row["actual"] - mean) / sd
        z_values.append(z)
        in50 += abs(z) <= 0.6745
        in80 += abs(z) <= 1.2816
        for multiple in LINE_MULTIPLES:
            line = math.floor(mean * multiple) + 0.5
            probs.append(_normal_over(mean, sd, line))
            outcomes.append(1 if row["actual"] > line else 0)
    n = len(rows)
    p = np.array(probs)
    o = np.array(outcomes)
    # Fold Over and Under into one "favored side" view.
    fav_p = np.where(p >= 0.5, p, 1 - p)
    fav_o = np.where(p >= 0.5, o, 1 - o)
    bins = []
    for low in (0.5, 0.6, 0.7, 0.8, 0.9):
        mask = (fav_p >= low) & (fav_p < low + 0.1)
        if mask.sum():
            bins.append({
                "bin": f"{int(low * 100)}-{int(low * 100) + 10}%",
                "n": int(mask.sum()),
                "predicted": float(fav_p[mask].mean()),
                "actual": float(fav_o[mask].mean()),
            })
    return {
        "n": n,
        "z_sd": float(np.std(z_values)),
        "coverage_50": in50 / n,
        "coverage_80": in80 / n,
        "synthetic_brier": float(((p - o) ** 2).mean()),
        "synthetic_n": len(probs),
        "favored_side_bins": bins,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(rows: list[dict]) -> dict:
    late = [row for row in rows if row["game_date"] >= TARGET_END]
    if late:
        raise ValueError("Decomposition saw a target game on or after 2026-01-01")

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": "2024-25 regular season and 2025-26 through 2025-12-31. January-March 2026 are not targets.",
        "filters": {
            "min_prior_games": MIN_HISTORY,
            "min_projected_minutes": MIN_PROJECTED_MINUTES,
            "minutes_forecast": f"mean of last {MINUTES_WINDOW} games",
            "rate_forecast": f"ratio of sums over last {RATE_WINDOW} games; conversion shrunk to the running league rate",
        },
        "caveats": [
            "This forecast is a plain walk-forward chain, not the production PointsModel. It isolates where error lives, not how production scores.",
            "Assist opportunities use passes. Game-level potential assists are not available.",
            "Rebound chances are used as reported unless they are below rebounds. Rows flagged inconsistent are counted, not dropped.",
            f"Passes are treated as missing when below assists or below {MIN_PASSES_PER_MINUTE} per minute in 10+ minutes (failed tracking feed). Those games leave the assists chain and its history windows.",
            "Spread checks use a normal approximation. Synthetic lines are correlated within a player-game and are not sportsbook lines.",
            "Historical injuries and lineups are not available, so minutes error includes unforeseen absences and returns.",
        ],
        "decomposition": {},
        "spread": {},
    }
    by_stat: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_stat[row["stat"]].append(row)

    for stat, stat_rows in sorted(by_stat.items()):
        entry = {"all": decompose(stat_rows)}
        for role in ("starter", "bench"):
            entry[role] = decompose([r for r in stat_rows if r["role"] == role])
        for season in ("2024-25", "2025-26"):
            entry[season] = decompose([r for r in stat_rows if r["season"] == season])
        if stat == "rebounds":
            entry["rebound_chances_inconsistent_rows"] = sum(r["rebound_chances_inconsistent"] for r in stat_rows)
        report["decomposition"][stat] = entry

        fit_rows = [r for r in stat_rows if r["season"] == "2024-25"]
        test_rows = [r for r in stat_rows if r["season"] == "2025-26"]
        phi = fit_dispersion(fit_rows)

        def fitted_sd(row, mean, phi=phi):
            return math.sqrt(phi * max(mean, 0.1))

        report["spread"][stat] = {
            "evaluated_on": "2025-26 through 2025-12-31",
            "dispersion_fit_on": "2024-25",
            "dispersion_phi": phi,
            "production_sd": spread_check(test_rows, production_sd),
            "raw_last10_sd": spread_check(test_rows, raw_recent_sd),
            "fitted_dispersion_sd": spread_check(test_rows, fitted_sd),
        }
    return report


def _print_summary(report: dict) -> None:
    print("\nError shares (Shapley, % of forecast squared error)")
    print(f"{'stat':<10}{'n':>7}{'MAE':>7}{'bias':>7}  {'minutes':>14}{'volume':>14}{'conversion':>14}")
    for stat, entry in report["decomposition"].items():
        d = entry["all"]
        cells = []
        for link in LINKS:
            lo, hi = d["share_95"][link]
            cells.append(f"{d['shares'][link]*100:5.1f} [{lo*100:3.0f},{hi*100:3.0f}]")
        print(f"{stat:<10}{d['n']:>7}{d['mae']['forecast']:>7.2f}{d['bias']:>7.2f}  " + "".join(f"{c:>14}" for c in cells))
    print("\nSpread on Oct-Dec 2025 (target: z SD 1.00, 50% cov 0.50, 80% cov 0.80)")
    for stat, entry in report["spread"].items():
        for method in ("production_sd", "raw_last10_sd", "fitted_dispersion_sd"):
            s = entry[method]
            print(f"{stat:<10}{method:<22} zSD {s['z_sd']:.2f}  cov50 {s['coverage_50']:.2f}  cov80 {s['coverage_80']:.2f}  Brier {s['synthetic_brier']:.4f}")


def main() -> None:
    from engine.backtest.advanced_history import load_historical_logs

    logs, _meta = load_historical_logs()
    rows = build_rows(logs)
    report = build_report(rows)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=1), encoding="utf-8")
    _print_summary(report)
    print(f"\nSaved {REPORT_PATH}")


if __name__ == "__main__":
    main()
