"""
Candidate minutes forecast (experiment; production MinutesModel is unchanged).

Question: can a pregame minutes forecast beat the plain last-10 average?

Models
    last10        mean of the last 10 games played (the current baseline)
    ewm           exponentially weighted mean; half-life chosen on the select split
    early_blend   ewm pulled toward last season's minutes while this season's sample is small
    ridge_clean   ridge regression on recency, rest, last season and the player's own
                  recent missed games. Everything is known before tip-off.
    ridge_absence ridge_clean plus teammate absences inferred from the box score.
                  A rotation player missing from tonight's box is treated as known-out.
                  Late scratches and coach's decisions leak in, so this is an UPPER
                  BOUND on what pregame injury reports could add. Live, the same
                  feature would come from BALLDONTLIE injuries.

Splits (pre-2026 development data only)
    fit     2024-25 season and October 2025
    select  November 2025  (half-life, blend strength, ridge penalty)
    check   December 2025  (reported once for every model)

October 2025 is in the fit split so last season's minutes can earn a weight;
2024-25 has no earlier season in the data.

Run: python -m engine.accuracy.decompose   (once, for the chain rows)
     python -m engine.candidate.minutes
"""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from engine.accuracy import decompose as dec

TARGET_END = "2026-01-01"
FIT_END, SELECT_END = "2025-11-01", "2025-12-01"
HALF_LIVES = (2.0, 3.0, 5.0, 8.0, 12.0)
BLEND_STRENGTHS = (0.0, 2.0, 5.0, 10.0, 20.0)
LAMBDAS = (0.1, 1.0, 10.0, 100.0, 1000.0)
ROTATION_LOOKBACK = 5
ROTATION_MIN_GAMES = 3
ROTATION_MIN_MINUTES = 15.0
BOOTSTRAP_REPS = 300
REPORT_PATH = Path(__file__).resolve().parents[2] / "data" / "backtests" / "candidate_minutes.json"

CLEAN_FEATURES = (
    "ewm3", "ewm5", "ewm12", "last3", "last10", "season_mean", "last_season",
    "has_last_season", "inv_games", "b2b", "rest_days", "missed_recent", "just_returned",
)
ABSENCE_FEATURES = ("absent_minutes", "absent_share", "absent_share_starter")


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def ewm(values: list[float], half_life: float) -> float:
    """Exponentially weighted mean, newest value last and weighted most."""
    if not values:
        return 0.0
    decay = 0.5 ** (1.0 / half_life)
    weights = [decay ** (len(values) - 1 - i) for i in range(len(values))]
    return sum(w * v for w, v in zip(weights, values)) / sum(weights)


# ---------------------------------------------------------------------------
# Walk-forward rows
# ---------------------------------------------------------------------------

def build_rows(logs: list[dict], target_end: str = TARGET_END, emit_from: str = "") -> list[dict]:
    """
    One row per player-game with pregame minutes features.

    Uses the same eligibility as the error breakdown (5+ prior games played and a
    last-10 average of 12+ minutes), so rows join one-to-one with its chain rows.
    """
    by_season: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in logs:
        if row.get("game_date") and row.get("season") and row.get("team_id") and row.get("game_id"):
            by_season[row["season"]][row["game_date"]].append(row)

    last_season_minutes: dict[str, float] = {}
    out: list[dict] = []
    for season in sorted(by_season):
        days = by_season[season]
        played: dict[str, list[float]] = defaultdict(list)          # pid -> minutes, games played
        appearances: dict[str, set[str]] = defaultdict(set)         # pid -> game_ids played
        team_games: dict[str, list[tuple[str, str]]] = defaultdict(list)  # team -> (date, game_id)
        player_team: dict[str, str] = {}
        for game_day in sorted(days):
            todays = days[game_day]
            by_team_game: dict[tuple[str, str], list[dict]] = defaultdict(list)
            for row in todays:
                by_team_game[(str(row["team_id"]), str(row["game_id"]))].append(row)
            if emit_from <= game_day < target_end:
                for (team, game_id), members in by_team_game.items():
                    out.extend(_emit(
                        season, game_day, team, members, played, appearances,
                        team_games, player_team, last_season_minutes,
                    ))
            for (team, game_id), members in by_team_game.items():
                team_games[team].append((game_day, game_id))
                for row in members:
                    pid = str(row.get("player_id") or "")
                    minutes = _num(row.get("min"))
                    if not pid:
                        continue
                    player_team[pid] = team
                    if minutes > 0:
                        played[pid].append(minutes)
                        appearances[pid].add(game_id)
        for pid, minutes in played.items():
            if len(minutes) >= 10:
                last_season_minutes[pid] = sum(minutes) / len(minutes)
    return out


def _emit(season, game_day, team, members, played, appearances, team_games, player_team, last_season_minutes):
    history = team_games.get(team) or []
    recent_games = [gid for _, gid in history[-ROTATION_LOOKBACK:]]
    present = {str(r.get("player_id")) for r in members if _num(r.get("min")) > 0}

    rotation: dict[str, float] = {}
    for pid, team_of in player_team.items():
        if team_of != team or not recent_games:
            continue
        hits = [gid for gid in recent_games if gid in appearances.get(pid, set())]
        if len(hits) < ROTATION_MIN_GAMES:
            continue
        recent = played[pid][-len(hits):]
        avg = sum(recent) / len(recent)
        if avg >= ROTATION_MIN_MINUTES:
            rotation[pid] = avg
    absent_minutes = sum(m for pid, m in rotation.items() if pid not in present)
    present_rotation = sum(m for pid, m in rotation.items() if pid in present)

    rest_days = 4.0
    if history:
        rest_days = min(4.0, float((date.fromisoformat(game_day) - date.fromisoformat(history[-1][0])).days))

    rows = []
    for row in members:
        pid = str(row.get("player_id") or "")
        minutes = _num(row.get("min"))
        past = played.get(pid) or []
        if minutes <= 0 or len(past) < dec.MIN_HISTORY:
            continue
        last10 = sum(past[-10:]) / len(past[-10:])
        if last10 < dec.MIN_PROJECTED_MINUTES:
            continue
        own_recent = ewm(past[-10:], 5.0)
        share = own_recent / max(present_rotation, own_recent, 1.0)
        missed_recent = sum(1 for gid in recent_games if gid not in appearances.get(pid, set()))
        just_returned = 1.0 if history and history[-1][1] not in appearances.get(pid, set()) else 0.0
        has_last = pid in last_season_minutes
        starter = 1.0 if last10 >= 28 else 0.0
        features = {
            "ewm3": ewm(past[-20:], 3.0),
            "ewm5": ewm(past[-20:], 5.0),
            "ewm12": ewm(past[-30:], 12.0),
            "last3": sum(past[-3:]) / len(past[-3:]),
            "last10": last10,
            "season_mean": sum(past) / len(past),
            "last_season": last_season_minutes.get(pid, last10),
            "has_last_season": 1.0 if has_last else 0.0,
            "inv_games": 1.0 / len(past),
            "b2b": 1.0 if rest_days <= 1 else 0.0,
            "rest_days": rest_days,
            "missed_recent": float(missed_recent),
            "just_returned": just_returned,
            "absent_minutes": absent_minutes,
            "absent_share": absent_minutes * share,
            "absent_share_starter": absent_minutes * share * starter,
        }
        rows.append({
            "season": season,
            "game_date": game_day,
            "game_id": str(row.get("game_id")),
            "player_id": pid,
            "team_id": team,
            "role": "starter" if starter else "bench",
            "actual": minutes,
            "history": past[-30:],
            "n_games": len(past),
            "features": features,
        })
    return rows


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def split(rows: list[dict]):
    if any(r["game_date"] >= TARGET_END for r in rows):
        raise ValueError("Minutes experiment saw a game on or after 2026-01-01")
    fit = [r for r in rows if r["game_date"] < FIT_END]
    select = [r for r in rows if FIT_END <= r["game_date"] < SELECT_END]
    check = [r for r in rows if SELECT_END <= r["game_date"] < TARGET_END]
    return fit, select, check


def predict_ewm(row: dict, half_life: float) -> float:
    return ewm(row["history"], half_life)


def predict_blend(row: dict, half_life: float, strength: float) -> float:
    current = predict_ewm(row, half_life)
    if not row["features"]["has_last_season"] or strength <= 0:
        return current
    n = row["n_games"]
    return (n * current + strength * row["features"]["last_season"]) / (n + strength)


class Ridge:
    def __init__(self, names: tuple[str, ...], lam: float):
        self.names = names
        self.lam = lam

    def _x(self, rows):
        return np.array([[r["features"][n] for n in self.names] for r in rows])

    def fit(self, rows):
        x = self._x(rows)
        y = np.array([r["actual"] for r in rows])
        self.mu = x.mean(axis=0)
        self.sd = x.std(axis=0)
        self.sd[self.sd == 0] = 1.0
        z = (x - self.mu) / self.sd
        self.intercept = float(y.mean())
        gram = z.T @ z + self.lam * np.eye(len(self.names))
        self.beta = np.linalg.solve(gram, z.T @ (y - self.intercept))
        return self

    def predict(self, rows) -> np.ndarray:
        z = (self._x(rows) - self.mu) / self.sd
        return np.clip(self.intercept + z @ self.beta, 0.0, 48.0)

    def describe(self) -> dict:
        """Effect of a one-unit change in each raw feature, in minutes."""
        return {n: float(b / s) for n, b, s in zip(self.names, self.beta, self.sd)}


def _mae(pred, rows) -> float:
    return float(np.mean(np.abs(np.asarray(pred) - np.array([r["actual"] for r in rows]))))


def fit_models(fit, select) -> dict:
    """Choose each model's settings on the select split. Returns predictors."""
    best_hl = min(HALF_LIVES, key=lambda h: _mae([predict_ewm(r, h) for r in select], select))
    best_k = min(BLEND_STRENGTHS, key=lambda k: _mae([predict_blend(r, best_hl, k) for r in select], select))

    def best_ridge(names):
        fitted = [Ridge(names, lam).fit(fit) for lam in LAMBDAS]
        return min(fitted, key=lambda m: _mae(m.predict(select), select))

    clean = best_ridge(CLEAN_FEATURES)
    absence = best_ridge(CLEAN_FEATURES + ABSENCE_FEATURES)
    return {
        "settings": {
            "ewm_half_life": best_hl,
            "early_blend_strength": best_k,
            "ridge_clean_lambda": clean.lam,
            "ridge_absence_lambda": absence.lam,
        },
        "coefficients": {"ridge_clean": clean.describe(), "ridge_absence": absence.describe()},
        "predict": {
            "last10": lambda rows: np.array([r["features"]["last10"] for r in rows]),
            "ewm": lambda rows: np.array([predict_ewm(r, best_hl) for r in rows]),
            "early_blend": lambda rows: np.array([predict_blend(r, best_hl, best_k) for r in rows]),
            "ridge_clean": clean.predict,
            "ridge_absence": absence.predict,
        },
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def date_ci(rows, base_pred, new_pred, seed: int = 3) -> dict:
    """Mean and 95% interval of (new - base) absolute error, resampling dates."""
    actual = np.array([r["actual"] for r in rows])
    diff = np.abs(new_pred - actual) - np.abs(base_pred - actual)
    by_date = defaultdict(list)
    for r, d in zip(rows, diff):
        by_date[r["game_date"]].append(d)
    days = list(by_date)
    rng = random.Random(seed)
    draws = []
    for _ in range(BOOTSTRAP_REPS):
        pick = [days[rng.randrange(len(days))] for _ in days]
        vals = [v for d in pick for v in by_date[d]]
        draws.append(sum(vals) / len(vals))
    draws.sort()
    return {
        "mae_diff": float(diff.mean()),
        "ci95": [draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]],
        "n_dates": len(days),
    }


def score(rows, predictors) -> dict:
    out = {}
    for name, fn in predictors.items():
        pred = fn(rows)
        actual = np.array([r["actual"] for r in rows])
        entry = {
            "mae": _mae(pred, rows),
            "rmse": float(np.sqrt(np.mean((pred - actual) ** 2))),
            "bias": float(np.mean(actual - pred)),
        }
        for role in ("starter", "bench"):
            idx = [i for i, r in enumerate(rows) if r["role"] == role]
            if idx:
                entry[f"mae_{role}"] = float(np.mean(np.abs(pred[idx] - actual[idx])))
        out[name] = entry
    return out


def downstream(check_rows, chain_rows, predictors) -> dict:
    """Stat MAE when the chain forecast uses each model's minutes. Volume and conversion are unchanged."""
    index = {(r["player_id"], r["game_id"]): i for i, r in enumerate(check_rows)}
    minutes_by_model = {name: fn(check_rows) for name, fn in predictors.items()}
    result: dict = {}
    by_stat = defaultdict(list)
    for row in chain_rows:
        i = index.get((row["player_id"], row["game_id"]))
        if i is not None:
            by_stat[row["stat"]].append((row, i))
    for stat, pairs in sorted(by_stat.items()):
        actual = np.array([row["actual"] for row, _ in pairs])
        entry = {"n": len(pairs)}
        for name, minutes in minutes_by_model.items():
            pred = np.array([
                minutes[i] * row["forecast"]["volume"] * row["forecast"]["conversion"] for row, i in pairs
            ])
            entry[name] = float(np.mean(np.abs(pred - actual)))
        oracle = np.array([
            row["observed"]["minutes"] * row["forecast"]["volume"] * row["forecast"]["conversion"] for row, _ in pairs
        ])
        entry["actual_minutes"] = float(np.mean(np.abs(oracle - actual)))
        result[stat] = entry
    return result


def build_report(rows: list[dict], chain_rows: list[dict]) -> dict:
    fit, select, check = split(rows)
    models = fit_models(fit, select)
    predictors = models["predict"]
    check_scores = score(check, predictors)
    base = predictors["last10"](check)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "splits": {
            "fit": f"2024-25 and 2025-10 (n={len(fit)})",
            "select": f"2025-11 (n={len(select)})",
            "check": f"2025-12 (n={len(check)})",
        },
        "caveats": [
            "ridge_absence infers teammate absences from who is missing in the box score. "
            "Late scratches and coach's decisions leak in, so treat it as an upper bound.",
            "Eligibility matches the error breakdown: 5+ prior games and a last-10 average of 12+ minutes. "
            "Players who jump from a small role into big minutes are not in the sample.",
            "No pregame point spreads are used yet; blowout risk is not modeled here.",
            "Only games a player actually played are targets. A player rested on a back-to-back drops out "
            "of the sample, so the b2b coefficient measures minutes given that he plays, not rest risk.",
        ],
        "settings": models["settings"],
        "coefficients_minutes_per_unit": models["coefficients"],
        "select_scores": score(select, predictors),
        "check_scores": check_scores,
        "check_vs_last10": {
            name: date_ci(check, base, fn(check)) for name, fn in predictors.items() if name != "last10"
        },
        "check_stat_mae_by_minutes_model": downstream(check, chain_rows, predictors),
    }


def main() -> None:
    from engine.backtest.advanced_history import load_historical_logs

    logs, _ = load_historical_logs()
    rows = build_rows(logs)
    chain_rows = dec.build_rows(logs)
    report = build_report(rows, chain_rows)
    REPORT_PATH.write_text(json.dumps(report, indent=1), encoding="utf-8")

    print("Settings:", report["settings"])
    print(f"\n{'model':<15}{'Nov MAE':>9}{'Dec MAE':>9}{'starter':>9}{'bench':>8}{'bias':>7}   Dec vs last10 [95%]")
    for name, s in report["check_scores"].items():
        sel = report["select_scores"][name]["mae"]
        ci = report["check_vs_last10"].get(name)
        ci_text = f"{ci['mae_diff']:+.3f} [{ci['ci95'][0]:+.3f}, {ci['ci95'][1]:+.3f}]" if ci else ""
        print(f"{name:<15}{sel:>9.3f}{s['mae']:>9.3f}{s['mae_starter']:>9.3f}{s['mae_bench']:>8.3f}{s['bias']:>7.2f}   {ci_text}")
    print("\nDec stat MAE using each minutes model (volume and conversion unchanged)")
    for stat, e in report["check_stat_mae_by_minutes_model"].items():
        cells = "  ".join(f"{k} {v:.3f}" for k, v in e.items() if k != "n")
        print(f"  {stat:<9} n={e['n']:<5} {cells}")
    print("\nridge_absence, minutes per unit:")
    for k, v in report["coefficients_minutes_per_unit"]["ridge_absence"].items():
        print(f"  {k:<22}{v:+.3f}")
    print(f"\nSaved {REPORT_PATH}")


if __name__ == "__main__":
    main()
