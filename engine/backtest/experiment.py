"""
January-fit interaction experiment. February is validation. March is not opened.

Production weights are not changed, and Model E is not installed over Model A.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from engine.backtest.expanded import score_lines
from engine.backtest.interaction import (
    MODEL_D_STRENGTH,
    adjusted_probability,
    apply_scale,
    blend_toward,
    interaction_features,
    matchup_band,
    matchup_gaps,
    select_lambda,
    usage_band,
)
from engine.backtest.metrics import (
    brier_and_log_loss,
    calibration_bins,
    clustered_scores,
    expected_roi,
    group_metric,
    paired_brier_difference,
)
from engine.backtest.odds_history import (
    SAMPLE_MARKETS,
    SAMPLE_REGION,
    estimate_event_odds_cost,
    fetch_historical_event_odds,
    fetch_historical_events,
    odds_snapshot_cached,
    snapshot_is_pregame,
    snapshot_target,
)
from engine.backtest.profiles import build_players_asof
from engine.backtest.runner import _bdl_games, _match_bdl_teams
from engine.backtest.variants import VARIANT_A, VARIANT_B, VARIANT_D, VARIANT_E
from engine.backtest_grade import realized_roi

CREDIT_APPROVAL_LIMIT = 1500
FEBRUARY_DATES = (
    date(2026, 2, 2), date(2026, 2, 4), date(2026, 2, 6), date(2026, 2, 9),
    date(2026, 2, 11), date(2026, 2, 13), date(2026, 2, 19), date(2026, 2, 21),
    date(2026, 2, 23), date(2026, 2, 25),
)
FEBRUARY_BACKUP = (date(2026, 2, 27), date(2026, 2, 1), date(2026, 2, 3), date(2026, 2, 8))
_REPORT = Path("data/backtests/interaction_experiment.json")
_JANUARY = Path("data/backtests/expanded_report.json")


def estimate_february_cost(n_dates: int = 10, n_games: int = 30) -> dict:
    odds = estimate_event_odds_cost(n_games, SAMPLE_MARKETS, SAMPLE_REGION)
    total = n_dates + odds
    return {
        "events_list_credits": n_dates,
        "event_odds_credits": odds,
        "total_credits": total,
        "n_dates": n_dates,
        "n_games": n_games,
        "approval_limit": CREDIT_APPROVAL_LIMIT,
        "march_not_included": True,
    }


def select_february(events_by_date: list[tuple[date, list[dict]]], max_games: int = 30) -> list[dict]:
    """Three games on each date until 30, with a team capped at three appearances."""
    chosen: list[dict] = []
    team_counts: dict[str, int] = defaultdict(int)
    for day, events in events_by_date:
        day_teams: set[str] = set()
        picked = 0
        for event in sorted(events, key=lambda item: str(item.get("commence_time") or "")):
            if len(chosen) >= max_games or picked >= 3:
                break
            home = str(event.get("home_team") or "")
            away = str(event.get("away_team") or "")
            if not home or not away or home in day_teams or away in day_teams:
                continue
            if team_counts[home] >= 3 or team_counts[away] >= 3:
                continue
            chosen.append({**event, "slate_date": day.isoformat()})
            day_teams.update((home, away))
            team_counts[home] += 1
            team_counts[away] += 1
            picked += 1
    return chosen


def _query_at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 18, 0, tzinfo=timezone.utc)


def _remaining(quota: dict | None) -> int | None:
    try:
        return int(str((quota or {}).get("remaining")))
    except (TypeError, ValueError):
        return None


def run_interaction_experiment() -> dict:
    from engine.backtest.advanced_history import load_historical_logs

    logs, coverage = load_historical_logs()
    january_events = _january_events()
    january_rows = _score_events(january_events, logs, period="train")
    fits = _fit_by_prop(january_rows)
    usage_cuts = _usage_cuts(january_rows)

    listed, quota = _list_dates(FEBRUARY_DATES)
    selected = select_february(listed, 30)
    if len(selected) < 30:
        extra, quota = _list_dates(FEBRUARY_BACKUP, quota)
        listed.extend(extra)
        selected = select_february(listed, 30)
    pending = [event for event in selected if not _odds_cached(event)]
    cost = estimate_february_cost(len(listed), len(selected))
    cold_odds = estimate_event_odds_cost(len(pending), SAMPLE_MARKETS, SAMPLE_REGION)
    if cost["total_credits"] > CREDIT_APPROVAL_LIMIT:
        report = _stopped(fits, usage_cuts, coverage, cost, "needs_approval", (
            f"February estimate is {cost['total_credits']} credits, above {CREDIT_APPROVAL_LIMIT}. "
            "No February prices were downloaded. March was not opened."
        ))
        _write(report, [])
        return report
    remaining = _remaining(quota)
    if remaining is None and pending:
        remaining = _remaining(fetch_historical_events(_query_at(listed[0][0]), use_cache=False).get("quota"))
    if pending and remaining is not None and remaining < cold_odds:
        report = _stopped(fits, usage_cuts, coverage, cost, "insufficient_quota", (
            f"Remaining credits {remaining} are below the {cold_odds} needed for uncached February games."
        ))
        _write(report, [])
        return report

    february_rows = _score_events(selected, logs, period="validation")
    _apply_d_and_e(january_rows, fits)
    _apply_d_and_e(february_rows, fits)
    report = _report(january_rows, february_rows, fits, usage_cuts, coverage, cost, cold_odds)
    _write(report, february_rows)
    return report


def _january_events() -> list[dict]:
    if not _JANUARY.exists():
        return []
    payload = json.loads(_JANUARY.read_text(encoding="utf-8"))
    events = []
    for game in (payload.get("report") or {}).get("coverage", {}).get("games") or []:
        if game.get("status") != "scored":
            continue
        events.append({
            "id": game.get("event_id"),
            "home_team": game.get("home_team"),
            "away_team": game.get("away_team"),
            "commence_time": game.get("commence_time"),
            "slate_date": game.get("slate_date"),
        })
    return events


def _list_dates(days: tuple[date, ...], quota: dict | None = None) -> tuple[list, dict | None]:
    listed = []
    for day in days:
        payload = fetch_historical_events(_query_at(day))
        quota = payload.get("quota") or quota
        listed.append((day, payload.get("events") or []))
    return listed, quota


def _odds_cached(event: dict) -> bool:
    tip = datetime.fromisoformat(str(event["commence_time"]).replace("Z", "+00:00"))
    return odds_snapshot_cached(str(event.get("id")), snapshot_target(tip))


def _score_events(events: list[dict], logs: list[dict], period: str) -> list[dict]:
    from engine.backtest.expanded import _bdl_game_date, _find_bdl_game
    from engine.backtest.profiles import build_defense_asof

    rows: list[dict] = []
    for event in events:
        if not event.get("commence_time") or not event.get("id"):
            continue
        tip = datetime.fromisoformat(str(event["commence_time"]).replace("Z", "+00:00"))
        odds_at = snapshot_target(tip)
        payload = fetch_historical_event_odds(str(event["id"]), odds_at)
        if not payload.get("available"):
            continue
        returned = payload.get("timestamp")
        if returned and not snapshot_is_pregame(
            datetime.fromisoformat(str(returned).replace("Z", "+00:00")), tip,
        ):
            continue
        slate = date.fromisoformat(str(event["slate_date"]))
        games = _bdl_games(slate) + _bdl_games(slate + timedelta(days=1))
        home_id, away_id = _match_bdl_teams(event, games)
        game_day = _bdl_game_date(_find_bdl_game(event, games)) or slate
        if not home_id or not away_id:
            continue
        players, quality = build_players_asof(logs, game_day)
        home_defense, home_quality = build_defense_asof(logs, game_day, home_id)
        away_defense, away_quality = build_defense_asof(logs, game_day, away_id)
        final_logs = [
            row for row in logs
            if str(row.get("game_id") or "") == str((_find_bdl_game(event, games) or {}).get("id") or "")
        ]
        from engine.backtest.line_rule import select_evaluation_lines
        chosen, alternates = select_evaluation_lines(payload.get("lines") or [])
        home_gaps = matchup_gaps(logs, game_day, home_id)
        away_gaps = matchup_gaps(logs, game_day, away_id)
        for line_group, is_eval in ((chosen, True), (alternates, False)):
            scored = score_lines(
                event=event,
                game_day=game_day,
                lines=line_group,
                players=players,
                player_quality=quality,
                home_team_id=home_id,
                away_team_id=away_id,
                home_defense=home_defense,
                away_defense=away_defense,
                home_quality=home_quality,
                away_quality=away_quality,
                final_logs=final_logs,
            )
            features: dict[tuple, dict] = {}
            for row in scored:
                row["period"] = period
                row["evaluation_line"] = is_eval
                if row.get("variant") != VARIANT_A:
                    continue
                player = players.get(row["player_id"])
                if player is None:
                    continue
                gaps = away_gaps if player.team_id == home_id else home_gaps
                features[(row["player_id"], row["prop_type"])] = interaction_features(
                    gaps, player, row["prop_type"],
                )
            for row in scored:
                row.update(features.get((row["player_id"], row["prop_type"])) or {})
            rows.extend(scored)
        print(f"{period} {event.get('away_team')} at {event.get('home_team')}", flush=True)
    return rows


def _fit_by_prop(rows: list[dict]) -> dict:
    fits = {}
    for prop in ("points", "rebounds", "threes"):
        sample = [
            row for row in rows
            if row.get("variant") == VARIANT_A
            and row.get("evaluation_line")
            and row.get("side") == "over"
            and row.get("prop_type") == prop
            and row.get("input_complete")
            and row.get("identified")
            and row.get("result") in ("win", "loss")
        ]
        prepared = []
        for row in sample:
            prepared.append({
                "game_date": row["game_date"],
                "outcome": row["outcome"],
                "p_a": row["raw_probability"],
                "x_volume": row.get("x_volume") or 0.0,
                "x_efficiency": row.get("x_efficiency") or 0.0,
            })
        fits[prop] = select_lambda(prepared)
        coef = fits[prop]["coefficients"]
        fits[prop]["coefficients"] = [float(coef[0]), float(coef[1])]
    fits["assists"] = {
        "lambda": None,
        "coefficients": [0.0, 0.0],
        "scale": {"x_volume": 1.0, "x_efficiency": 1.0},
        "reason": (
            "Game-level potential assists are not on the advanced box score. "
            "The passing endpoint's potential_ast is a season total. The 2025-26 total "
            "includes games after the cutoff, so it was not used. No assist interaction was fit."
        ),
        "cv": [],
    }
    return fits


def _apply_d_and_e(rows: list[dict], fits: dict) -> None:
    groups: dict[tuple, dict] = defaultdict(dict)
    for row in rows:
        if row.get("variant") not in (VARIANT_A, VARIANT_B):
            continue
        key = (
            row.get("game_id"), row.get("player_id"), row.get("prop_type"),
            row.get("line"), row.get("side"), row.get("evaluation_line"),
        )
        groups[key][row["variant"]] = row
    extra = []
    for pair in groups.values():
        base = pair.get(VARIANT_A)
        matchup = pair.get(VARIANT_B)
        if base is None or matchup is None:
            continue
        model_d = dict(base)
        model_d["variant"] = VARIANT_D
        model_d["raw_probability"] = blend_toward(
            float(base["raw_probability"]), float(matchup["raw_probability"]), MODEL_D_STRENGTH,
        )
        model_d["projected"] = blend_toward(
            float(base["projected"]), float(matchup["projected"]), MODEL_D_STRENGTH,
        )
        model_d["edge_raw"] = model_d["raw_probability"] - float(base["implied_probability"])
        _refresh_roi(model_d)
        extra.append(model_d)

        fit = fits.get(base["prop_type"]) or fits["assists"]
        scale = fit.get("scale") or {"x_volume": 1.0, "x_efficiency": 1.0}
        coef = tuple(fit.get("coefficients") or (0.0, 0.0))
        x1, x2 = apply_scale(base.get("x_volume") or 0.0, base.get("x_efficiency") or 0.0, scale)
        model_e = dict(base)
        model_e["variant"] = VARIANT_E
        if base.get("prop_type") == "assists" or not base.get("identified"):
            model_e["raw_probability"] = float(base["raw_probability"])
            model_e["interaction_applied"] = False
        else:
            model_e["raw_probability"] = adjusted_probability(float(base["raw_probability"]), x1, x2, coef)
            model_e["interaction_applied"] = True
        model_e["edge_raw"] = model_e["raw_probability"] - float(base["implied_probability"])
        model_e["x_volume_scaled"] = x1
        model_e["x_efficiency_scaled"] = x2
        _refresh_roi(model_e)
        extra.append(model_e)
    rows.extend(extra)


def _refresh_roi(row: dict) -> None:
    if row.get("result") in ("win", "loss", "push"):
        row["expected_roi"] = expected_roi(float(row["raw_probability"]), int(row["american_odds"]))
        row["realized_roi"] = realized_roi(None, int(row["american_odds"]), row["result"])


def _usage_cuts(rows: list[dict]) -> dict:
    usage = sorted(
        float(row.get("usage") or 0)
        for row in rows
        if row.get("variant") == VARIANT_A and row.get("evaluation_line") and row.get("side") == "over"
    )
    if len(usage) < 3:
        return {"low": 0.18, "high": 0.25}
    return {"low": usage[len(usage) // 3], "high": usage[(2 * len(usage)) // 3]}


def _eval_rows(rows: list[dict], variant: str | None = None) -> list[dict]:
    chosen = [
        row for row in rows
        if row.get("evaluation_line") and row.get("side") == "over"
        and row.get("prop_type") in ("points", "rebounds", "threes")
        and row.get("input_complete")
    ]
    if variant:
        chosen = [row for row in chosen if row.get("variant") == variant]
    return chosen


def _favorite(rows: list[dict], variant: str) -> dict:
    sample = [
        row for row in rows
        if row.get("variant") == variant
        and row.get("result") in ("win", "loss", "push")
        and -600 <= int(row.get("american_odds") or 0) <= -200
    ]
    return _roi_block(sample)


def _roi_block(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    realized = [float(row["realized_roi"]) for row in rows if row.get("realized_roi") is not None]
    expected = [float(row["expected_roi"]) for row in rows if row.get("expected_roi") is not None]
    by_game: dict[str, list[float]] = defaultdict(list)
    by_player: dict[tuple, list[float]] = defaultdict(list)
    by_date: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get("realized_roi") is None:
            continue
        profit = float(row["realized_roi"])
        by_game[str(row.get("game_id"))].append(profit)
        by_player[(row.get("game_id"), row.get("player_id"), row.get("prop_type"))].append(profit)
        by_date[str(row.get("game_date"))].append(profit)
    from engine.backtest.metrics import _standard_error
    game_means = [sum(values) / len(values) for values in by_game.values()]
    player_means = [sum(values) / len(values) for values in by_player.values()]
    date_means = [sum(values) / len(values) for values in by_date.values()]
    return {
        "n_lines": len(rows),
        "n_player_game_stats": len(player_means),
        "n_games": len(game_means),
        "n_dates": len(date_means),
        "expected_roi": (sum(expected) / len(expected)) if expected else None,
        "realized_roi": (sum(realized) / len(realized)) if realized else None,
        "player_cluster_se": _standard_error(player_means),
        "game_cluster_se": _standard_error(game_means),
        "date_cluster_se": _standard_error(date_means),
    }


def _overconfidence(rows: list[dict], variant: str) -> dict:
    sample = [
        row for row in _eval_rows(rows, variant)
        if row.get("result") in ("win", "loss") and 0.70 <= float(row["raw_probability"]) < 0.90
    ]
    if not sample:
        return {"n": 0}
    predicted = sum(float(row["raw_probability"]) for row in sample) / len(sample)
    actual = sum(int(row["outcome"]) for row in sample) / len(sample)
    return {"n": len(sample), "predicted_avg": predicted, "actual_rate": actual, "gap": predicted - actual}


def _report(january, february, fits, usage_cuts, coverage, cost, cold_odds) -> dict:
    validation = _eval_rows(february)
    variants = {}
    for name in (VARIANT_A, VARIANT_B, VARIANT_D, VARIANT_E):
        subset = [row for row in validation if row.get("variant") == name]
        variants[name] = {
            **clustered_scores(subset),
            "log_loss": brier_and_log_loss(subset).get("log_loss"),
            "by_prop": group_metric(subset, "prop_type"),
            "by_usage": group_metric(_labeled(subset, usage_cuts), "usage_band"),
            "by_matchup": group_metric(_labeled(subset, usage_cuts), "matchup_band"),
            "both_favorable": clustered_scores([
                row for row in _labeled(subset, usage_cuts)
                if row.get("usage_band") == "high" and row.get("matchup_band") == "favorable"
            ]),
            "favorite_band": _favorite(
                [row for row in february if row.get("evaluation_line")], name,
            ),
            "alternate_favorite_band": _favorite(
                [row for row in february if not row.get("evaluation_line")], name,
            ),
            "overconfidence_70_90": _overconfidence(february, name),
            "calibration": calibration_bins(subset),
        }
    book_rows = [row for row in validation if row.get("variant") == VARIANT_A]
    book = clustered_scores(book_rows, "novig_probability")
    book["log_loss"] = brier_and_log_loss(book_rows, "novig_probability").get("log_loss")
    return {
        "production_change": {
            "applied": False,
            "reason": (
                "Model E was not installed. A production change requires February and the "
                "untouched March window. March was not opened."
            ),
        },
        "fits": fits,
        "usage_cuts_from_january": usage_cuts,
        "january_games": len({row.get("game_id") for row in january}),
        "february": {
            "n_games": len({row.get("game_id") for row in february}),
            "n_dates": len({row.get("game_date") for row in february}),
            "variants": variants,
            "book_novig": book,
            "e_minus_a": paired_brier_difference(validation, VARIANT_A, VARIANT_E),
            "d_minus_a": paired_brier_difference(validation, VARIANT_A, VARIANT_D),
            "b_minus_a": paired_brier_difference(validation, VARIANT_A, VARIANT_B),
        },
        "coverage": {
            "estimated_cost": cost,
            "uncached_odds_credits": cold_odds,
            "feature_rows": coverage.get("joined_rows"),
            "potential_assists": fits["assists"]["reason"],
            "march": "Not downloaded. Parameters were not selected from March.",
            "model_d": f"Prespecified at {MODEL_D_STRENGTH:.0%} of the Model B probability gap.",
        },
    }


def _labeled(rows: list[dict], cuts: dict) -> list[dict]:
    labeled = []
    for row in rows:
        copy = dict(row)
        copy["usage_band"] = usage_band(float(row.get("usage") or 0), cuts["low"], cuts["high"])
        copy["matchup_band"] = matchup_band(float(row.get("volume_gap") or 0))
        labeled.append(copy)
    return labeled


def _stopped(fits, usage_cuts, coverage, cost, status, reason) -> dict:
    return {
        "production_change": {"applied": False, "reason": reason},
        "fits": fits,
        "usage_cuts_from_january": usage_cuts,
        "february": {"n_games": 0, "variants": {}},
        "coverage": {
            "historical_odds": status,
            "reason": reason,
            "estimated_cost": cost,
            "feature_rows": coverage.get("joined_rows"),
            "march": "Not downloaded.",
        },
    }


def _write(report: dict, february_rows: list[dict]) -> None:
    _REPORT.parent.mkdir(parents=True, exist_ok=True)
    keep = [
        row for row in february_rows
        if row.get("evaluation_line") or (
            row.get("variant") in (VARIANT_A, VARIANT_E) and row.get("side") == "over"
        )
    ]
    _REPORT.write_text(json.dumps({"report": report, "rows": keep}, indent=2, default=str), encoding="utf-8")


def load_interaction_report(path: Path = _REPORT) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
