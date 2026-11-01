"""
Walk-forward sample backtest.

One historical game is the default. A full slate is a separate, explicit call
with a higher credit cap. Production model weights are never written.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from domain.constants import LEAGUE_AVG_PACE
from domain.enums import PropSide, PropType
from engine.backtest.asof import (
    build_defense_asof,
    build_players_asof,
    game_from_event,
    match_player,
)
from engine.backtest.metrics import (
    brier_and_log_loss,
    calibration_bins,
    chronological_split,
    compare_variants,
    group_metric,
    mean_absolute_error,
    roi_summary,
    select_primary_lines,
)
from engine.backtest.odds_history import (
    SAMPLE_MARKETS,
    SAMPLE_REGION,
    estimate_sample_cost,
    fetch_historical_event_odds,
    fetch_historical_events,
    snapshot_is_pregame,
    snapshot_target,
)
from engine.backtest.variants import (
    VARIANT_A,
    VARIANT_B,
    VARIANT_C,
    VARIANT_C_NO_POS,
    variant_inputs,
)
from engine.backtest_grade import grade_prop
from engine.market_calibration import calibrate_true_probability
from engine.prop_evaluator import _true_prob
from odds.implied_probability import raw_implied_prob_for_side
from utils.math_helpers import clamp

_SUPPORTED = (
    PropType.POINTS,
    PropType.REBOUNDS,
    PropType.ASSISTS,
    PropType.THREES,
    PropType.PRA,
)
_STAT_FIELD = {
    PropType.POINTS: "pts",
    PropType.REBOUNDS: "reb",
    PropType.ASSISTS: "ast",
    PropType.THREES: "fg3m",
}
_REPORT_PATH = Path("data/backtests/latest_report.json")


def _models():
    from models.assists_model import AssistsModel
    from models.points_model import PointsModel
    from models.pra_model import PRAModel
    from models.rebounds_model import ReboundsModel
    from models.threes_model import ThreesModel

    return {
        PropType.POINTS: PointsModel(),
        PropType.REBOUNDS: ReboundsModel(),
        PropType.ASSISTS: AssistsModel(),
        PropType.THREES: ThreesModel(),
        PropType.PRA: PRAModel(),
    }


def _actual(final_row: dict | None, prop: PropType) -> tuple[float | None, str]:
    if final_row is None:
        return None, "void"
    minutes = final_row.get("min")
    if minutes is not None and float(minutes) <= 0:
        return None, "void"
    if prop == PropType.PRA:
        if any(final_row.get(key) is None for key in ("pts", "reb", "ast")):
            return None, "void"
        return float(final_row["pts"] or 0) + float(final_row["reb"] or 0) + float(final_row["ast"] or 0), "ok"
    field = _STAT_FIELD[prop]
    if final_row.get(field) is None:
        return None, "void"
    return float(final_row[field] or 0), "ok"


def _bdl_games(game_day: date) -> list[dict]:
    from data.loaders.nba_api_loader import _client

    return _client().paginate("v1/games", {"dates": [game_day.isoformat()]})


def normalize_team_name(name: str) -> str:
    """Odds API and BALLDONTLIE do not always use the same Clippers name."""
    text = " ".join(str(name or "").lower().replace(".", "").split())
    if text in {"los angeles clippers", "l a clippers", "la clippers"}:
        return "la clippers"
    return text


def _team_names(game: dict) -> tuple[str, str, str, str]:
    home = game.get("home_team") if isinstance(game.get("home_team"), dict) else {}
    away = game.get("visitor_team") if isinstance(game.get("visitor_team"), dict) else {}
    return (
        str(home.get("full_name") or ""),
        str(home.get("id") or ""),
        str(away.get("full_name") or ""),
        str(away.get("id") or ""),
    )


def evaluate_snapshot(
    *,
    event: dict,
    lines: list,
    prior_logs: list[dict],
    final_logs: list[dict],
    home_team_id: str,
    away_team_id: str,
    snapshot_at: datetime,
) -> list[dict]:
    """Score one pregame snapshot with all four variants. No log on or after the game date is an input."""
    tip = datetime.fromisoformat(str(event["commence_time"]).replace("Z", "+00:00"))
    if not snapshot_is_pregame(snapshot_at, tip):
        raise ValueError("Odds snapshot is not before tip-off")
    game_day = tip.astimezone(timezone.utc).date()
    game = game_from_event(event, game_day)
    players = build_players_asof(prior_logs, game_day)
    for player in players.values():
        player.home_ppg = player.points_per_game
        player.away_ppg = player.points_per_game
    home_defense, home_quality = build_defense_asof(prior_logs, game_day, home_team_id)
    away_defense, away_quality = build_defense_asof(prior_logs, game_day, away_team_id)
    finals = {str(row.get("player_id")): row for row in final_logs}
    models = _models()
    rows: list[dict] = []
    seen_lines: dict[tuple, object] = {}
    for line in lines:
        if line.prop_type not in _SUPPORTED:
            continue
        key = (line.player_name, line.prop_type, float(line.line))
        seen_lines.setdefault(key, line)
    for (player_name, prop_type, numeric_line), line in seen_lines.items():
        player = match_player(player_name, players)
        if player is None:
            continue
        if player.team_id == home_team_id:
            defense, quality = away_defense, away_quality
        elif player.team_id == away_team_id:
            defense, quality = home_defense, home_quality
        else:
            defense, quality = away_defense, away_quality
        actual, status = _actual(finals.get(player.player_id), prop_type)
        for variant in (VARIANT_A, VARIANT_B, VARIANT_C, VARIANT_C_NO_POS):
            v_player, v_defense = variant_inputs(player, defense, variant)
            projection = models[prop_type].project(v_player, game, v_defense, is_home=True)
            for side, american in (
                (PropSide.OVER, int(line.over_odds)),
                (PropSide.UNDER, int(line.under_odds)),
            ):
                raw = clamp(_true_prob(projection, numeric_line, side), 0.001, 0.999)
                implied = raw_implied_prob_for_side(side.value, int(line.over_odds), int(line.under_odds))
                calibrated, warnings = calibrate_true_probability(raw, implied, american)
                if status != "ok" or actual is None:
                    result = "void" if status == "void" else "pending"
                    outcome = None
                else:
                    result = grade_prop(actual, numeric_line, side.value)
                    outcome = None if result == "push" else (1 if result == "win" else 0)
                rows.append({
                    "variant": variant,
                    "game_id": game.game_id,
                    "game_date": game_day.isoformat(),
                    "player_id": player.player_id,
                    "player": player.name,
                    "position": player.position.value,
                    "prop_type": prop_type.value,
                    "line": numeric_line,
                    "side": side.value,
                    "book": line.book_key,
                    "american_odds": american,
                    "implied_probability": implied,
                    "raw_probability": raw,
                    "calibrated_probability": calibrated,
                    "edge_raw": raw - implied,
                    "edge_calibrated": calibrated - implied,
                    "projected": projection.projected_value,
                    "expected_minutes": projection.expected_minutes,
                    "actual": actual,
                    "result": result,
                    "outcome": outcome,
                    "pace": v_defense.pace,
                    "pace_is_fallback": v_defense.pace_is_fallback,
                    "calibration_warnings": warnings,
                    "data_quality": quality,
                    "closing_line_value": None,
                    "closing_line_note": "Only one pregame snapshot was purchased. Closing price was not queried.",
                    "bet_side": False,
                })
    _mark_bets(rows)
    return rows


def _mark_bets(rows: list[dict]) -> None:
    """One side per variant, player, prop, and line. The other side is scored for calibration only."""
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if row["side"] != "over":
            continue
        key = (row["variant"], row["game_id"], row["player_id"], row["prop_type"], row["line"])
        groups.setdefault(key, []).append(row)
    unders = {
        (row["variant"], row["game_id"], row["player_id"], row["prop_type"], row["line"]): row
        for row in rows
        if row["side"] == "under"
    }
    for key, overs in groups.items():
        over = overs[0]
        under = unders.get(key)
        choice = over
        if under is not None and float(under["edge_calibrated"]) > float(over["edge_calibrated"]):
            choice = under
        if float(choice["edge_calibrated"]) > 0:
            choice["bet_side"] = True


def _with_minute_bucket(rows: list[dict]) -> list[dict]:
    labeled = []
    for row in rows:
        copy = dict(row)
        minutes = float(row.get("expected_minutes") or 0)
        if minutes < 20:
            copy["minutes_bucket"] = "under 20"
        elif minutes < 30:
            copy["minutes_bucket"] = "20 to 30"
        else:
            copy["minutes_bucket"] = "30 or more"
        labeled.append(copy)
    return labeled


def build_report(rows: list[dict], coverage: dict) -> dict:
    primary = select_primary_lines(rows)
    scored = [row for row in primary if row.get("variant") == VARIANT_A and row.get("side") == "over"]
    dates = [row["game_date"] for row in rows]
    split = chronological_split(dates)
    bets = [
        row for row in primary
        if row.get("variant") == VARIANT_A and row.get("bet_side")
    ]
    return {
        "coverage": coverage,
        "n_rows": len(rows),
        "n_over_rows": len(scored),
        "n_primary_rows": len(primary),
        "n_games": len({row.get("game_id") for row in rows}),
        "scoring_note": (
            "Headline scores use Model A and one line per player and prop, "
            "the line closest to the projection. Alternate lines and the other "
            "variants are not counted as extra observations."
        ),
        "variants": compare_variants([
            row for row in primary if row.get("side") == "over"
        ]),
        "brier_raw": brier_and_log_loss(scored, "raw_probability"),
        "brier_calibrated": brier_and_log_loss(scored, "calibrated_probability"),
        "brier_implied": brier_and_log_loss(scored, "implied_probability"),
        "calibration_raw": calibration_bins(scored, "raw_probability"),
        "mae": mean_absolute_error(scored),
        "by_prop": group_metric(scored, "prop_type"),
        "by_position": group_metric(scored, "position"),
        "by_minutes": group_metric(_with_minute_bucket(scored), "minutes_bucket"),
        "roi": roi_summary(bets),
        "roi_by_variant": {
            variant: roi_summary([
                row for row in primary
                if row.get("variant") == variant and row.get("bet_side")
            ])
            for variant in (VARIANT_A, VARIANT_B, VARIANT_C, VARIANT_C_NO_POS)
        },
        "favorite_band": [
            row for row in bets
            if -600 <= int(row.get("american_odds") or 0) <= -200
        ],
        "chronological_split": split,
        "parameter_update": {
            "applied": False,
            "reason": (
                split["reason"]
                + " Matchup weights were not changed. A lower Brier for Model B than Model A "
                "on this sample is not treated as a production retune."
            ),
        },
        "pace_constant_unchanged": LEAGUE_AVG_PACE,
        "sample_warning": (
            "This sample is too small for a calibration or profitability claim."
            if len({row.get("game_id") for row in rows}) < 30
            else ""
        ),
    }


def run_sample_backtest(
    game_day: date,
    *,
    max_credits: int = 50,
    lookback_days: int = 14,
    output_path: Path = _REPORT_PATH,
) -> dict:
    """
    List historical events (1 credit), then price one game if the estimate fits
    under ``max_credits`` and the account still has that many credits.
    """
    from data.loaders.nba_api_loader import fetch_box_scores_between

    cost = estimate_sample_cost(1)
    coverage = {
        "historical_odds": "unknown",
        "estimated_cost": cost,
        "max_credits": max_credits,
        "game_day": game_day.isoformat(),
    }
    if cost["total_credits"] > max_credits:
        coverage["historical_odds"] = "skipped_cost"
        coverage["reason"] = (
            f"Sample estimate is {cost['total_credits']} credits, above the cap of {max_credits}."
        )
        report = build_report([], coverage)
        _write(output_path, report, [])
        return report

    query_at = datetime(game_day.year, game_day.month, game_day.day, 22, 0, tzinfo=timezone.utc)
    events_payload = fetch_historical_events(query_at)
    coverage["events_quota"] = events_payload.get("quota")
    if not events_payload.get("available"):
        coverage["historical_odds"] = "unavailable"
        coverage["reason"] = events_payload.get("reason")
        report = build_report([], coverage)
        _write(output_path, report, [])
        return report
    events = events_payload.get("events") or []
    coverage["events_found"] = len(events)
    coverage["snapshot_timestamp"] = events_payload.get("timestamp")
    if not events:
        coverage["historical_odds"] = "no_events"
        coverage["reason"] = "The historical events list returned no NBA games at that timestamp."
        report = build_report([], coverage)
        _write(output_path, report, [])
        return report

    remaining_text = str((events_payload.get("quota") or {}).get("remaining") or "")
    try:
        remaining = int(remaining_text)
    except ValueError:
        remaining = None
    odds_cost = cost["event_odds_credits"]
    if remaining is not None and remaining < odds_cost:
        coverage["historical_odds"] = "insufficient_quota"
        coverage["reason"] = f"Remaining credits {remaining} are below the {odds_cost} needed for one event."
        report = build_report([], coverage)
        _write(output_path, report, [])
        return report

    event = _pick_event(events, query_at)
    if event is None:
        coverage["historical_odds"] = "no_pregame_event"
        coverage["reason"] = "No event had a tip at least two hours after the events snapshot."
        report = build_report([], coverage)
        _write(output_path, report, [])
        return report

    tip = datetime.fromisoformat(str(event["commence_time"]).replace("Z", "+00:00"))
    odds_at = snapshot_target(tip)
    odds_payload = fetch_historical_event_odds(str(event["id"]), odds_at, SAMPLE_MARKETS, SAMPLE_REGION)
    coverage["odds_quota"] = odds_payload.get("quota")
    coverage["priced_event"] = {
        "id": event.get("id"),
        "home_team": event.get("home_team"),
        "away_team": event.get("away_team"),
        "commence_time": event.get("commence_time"),
        "requested_snapshot": odds_at.isoformat(),
        "returned_snapshot": odds_payload.get("timestamp"),
        "books": odds_payload.get("bookmakers"),
        "n_lines": len(odds_payload.get("lines") or []),
    }
    if not odds_payload.get("available"):
        coverage["historical_odds"] = "event_odds_unavailable"
        coverage["reason"] = odds_payload.get("reason")
        report = build_report([], coverage)
        _write(output_path, report, [])
        return report
    returned = odds_payload.get("timestamp")
    if returned:
        returned_dt = datetime.fromisoformat(str(returned).replace("Z", "+00:00"))
        if not snapshot_is_pregame(returned_dt, tip):
            coverage["historical_odds"] = "snapshot_after_tip"
            coverage["reason"] = "The returned snapshot is not before tip-off. It was not graded."
            report = build_report([], coverage)
            _write(output_path, report, [])
            return report
        snapshot_dt = returned_dt
    else:
        snapshot_dt = odds_at

    bdl_games = _bdl_games(game_day)
    home_id, away_id = _match_bdl_teams(event, bdl_games)
    prior_end = game_day - timedelta(days=1)
    prior_start = prior_end - timedelta(days=lookback_days)
    prior_logs = fetch_box_scores_between(prior_start, prior_end)
    final_logs = fetch_box_scores_between(game_day, game_day)
    rows = evaluate_snapshot(
        event=event,
        lines=odds_payload["lines"],
        prior_logs=prior_logs,
        final_logs=final_logs,
        home_team_id=home_id,
        away_team_id=away_id,
        snapshot_at=snapshot_dt,
    )
    coverage["historical_odds"] = "available"
    coverage["prior_box_rows"] = len(prior_logs)
    coverage["final_box_rows"] = len(final_logs)
    coverage["limitations"] = [
        "Injuries and projected lineups were not available historically and were not invented.",
        "Team pace and defensive rating are not on the player box score, so those inputs are the league-average fallback.",
        "Potential assists and rebound chances are tracking season averages, not historical box scores, so those props use the rate prior and say the field is missing.",
        "Closing-line value needs a second historical snapshot and was not purchased.",
        "One game cannot support a claim that a matchup feature improves accuracy.",
    ]
    report = build_report(rows, coverage)
    _write(output_path, report, rows)
    return report


def _pick_event(events: list[dict], not_before: datetime) -> dict | None:
    candidates = []
    for event in events:
        raw = event.get("commence_time")
        if not raw:
            continue
        tip = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if tip >= not_before + timedelta(hours=2):
            candidates.append((tip, event))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _match_bdl_teams(event: dict, games: list[dict]) -> tuple[str, str]:
    home_name = str(event.get("home_team") or "")
    away_name = str(event.get("away_team") or "")
    for game in games:
        home_full, home_id, away_full, away_id = _team_names(game)
        if normalize_team_name(home_full) == normalize_team_name(home_name) and normalize_team_name(away_full) == normalize_team_name(away_name):
            return home_id, away_id
        if normalize_team_name(home_full) == normalize_team_name(away_name) and normalize_team_name(away_full) == normalize_team_name(home_name):
            return away_id, home_id
    return "", ""


def _write(path: Path, report: dict, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"report": report, "rows": rows}
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def load_latest_report(path: Path = _REPORT_PATH) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
