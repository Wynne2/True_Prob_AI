"""
Thirty-game walk-forward. Production weights are not written.

The evaluation line is chosen from the prices before any model runs.
Alternate lines are stored and are not extra observations.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from domain.enums import PropSide, PropType
from engine.backtest.line_rule import no_vig_probability, select_evaluation_lines
from engine.backtest.metrics import (
    brier_and_log_loss,
    calibration_bins,
    clustered_scores,
    compare_variants,
    expected_roi,
    group_metric,
    mean_absolute_error,
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
from engine.backtest.profiles import build_defense_asof, build_players_asof
from engine.backtest.runner import _actual, _bdl_games, _match_bdl_teams, _models
from engine.backtest.variants import VARIANT_A, VARIANT_B, VARIANT_C, variant_inputs
from engine.backtest_grade import grade_prop, realized_roi
from engine.market_calibration import calibrate_true_probability
from engine.prop_evaluator import _true_prob
from odds.implied_probability import raw_implied_prob_for_side
from utils.math_helpers import clamp

CREDIT_APPROVAL_LIMIT = 1500
PRIMARY_DATES = (
    date(2026, 1, 5),
    date(2026, 1, 8),
    date(2026, 1, 12),
    date(2026, 1, 15),
    date(2026, 1, 19),
    date(2026, 1, 22),
    date(2026, 1, 26),
    date(2026, 1, 29),
)
BACKUP_DATES = (
    date(2026, 2, 2),
    date(2026, 2, 5),
    date(2026, 2, 9),
    date(2026, 2, 12),
)
_PER_DATE = (4, 4, 4, 4, 4, 3, 4, 3)
_REPORT_PATH = Path("data/backtests/expanded_report.json")
_COMPLETE_PROPS = {PropType.POINTS.value, PropType.REBOUNDS.value, PropType.THREES.value}


def estimate_expanded_cost(n_dates: int, n_games: int) -> dict:
    odds = estimate_event_odds_cost(n_games, SAMPLE_MARKETS, SAMPLE_REGION)
    listing = n_dates
    return {
        "events_list_credits": listing,
        "event_odds_credits": odds,
        "total_credits": listing + odds,
        "n_dates": n_dates,
        "n_games": n_games,
        "approval_limit": CREDIT_APPROVAL_LIMIT,
        "markets": SAMPLE_MARKETS,
        "regions": SAMPLE_REGION,
    }


def select_cohort(events_by_date: list[tuple[date, list[dict]]], max_games: int = 30) -> list[dict]:
    """
    Spread games across dates and teams. The per-date targets sum to 30.
    A team is capped at three appearances so one club cannot dominate.
    """
    chosen: list[dict] = []
    team_counts: dict[str, int] = defaultdict(int)
    for index, (day, events) in enumerate(events_by_date):
        target = _PER_DATE[index] if index < len(_PER_DATE) else 4
        day_teams: set[str] = set()
        ordered = sorted(events, key=lambda event: str(event.get("commence_time") or ""))
        picked = 0
        for event in ordered:
            if len(chosen) >= max_games or picked >= target:
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


def run_expanded_backtest(max_games: int = 30, credit_cap: int = CREDIT_APPROVAL_LIMIT) -> dict:
    dates = list(PRIMARY_DATES)
    listed: list[tuple[date, list[dict]]] = []
    quota = None
    for day in dates:
        payload = fetch_historical_events(_query_at(day))
        quota = payload.get("quota") or quota
        if not payload.get("available"):
            report = _empty_report({
                "historical_odds": "unavailable",
                "reason": payload.get("reason"),
                "estimated_cost": estimate_expanded_cost(len(dates), max_games),
            })
            _write(report, [], [])
            return report
        events = [
            event for event in (payload.get("events") or [])
            if event.get("commence_time")
        ]
        listed.append((day, events))

    selected = select_cohort(listed, max_games=max_games)
    if len(selected) < max_games:
        for day in BACKUP_DATES:
            if len(selected) >= max_games:
                break
            extra_cost = estimate_expanded_cost(len(listed) + 1, max_games)
            if extra_cost["total_credits"] > credit_cap:
                break
            payload = fetch_historical_events(_query_at(day))
            quota = payload.get("quota") or quota
            listed.append((day, payload.get("events") or []))
            selected = select_cohort(listed, max_games=max_games)

    pending = [
        event for event in selected
        if not odds_snapshot_cached(
            str(event.get("id")),
            snapshot_target(datetime.fromisoformat(str(event["commence_time"]).replace("Z", "+00:00"))),
        )
    ]
    cost = estimate_expanded_cost(len(listed), len(selected))
    cold_odds = estimate_event_odds_cost(len(pending), SAMPLE_MARKETS, SAMPLE_REGION)
    if cost["total_credits"] > credit_cap:
        report = _empty_report({
            "historical_odds": "needs_approval",
            "reason": (
                f"Estimated cost is {cost['total_credits']} credits, above {credit_cap}. "
                "No prop prices were downloaded."
            ),
            "estimated_cost": cost,
        })
        _write(report, [], [])
        return report

    remaining = _remaining(quota)
    if remaining is None and pending:
        probe = fetch_historical_events(_query_at(listed[0][0]), use_cache=False)
        remaining = _remaining(probe.get("quota"))
    if pending and remaining is not None and remaining < cold_odds:
        report = _empty_report({
            "historical_odds": "insufficient_quota",
            "reason": f"Remaining credits {remaining} are below the {cold_odds} needed for uncached games.",
            "estimated_cost": cost,
        })
        _write(report, [], [])
        return report

    from engine.backtest.advanced_history import load_historical_logs

    logs, feature_coverage = load_historical_logs()
    rows: list[dict] = []
    alternates: list[dict] = []
    game_notes: list[dict] = []
    for event in selected:
        tip = datetime.fromisoformat(str(event["commence_time"]).replace("Z", "+00:00"))
        odds_at = snapshot_target(tip)
        print(
            f"Snapshot {event.get('away_team')} at {event.get('home_team')} {event.get('slate_date')}",
            flush=True,
        )
        odds_payload = fetch_historical_event_odds(str(event["id"]), odds_at)
        note = {
            "event_id": event.get("id"),
            "slate_date": event.get("slate_date"),
            "home_team": event.get("home_team"),
            "away_team": event.get("away_team"),
            "commence_time": event.get("commence_time"),
            "requested_snapshot": odds_at.isoformat(),
            "returned_snapshot": odds_payload.get("timestamp"),
            "cached": bool(odds_payload.get("cached")),
        }
        if not odds_payload.get("available"):
            note["status"] = "odds_unavailable"
            game_notes.append(note)
            continue
        returned = odds_payload.get("timestamp")
        snapshot_dt = odds_at
        if returned:
            snapshot_dt = datetime.fromisoformat(str(returned).replace("Z", "+00:00"))
            if not snapshot_is_pregame(snapshot_dt, tip):
                note["status"] = "snapshot_after_tip"
                game_notes.append(note)
                continue
        slate = date.fromisoformat(str(event["slate_date"]))
        bdl_games = _bdl_games(slate) + _bdl_games(slate + timedelta(days=1))
        home_id, away_id = _match_bdl_teams(event, bdl_games)
        bdl_game = _find_bdl_game(event, bdl_games)
        game_day = _bdl_game_date(bdl_game) or slate
        note["bdl_game_date"] = game_day.isoformat()
        note["home_team_id"] = home_id
        note["away_team_id"] = away_id
        if not home_id or not away_id:
            note["status"] = "team_unmatched"
            game_notes.append(note)
            continue
        players, player_quality = build_players_asof(logs, game_day)
        home_defense, home_quality = build_defense_asof(logs, game_day, home_id)
        away_defense, away_quality = build_defense_asof(logs, game_day, away_id)
        final_logs = [
            row for row in logs
            if str(row.get("game_id") or "") == str((bdl_game or {}).get("id") or "")
        ]
        chosen, held = select_evaluation_lines(odds_payload.get("lines") or [])
        for line in held:
            alternates.append({
                "event_id": event.get("id"),
                "player": line.player_name,
                "prop_type": line.prop_type.value,
                "line": line.line,
                "book": line.book_key,
                "over_odds": line.over_odds,
                "under_odds": line.under_odds,
                "evaluation_line": False,
            })
        rows.extend(score_lines(
            event=event,
            game_day=game_day,
            lines=chosen,
            players=players,
            player_quality=player_quality,
            home_team_id=home_id,
            away_team_id=away_id,
            home_defense=home_defense,
            away_defense=away_defense,
            home_quality=home_quality,
            away_quality=away_quality,
            final_logs=final_logs,
        ))
        note["status"] = "scored"
        note["evaluation_lines"] = len(chosen)
        note["alternate_lines"] = len(held)
        game_notes.append(note)

    report = build_expanded_report(rows, {
        "historical_odds": "available",
        "estimated_cost": cost,
        "uncached_odds_credits": cold_odds,
        "quota_after_events": quota,
        "games_requested": len(selected),
        "games_scored": len({note["event_id"] for note in game_notes if note.get("status") == "scored"}),
        "feature_coverage": feature_coverage,
        "games": game_notes,
        "model_c_status": "incomplete",
        "model_c_reason": (
            "Historical injuries and lineups were not available. "
            "Model C uses measured pace, defensive rating, usage, minutes, and rebound chances. "
            "It does not include teammate-absence redistribution."
        ),
        "potential_assists": (
            "Not present on GET /nba/v2/stats/advanced. Assists rows use the rate prior and "
            "are excluded from the complete-input comparison."
        ),
        "line_rule": (
            "One line per player and prop: the two-sided price closest to 50/50, "
            "chosen before any model ran. The same line is used for A, B, and C."
        ),
    })
    _write(report, rows, alternates)
    return report


def score_lines(
    *,
    event: dict,
    game_day: date,
    lines: list,
    players: dict,
    player_quality: dict,
    home_team_id: str,
    away_team_id: str,
    home_defense,
    away_defense,
    home_quality: dict,
    away_quality: dict,
    final_logs: list[dict],
) -> list[dict]:
    from engine.backtest.asof import game_from_event, match_player

    game = game_from_event(event, game_day)
    finals = {str(row.get("player_id")): row for row in final_logs}
    models = _models()
    rows: list[dict] = []
    for line in lines:
        if line.prop_type not in (PropType.POINTS, PropType.REBOUNDS, PropType.ASSISTS, PropType.THREES):
            continue
        player = match_player(line.player_name, players)
        if player is None:
            continue
        if player.team_id == home_team_id:
            defense, defense_quality = away_defense, away_quality
        elif player.team_id == away_team_id:
            defense, defense_quality = home_defense, home_quality
        else:
            defense, defense_quality = away_defense, away_quality
        actual, status = _actual(finals.get(player.player_id), line.prop_type)
        quality = player_quality.get(player.player_id, {})
        for variant in (VARIANT_A, VARIANT_B, VARIANT_C):
            v_player, v_defense = variant_inputs(player, defense, variant)
            is_home = player.team_id == home_team_id
            projection = models[line.prop_type].project(v_player, game, v_defense, is_home=is_home)
            context = projection.model_context or {}
            mechanism = str(context.get("mechanism") or "")
            attempt_source = str(context.get("attempt_source") or "")
            make_source = str(context.get("make_source") or "")
            silent_fallback = (
                "rate_prior" in mechanism
                or attempt_source == "derived_from_makes"
                or make_source == "league_prior"
            )
            input_complete = line.prop_type.value in _COMPLETE_PROPS and not silent_fallback
            for side, american in (
                (PropSide.OVER, int(line.over_odds)),
                (PropSide.UNDER, int(line.under_odds)),
            ):
                raw = clamp(_true_prob(projection, float(line.line), side), 0.001, 0.999)
                implied = raw_implied_prob_for_side(side.value, int(line.over_odds), int(line.under_odds))
                novig = no_vig_probability(int(line.over_odds), int(line.under_odds), side.value)
                calibrated, _warnings = calibrate_true_probability(raw, implied, american)
                if status != "ok" or actual is None:
                    result = "void" if status == "void" else "pending"
                    outcome = None
                else:
                    result = grade_prop(actual, float(line.line), side.value)
                    outcome = None if result == "push" else (1 if result == "win" else 0)
                rows.append({
                    "variant": variant,
                    "model_c_status": "incomplete" if variant == VARIANT_C else "",
                    "game_id": str(event.get("id") or game.game_id),
                    "game_date": game_day.isoformat(),
                    "player_id": player.player_id,
                    "player": player.name,
                    "position": player.position.value,
                    "position_raw": quality.get("position_raw"),
                    "prop_type": line.prop_type.value,
                    "line": float(line.line),
                    "side": side.value,
                    "book": line.book_key,
                    "american_odds": american,
                    "implied_probability": implied,
                    "novig_probability": novig,
                    "raw_probability": raw,
                    "calibrated_probability": calibrated,
                    "edge_raw": raw - implied,
                    "projected": projection.projected_value,
                    "expected_minutes": projection.expected_minutes,
                    "actual": actual,
                    "result": result,
                    "outcome": outcome,
                    "mechanism": mechanism,
                    "attempt_source": attempt_source,
                    "silent_fallback": silent_fallback,
                    "input_complete": input_complete,
                    "pace": v_defense.pace,
                    "pace_is_fallback": v_defense.pace_is_fallback,
                    "defensive_rating_fallback": bool(defense_quality.get("defensive_rating_fallback")),
                    "usage_source": quality.get("usage_source"),
                    "rebound_chances_source": quality.get("rebound_chances_source"),
                    "fga_source": quality.get("fga_source"),
                    "potential_assists_source": quality.get("potential_assists_source"),
                    "current_games": quality.get("current_games"),
                    "previous_season_games": quality.get("previous_season_games"),
                    "expected_roi": expected_roi(raw, american) if outcome is not None or result == "push" else None,
                    "realized_roi": realized_roi(None, american, result) if result in ("win", "loss", "push") else None,
                    "evaluation_line": True,
                })
    return rows


def build_expanded_report(rows: list[dict], coverage: dict) -> dict:
    complete_all = [
        row for row in rows
        if row.get("input_complete") and row.get("side") == "over"
    ]
    complete = [row for row in complete_all if row.get("variant") == VARIANT_A]
    assists = [
        row for row in rows
        if row.get("prop_type") == "assists" and row.get("side") == "over"
    ]
    unders = [
        row for row in rows
        if row.get("input_complete") and row.get("side") == "under" and row.get("variant") == VARIANT_A
    ]
    favorite = [
        row for row in rows
        if row.get("input_complete") and -600 <= int(row.get("american_odds") or 0) <= -200
    ]
    return {
        "coverage": coverage,
        "n_rows": len(rows),
        "n_games": len({row.get("game_id") for row in rows}),
        "n_dates": len({row.get("game_date") for row in rows}),
        "complete_input_overs": clustered_scores(complete),
        "complete_input_unders": clustered_scores(unders),
        "variants_over": compare_variants(complete_all),
        "variants_under": compare_variants([
            row for row in rows
            if row.get("input_complete") and row.get("side") == "under"
        ]),
        "book_raw_over": clustered_scores(complete, "implied_probability"),
        "book_novig_over": clustered_scores(complete, "novig_probability"),
        "positional_effect": paired_brier_difference(complete_all, VARIANT_A, VARIANT_B),
        "full_versus_opportunity": paired_brier_difference(complete_all, VARIANT_A, VARIANT_C),
        "calibration_raw": calibration_bins(complete, "raw_probability"),
        "calibration_book": calibration_bins(complete, "implied_probability"),
        "by_prop": group_metric(complete, "prop_type"),
        "assists_incomplete": {
            "note": "Potential assists were not in the historical feed. These rows are the rate prior.",
            "variants": compare_variants(assists),
            "brier": brier_and_log_loss([
                row for row in assists if row.get("variant") == VARIANT_A
            ]),
        },
        "mae": mean_absolute_error(complete),
        "favorite_band": _favorite_summary(favorite),
        "fallback_rates": _fallback_rates(rows),
        "parameter_update": {
            "applied": False,
            "reason": "Production matchup weights and calibration settings were not changed.",
        },
        "sample_warning": (
            "Uncertainty is the game-clustered and date-clustered standard error. "
            "Props inside one game are not independent observations."
        ),
    }


def _favorite_summary(rows: list[dict]) -> dict:
    by_variant: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("result") in ("win", "loss", "push"):
            by_variant[str(row.get("variant"))].append(row)
    summary = {}
    for variant, bucket in by_variant.items():
        realized = [float(row["realized_roi"]) for row in bucket if row.get("realized_roi") is not None]
        expected = [float(row["expected_roi"]) for row in bucket if row.get("expected_roi") is not None]
        by_game: dict[str, list[float]] = defaultdict(list)
        for row in bucket:
            if row.get("realized_roi") is not None:
                by_game[str(row.get("game_id"))].append(float(row["realized_roi"]))
        game_means = [sum(values) / len(values) for values in by_game.values() if values]
        from engine.backtest.metrics import _standard_error
        summary[variant] = {
            "n": len(bucket),
            "n_games": len(game_means),
            "mean_model_probability": _mean(row.get("raw_probability") for row in bucket),
            "mean_implied_probability": _mean(row.get("implied_probability") for row in bucket),
            "hit_rate": _mean(row.get("outcome") for row in bucket if row.get("outcome") is not None),
            "expected_roi": (sum(expected) / len(expected)) if expected else None,
            "realized_roi": (sum(realized) / len(realized)) if realized else None,
            "realized_roi_game_se": _standard_error(game_means),
        }
    return summary


def _fallback_rates(rows: list[dict]) -> dict:
    overs = [row for row in rows if row.get("side") == "over" and row.get("variant") == VARIANT_A]
    n = len(overs)
    if n == 0:
        return {}
    def rate(predicate) -> float:
        return sum(1 for row in overs if predicate(row)) / n
    return {
        "n_evaluation_overs": n,
        "input_complete_rate": rate(lambda row: row.get("input_complete")),
        "rate_prior_rate": rate(lambda row: bool(row.get("silent_fallback"))),
        "potential_assists_unavailable_rate": rate(lambda row: row.get("prop_type") == "assists"),
        "pace_fallback_rate_on_c": _pace_fallback_rate(rows),
        "usage_missing_rate": rate(lambda row: row.get("usage_source") == "missing"),
        "rebound_chances_missing_rate": rate(
            lambda row: row.get("prop_type") == "rebounds" and row.get("rebound_chances_source") == "missing"
        ),
    }


def _pace_fallback_rate(rows: list[dict]) -> float:
    sample = [
        row for row in rows
        if row.get("variant") == VARIANT_C and row.get("side") == "over"
    ]
    if not sample:
        return 0.0
    return sum(1 for row in sample if row.get("pace_is_fallback")) / len(sample)


def _mean(values) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _find_bdl_game(event: dict, games: list[dict]) -> dict | None:
    home = str(event.get("home_team") or "")
    away = str(event.get("away_team") or "")
    for game in games:
        home_team = game.get("home_team") if isinstance(game.get("home_team"), dict) else {}
        away_team = game.get("visitor_team") if isinstance(game.get("visitor_team"), dict) else {}
        from engine.backtest.runner import normalize_team_name
        names = {
            normalize_team_name(home_team.get("full_name") or ""),
            normalize_team_name(away_team.get("full_name") or ""),
        }
        if normalize_team_name(home) in names and normalize_team_name(away) in names:
            return game
    return None


def _bdl_game_date(game: dict | None) -> date | None:
    if not game:
        return None
    raw = str(game.get("date") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _empty_report(coverage: dict) -> dict:
    return {
        "coverage": coverage,
        "n_rows": 0,
        "n_games": 0,
        "n_dates": 0,
        "parameter_update": {"applied": False, "reason": "No prices were scored."},
    }


def _write(report: dict, rows: list[dict], alternates: list[dict]) -> None:
    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REPORT_PATH.write_text(
        json.dumps({"report": report, "rows": rows, "alternates": alternates}, indent=2, default=str),
        encoding="utf-8",
    )


def load_expanded_report(path: Path = _REPORT_PATH) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
