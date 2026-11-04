"""
Candidate opportunity-volume forecast (experiment; production models are unchanged).

Question: can a pregame forecast of opportunities per minute beat the plain
20-game ratio the chain uses today?

Volume per stat (same definitions as engine/accuracy/decompose.py):
    points   (FGA + 0.44 FTA) per minute
    threes   3PA per minute
    rebounds rebound chances per minute
    assists  passes per minute

Models, fitted separately for each stat:
    ratio20        sum of opportunities / sum of minutes over the last 20 games (baseline)
    ewm            exponentially weighted per-game rate; half-life chosen on select
    ridge_clean    ridge on recency, last season, usage trend, touches trend,
                   opponent pace and opponent volume allowed (all known pregame)
    ridge_absence  ridge_clean plus teammate absences from the box score
                   (UPPER BOUND: late scratches leak in)

Scoring holds minutes at their actual value, so only volume is judged:
    error = | actual minutes x forecast rate - actual opportunities |
Then the combined chain (candidate minutes x candidate volume x forecast
conversion) is scored against the stat itself.

Each clean feature group is removed and the model refit to show what it adds.

Splits match engine/candidate/minutes.py:
    fit 2024-25 + Oct 2025, select Nov 2025, check Dec 2025.

Run: python -m engine.candidate.volume
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from engine.accuracy import decompose as dec
from engine.backtest.profiles import team_game_rate
from engine.candidate import minutes as cm

STATS = tuple(dec.CHAINS)
HALF_LIVES = (3.0, 5.0, 8.0, 12.0, 20.0)
LAMBDAS = (0.1, 1.0, 10.0, 100.0, 1000.0)
OPPONENT_PRIOR_GAMES = 10.0
MIN_TOUCHES_PER_MINUTE = 0.3
BOOTSTRAP_REPS = 300
REPORT_PATH = Path(__file__).resolve().parents[2] / "data" / "backtests" / "candidate_volume.json"

GROUPS = {
    "recency": ("ewm_rate", "last5_rate", "season_rate"),
    "last_season": ("last_season_rate", "has_last_season"),
    "usage": ("usage_delta_x_rate",),
    "touches": ("touches_delta_x_rate",),
    "pace": ("pace_gap_x_rate",),
    "opponent_allowed": ("allowed_gap_x_rate",),
    "rest": ("b2b",),
}
BASE_FEATURES = ("base_rate",)
CLEAN_FEATURES = BASE_FEATURES + tuple(f for g in GROUPS.values() for f in g)
ABSENCE_FEATURES = ("absent_share",)


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _mean(values) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def _ratio(rows: list[dict], stat: str) -> float:
    opp = sum(dec.opportunities(r, stat) for r in rows)
    minutes = sum(_num(r.get("min")) for r in rows)
    return opp / minutes if minutes > 0 else 0.0


def _touches(row: dict) -> float | None:
    touches = row.get("touches")
    minutes = _num(row.get("min"))
    if touches in (None, "") or minutes <= 0:
        return None
    t = _num(touches)
    if minutes >= 10 and t < MIN_TOUCHES_PER_MINUTE * minutes:
        return None  # failed tracking feed
    return t / minutes


def _gap(values: list[float], league: list[float]) -> float:
    league_mean = _mean(league)
    own = _mean(values)
    if not values or not league_mean:
        return 0.0
    shrink = len(values) / (len(values) + OPPONENT_PRIOR_GAMES)
    return shrink * (own - league_mean) / league_mean


# ---------------------------------------------------------------------------
# Walk-forward rows
# ---------------------------------------------------------------------------

def build_rows(logs: list[dict], target_end: str = cm.TARGET_END) -> list[dict]:
    """One row per player-game-stat. Same eligibility as the minutes experiment."""
    by_season: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in logs:
        if row.get("game_date") and row.get("season") and row.get("team_id") and row.get("game_id"):
            by_season[row["season"]][row["game_date"]].append(row)

    last_season_rate: dict[tuple[str, str], float] = {}
    out: list[dict] = []
    for season in sorted(by_season):
        days = by_season[season]
        state = {
            "hist": defaultdict(list),          # pid -> played rows
            "appearances": defaultdict(set),    # pid -> game ids played
            "team_games": defaultdict(list),    # team -> (date, game id)
            "player_team": {},
            "team_pace": defaultdict(list),
            "league_pace": [],
            "allowed": defaultdict(list),       # (team, stat) -> opponent volume per game
            "league_allowed": defaultdict(list),
        }
        for game_day in sorted(days):
            by_team_game: dict[tuple[str, str], list[dict]] = defaultdict(list)
            for row in days[game_day]:
                by_team_game[(str(row["team_id"]), str(row["game_id"]))].append(row)
            if game_day < target_end:
                for (team, game_id), members in by_team_game.items():
                    out.extend(_emit(season, game_day, team, members, state, last_season_rate))
            _update(by_team_game, game_day, state)
        for pid, rows in state["hist"].items():
            if len(rows) >= 10:
                for stat in STATS:
                    valid = [r for r in rows if dec.opportunities(r, stat) is not None]
                    if len(valid) >= 10:
                        last_season_rate[(pid, stat)] = _ratio(valid, stat)
    return out


def _update(by_team_game, game_day, state) -> None:
    totals: dict[tuple[str, str], dict[str, float]] = {}
    for (team, game_id), members in by_team_game.items():
        state["team_games"][team].append((game_day, game_id))
        pace = team_game_rate(members, "pace")
        if pace:
            state["team_pace"][team].append(pace)
            state["league_pace"].append(pace)
        totals[(team, game_id)] = {
            stat: sum(dec.opportunities(r, stat) or 0.0 for r in members if _num(r.get("min")) > 0)
            for stat in STATS
        }
        for row in members:
            pid = str(row.get("player_id") or "")
            if not pid:
                continue
            state["player_team"][pid] = team
            if _num(row.get("min")) > 0:
                state["hist"][pid].append(row)
                state["appearances"][pid].add(game_id)
    # What each team allowed is what its opponent produced in the same game.
    teams_by_game = defaultdict(list)
    for team, game_id in totals:
        teams_by_game[game_id].append(team)
    for game_id, teams in teams_by_game.items():
        if len(teams) != 2:
            continue
        a, b = teams
        for stat in STATS:
            state["allowed"][(a, stat)].append(totals[(b, game_id)][stat])
            state["allowed"][(b, stat)].append(totals[(a, game_id)][stat])
            state["league_allowed"][stat].extend([totals[(a, game_id)][stat], totals[(b, game_id)][stat]])


def _rotation(team, state) -> dict[str, list[dict]]:
    recent = [gid for _, gid in state["team_games"].get(team, [])[-cm.ROTATION_LOOKBACK:]]
    members = {}
    for pid, team_of in state["player_team"].items():
        if team_of != team or not recent:
            continue
        hits = [gid for gid in recent if gid in state["appearances"].get(pid, set())]
        if len(hits) < cm.ROTATION_MIN_GAMES:
            continue
        rows = state["hist"][pid][-len(hits):]
        if sum(_num(r.get("min")) for r in rows) / len(rows) >= cm.ROTATION_MIN_MINUTES:
            members[pid] = rows
    return members


def _emit(season, game_day, team, members, state, last_season_rate) -> list[dict]:
    history = state["team_games"].get(team) or []
    rest_days = 4.0
    if history:
        rest_days = min(4.0, float((date.fromisoformat(game_day) - date.fromisoformat(history[-1][0])).days))
    present = {str(r.get("player_id")) for r in members if _num(r.get("min")) > 0}
    rotation = _rotation(team, state)

    out = []
    for row in members:
        pid = str(row.get("player_id") or "")
        minutes = _num(row.get("min"))
        past = state["hist"].get(pid) or []
        if minutes <= 0 or len(past) < dec.MIN_HISTORY:
            continue
        recent_min = [_num(r.get("min")) for r in past[-10:]]
        if sum(recent_min) / len(recent_min) < dec.MIN_PROJECTED_MINUTES:
            continue
        opponent = str(row.get("opponent_team_id") or "")
        pace_gap = _gap(state["team_pace"].get(opponent, []), state["league_pace"])
        usage = [_num(r.get("usage_percentage")) for r in past if _num(r.get("usage_percentage")) > 0]
        usage_delta = 0.0
        if len(usage) >= 3:
            season_usage = sum(usage) / len(usage)
            usage_delta = cm.ewm(usage[-10:], 3.0) / season_usage - 1.0 if season_usage > 0 else 0.0
        touches = [t for t in (_touches(r) for r in past) if t is not None]
        touches_delta = 0.0
        if len(touches) >= 3:
            season_touches = sum(touches) / len(touches)
            touches_delta = cm.ewm(touches[-10:], 3.0) / season_touches - 1.0 if season_touches > 0 else 0.0

        for stat in STATS:
            opp = dec.opportunities(row, stat)
            if opp is None:
                continue
            valid = [r for r in past if dec.opportunities(r, stat) is not None]
            window = valid[-dec.RATE_WINDOW:]
            if not window or sum(_num(r.get("min")) for r in window) <= 0:
                continue
            base = _ratio(window, stat)
            if base <= 0:
                continue
            rates = [dec.opportunities(r, stat) / _num(r.get("min")) for r in window]
            has_last = (pid, stat) in last_season_rate

            absent_volume = 0.0
            present_volume = 0.0
            for mate, mate_rows in rotation.items():
                mate_valid = [r for r in mate_rows if dec.opportunities(r, stat) is not None]
                if not mate_valid:
                    continue
                volume = sum(dec.opportunities(r, stat) for r in mate_valid) / len(mate_valid)
                if mate in present:
                    present_volume += volume
                else:
                    absent_volume += volume
            own_volume = base * (sum(recent_min) / len(recent_min))
            share = own_volume / max(present_volume, own_volume, 1e-6)

            allowed_gap = _gap(
                state["allowed"].get((opponent, stat), []), state["league_allowed"].get(stat, []),
            )
            features = {
                "base_rate": base,
                "ewm_rate": cm.ewm(rates, 5.0),
                "last5_rate": _ratio(window[-5:], stat),
                "season_rate": _ratio(valid, stat),
                "last_season_rate": last_season_rate.get((pid, stat), base),
                "has_last_season": 1.0 if has_last else 0.0,
                "usage_delta_x_rate": usage_delta * base,
                "touches_delta_x_rate": touches_delta * base,
                "pace_gap_x_rate": pace_gap * base,
                "allowed_gap_x_rate": allowed_gap * base,
                "b2b": 1.0 if rest_days <= 1 else 0.0,
                # Absent teammates' usual volume, at this player's share, per expected minute.
                "absent_share": absent_volume * share / (sum(recent_min) / len(recent_min)),
            }
            out.append({
                "season": season,
                "game_date": game_day,
                "game_id": str(row.get("game_id")),
                "player_id": pid,
                "stat": stat,
                "role": "starter" if sum(recent_min) / len(recent_min) >= 28 else "bench",
                "minutes": minutes,
                "opportunities": opp,
                "actual_rate": opp / minutes,
                "rates": rates,
                "features": features,
            })
    return out


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class WeightedRidge:
    """Ridge on per-minute rate, weighted by minutes so short stints count less."""

    def __init__(self, names, lam):
        self.names = tuple(names)
        self.lam = lam

    def _x(self, rows):
        return np.array([[r["features"][n] for n in self.names] for r in rows])

    def fit(self, rows):
        x = self._x(rows)
        y = np.array([r["actual_rate"] for r in rows])
        w = np.array([r["minutes"] for r in rows])
        self.mu = np.average(x, axis=0, weights=w)
        self.sd = np.sqrt(np.average((x - self.mu) ** 2, axis=0, weights=w))
        self.sd[self.sd == 0] = 1.0
        z = (x - self.mu) / self.sd
        self.intercept = float(np.average(y, weights=w))
        zw = z * w[:, None]
        gram = z.T @ zw + self.lam * w.mean() * np.eye(len(self.names))
        self.beta = np.linalg.solve(gram, zw.T @ (y - self.intercept))
        return self

    def predict(self, rows):
        z = (self._x(rows) - self.mu) / self.sd
        return np.clip(self.intercept + z @ self.beta, 0.0, None)

    def describe(self):
        return {n: float(b / s) for n, b, s in zip(self.names, self.beta, self.sd)}


def split(rows):
    return cm.split(rows)


def _opp_mae(rows, rates) -> float:
    pred = np.array([r["minutes"] for r in rows]) * np.asarray(rates)
    return float(np.mean(np.abs(pred - np.array([r["opportunities"] for r in rows]))))


def fit_stat(fit, select) -> dict:
    best_hl = min(HALF_LIVES, key=lambda h: _opp_mae(select, [cm.ewm(r["rates"], h) for r in select]))

    def best_ridge(names):
        models = [WeightedRidge(names, lam).fit(fit) for lam in LAMBDAS]
        return min(models, key=lambda m: _opp_mae(select, m.predict(select)))

    clean = best_ridge(CLEAN_FEATURES)
    absence = best_ridge(CLEAN_FEATURES + ABSENCE_FEATURES)
    ablations = {}
    for group, names in GROUPS.items():
        kept = tuple(f for f in CLEAN_FEATURES if f not in names)
        ablations[group] = WeightedRidge(kept, clean.lam).fit(fit)
    selected, dropped = select_groups(fit, select, clean.lam)
    return {
        "half_life": best_hl,
        "clean": clean,
        "absence": absence,
        "selected": selected,
        "dropped_groups": dropped,
        "ablations": ablations,
        "predict": {
            "ratio20": lambda rows: np.array([r["features"]["base_rate"] for r in rows]),
            "ewm": lambda rows: np.array([cm.ewm(r["rates"], best_hl) for r in rows]),
            "ridge_clean": clean.predict,
            "ridge_selected": selected.predict,
            "ridge_absence": absence.predict,
        },
    }


def select_groups(fit, select, lam):
    """
    Backward elimination on the select split only: drop the feature group whose
    removal lowers November error the most, until no removal helps.
    """
    groups = list(GROUPS)

    def model_for(active):
        names = BASE_FEATURES + tuple(f for g in active for f in GROUPS[g])
        return WeightedRidge(names, lam).fit(fit)

    current = model_for(groups)
    current_mae = _opp_mae(select, current.predict(select))
    dropped = []
    while groups:
        trials = []
        for g in groups:
            active = [x for x in groups if x != g]
            m = model_for(active)
            trials.append((_opp_mae(select, m.predict(select)), g, m))
        mae, g, m = min(trials, key=lambda t: t[0])
        if mae >= current_mae - 1e-6:
            break
        groups.remove(g)
        dropped.append(g)
        current, current_mae = m, mae
    return current, dropped


def date_ci(rows, base_rates, new_rates, seed: int = 5) -> dict:
    minutes = np.array([r["minutes"] for r in rows])
    actual = np.array([r["opportunities"] for r in rows])
    diff = np.abs(minutes * new_rates - actual) - np.abs(minutes * base_rates - actual)
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
    return {"mae_diff": float(diff.mean()), "ci95": [draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]]}


def combined(stat_check, fitted, chain_index, minutes_pred) -> dict:
    """Stat MAE for minutes model x volume model, conversion held at the chain forecast."""
    pairs = []
    for i, r in enumerate(stat_check):
        chain = chain_index.get((r["player_id"], r["game_id"], r["stat"]))
        mins = minutes_pred.get((r["player_id"], r["game_id"]))
        if chain is not None and mins is not None:
            pairs.append((i, chain, mins))
    if not pairs:
        return {"n": 0}
    idx = [i for i, _, _ in pairs]
    actual = np.array([c["actual"] for _, c, _ in pairs])
    conv = np.array([c["forecast"]["conversion"] for _, c, _ in pairs])
    rows = [stat_check[i] for i in idx]
    out = {"n": len(pairs)}
    for vol_name in ("ratio20", "ridge_selected"):
        rates = fitted["predict"][vol_name](rows)
        for min_name in ("last10", "ridge_clean"):
            mins = np.array([m[min_name] for _, _, m in pairs])
            out[f"min_{min_name}+vol_{vol_name}"] = float(np.mean(np.abs(mins * rates * conv - actual)))
    return out


def build_report(rows, chain_rows, minute_rows) -> dict:
    fit_m, select_m, check_m = cm.split(minute_rows)
    minute_models = cm.fit_models(fit_m, select_m)
    minutes_pred = {}
    preds = {name: minute_models["predict"][name](check_m) for name in ("last10", "ridge_clean")}
    for i, r in enumerate(check_m):
        minutes_pred[(r["player_id"], r["game_id"])] = {name: float(p[i]) for name, p in preds.items()}
    chain_index = {(r["player_id"], r["game_id"], r["stat"]): r for r in chain_rows}

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "splits": {"fit": "2024-25 and 2025-10", "select": "2025-11", "check": "2025-12"},
        "caveats": [
            "Volume is scored with actual minutes held fixed, so minutes error does not count against it.",
            "ridge_absence uses who was missing from the box score; it is an upper bound.",
            "Opponent features use only that opponent's earlier 2025-26 (or 2024-25) games, shrunk toward the league.",
            "Assist volume uses passes; potential assists are not available per game.",
        ],
        "stats": {},
    }
    for stat in STATS:
        stat_rows = [r for r in rows if r["stat"] == stat]
        fit, select, check = split(stat_rows)
        fitted = fit_stat(fit, select)
        base = fitted["predict"]["ratio20"](check)
        scores = {}
        for name, fn in fitted["predict"].items():
            rates = fn(check)
            scores[name] = {
                "select_opp_mae": _opp_mae(select, fn(select)),
                "check_opp_mae": _opp_mae(check, rates),
                "check_vs_ratio20": date_ci(check, base, rates) if name != "ratio20" else None,
            }
        clean_mae = scores["ridge_clean"]["check_opp_mae"]
        ablation = {
            group: _opp_mae(check, model.predict(check)) - clean_mae
            for group, model in fitted["ablations"].items()
        }
        report["stats"][stat] = {
            "n": {"fit": len(fit), "select": len(select), "check": len(check)},
            "ewm_half_life": fitted["half_life"],
            "ridge_lambda": {"clean": fitted["clean"].lam, "absence": fitted["absence"].lam},
            "selected_dropped_groups_on_november": fitted["dropped_groups"],
            "scores": scores,
            "ablation_mae_increase_when_removed": ablation,
            "coefficients": {
                "clean": fitted["clean"].describe(),
                "selected": fitted["selected"].describe(),
                "absence": fitted["absence"].describe(),
            },
            "combined_stat_mae": combined(check, fitted, chain_index, minutes_pred),
        }
    return report


def main() -> None:
    from engine.backtest.advanced_history import load_historical_logs

    logs, _ = load_historical_logs()
    rows = build_rows(logs)
    report = build_report(rows, dec.build_rows(logs), cm.build_rows(logs))
    REPORT_PATH.write_text(json.dumps(report, indent=1), encoding="utf-8")

    for stat, e in report["stats"].items():
        print(f"\n{stat}  (n check {e['n']['check']}, ewm half-life {e['ewm_half_life']})")
        for name, s in e["scores"].items():
            ci = s["check_vs_ratio20"]
            ci_text = f"{ci['mae_diff']:+.3f} [{ci['ci95'][0]:+.3f}, {ci['ci95'][1]:+.3f}]" if ci else ""
            print(f"  {name:<14} Nov {s['select_opp_mae']:.3f}  Dec {s['check_opp_mae']:.3f}  {ci_text}")
        print("  removed group -> Dec MAE change:", {k: round(v, 4) for k, v in e["ablation_mae_increase_when_removed"].items()})
        print("  combined stat MAE:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in e["combined_stat_mae"].items()})
    print(f"\nSaved {REPORT_PATH}")


if __name__ == "__main__":
    main()
