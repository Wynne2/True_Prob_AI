"""Backtest and calibration tab. A button is required before any Odds API call."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from engine.accuracy.run import load_accuracy_report
from engine.backtest.expanded import load_expanded_report
from engine.backtest.experiment import load_interaction_report
from engine.backtest.odds_history import estimate_sample_cost
from engine.backtest.runner import load_latest_report, run_sample_backtest
from app import ui
from utils.date_utils import today_eastern


def render_backtest_tab() -> None:
    _render_live_track_record()
    _render_expanded_report()
    _render_interaction_report()
    _render_accuracy_report()
    ui.section("One-game historical sample", "scored against historical Odds API prices")
    st.caption(
        "Historical prices come only from The Odds API. Stats come only from BALLDONTLIE. "
        "A one-game sample is 1 credit for the events list plus 40 credits for four prop markets "
        "in one US region. Production model weights are not changed from this tab."
    )
    cost = estimate_sample_cost(1)
    st.write(
        f"Estimated sample cost: **{cost['total_credits']}** credits "
        f"({cost['events_list_credits']} for events, {cost['event_odds_credits']} for one game)."
    )

    default_day = today_eastern() - timedelta(days=2)
    with st.container(border=True):
        d1, d2 = st.columns(2)
        sample_day = d1.date_input("Completed game date", value=default_day, max_value=today_eastern())
        cap = d2.number_input("Credit cap", min_value=1, max_value=80, value=50, step=1)
        run_sample = st.button("Run one-game historical sample", icon=":material/play_arrow:")
    if run_sample:
        if not isinstance(sample_day, date):
            st.error("Choose a date.")
            return
        with st.spinner("Checking historical coverage and scoring one game if the credit cap allows..."):
            run_sample_backtest(sample_day, max_credits=int(cap))
        st.rerun()

    payload = load_latest_report()
    if not payload:
        st.info(
            "No backtest file yet. Daily scans still save pregame odds in data/predictions "
            "without overwriting an earlier price. Grade those after the games finish if "
            "historical odds are unavailable."
        )
        return

    report = payload.get("report") or {}
    coverage = report.get("coverage") or {}
    st.markdown(f"**Historical odds status:** {coverage.get('historical_odds', 'unknown')}")
    if coverage.get("reason"):
        st.warning(coverage["reason"])
    for note in coverage.get("limitations") or []:
        st.caption(note)
    if report.get("scoring_note"):
        st.caption(report["scoring_note"])
    if report.get("sample_warning"):
        st.warning(report["sample_warning"])

    raw = report.get("brier_raw") or {}
    cal = report.get("brier_calibrated") or {}
    ui.tiles([
        ("Games", str(report.get("n_games") or 0), ""),
        ("Over rows", str(report.get("n_over_rows") or 0), ""),
        ("Brier, raw", _fmt(raw.get("brier")), ""),
        ("Log loss, raw", _fmt(raw.get("log_loss")), ""),
    ])
    st.caption(f"Calibrated Brier {_fmt(cal.get('brier'))} on {cal.get('n') or 0} settled overs. Pushes are excluded.")

    st.markdown("#### Model A versus matchup variants")
    variants = report.get("variants") or []
    if variants:
        st.dataframe(pd.DataFrame(variants), width="stretch", hide_index=True)
    else:
        st.caption("No graded rows yet.")

    st.markdown("#### Predicted probability versus actual hit rate")
    bins = report.get("calibration_raw") or []
    if any(row.get("n") for row in bins):
        frame = pd.DataFrame(bins)
        st.dataframe(frame, width="stretch", hide_index=True)
        chart = frame.dropna(subset=["predicted_avg", "actual_rate"])
        if not chart.empty:
            st.line_chart(chart.set_index("bin")[["predicted_avg", "actual_rate"]])
    else:
        st.caption("Calibration bins are empty.")

    st.markdown("#### By prop type")
    if report.get("by_prop"):
        st.dataframe(pd.DataFrame(report["by_prop"]), width="stretch", hide_index=True)
    if report.get("by_minutes"):
        st.markdown("#### By projected minutes")
        st.dataframe(pd.DataFrame(report["by_minutes"]), width="stretch", hide_index=True)

    st.markdown("#### Unit ROI on the chosen side")
    roi = report.get("roi") or {}
    if roi.get("by_odds_band"):
        st.dataframe(pd.DataFrame(roi["by_odds_band"]), width="stretch", hide_index=True)
    if roi.get("by_edge_threshold"):
        st.dataframe(pd.DataFrame(roi["by_edge_threshold"]), width="stretch", hide_index=True)
    favorites = report.get("favorite_band") or []
    st.caption(f"Chosen bets with American odds from -600 to -200: {len(favorites)}.")

    st.markdown("#### Parameter update")
    st.write(report.get("parameter_update") or {})

    rows = payload.get("rows") or []
    if rows:
        st.markdown("#### Prediction log")
        show = pd.DataFrame(rows)
        keep = [
            column for column in (
                "variant", "player", "prop_type", "line", "side", "american_odds",
                "raw_probability", "calibrated_probability", "implied_probability",
                "projected", "actual", "result", "edge_calibrated", "bet_side",
            )
            if column in show.columns
        ]
        st.dataframe(show[keep].head(200), width="stretch", hide_index=True)


def _render_live_track_record() -> None:
    """Nightly boards graded against box scores: candidate vs production vs market."""
    from engine.candidate import track

    ui.section("Live track record", f"every scanned board since {track.DEFAULT_START:%b %d}, graded from box scores")
    st.caption(
        "Each scan of today's slate saves every priced prop before tip-off (first save keeps the price). "
        "Finished dates are graded against BALLDONTLIE box scores. No Odds API credits are used."
    )
    if st.button("Grade finished games", icon=":material/fact_check:"):
        with st.spinner("Grading saved boards..."):
            graded = track.grade_pending(today=today_eastern())
        st.caption(f"Graded {len(graded)} date(s).")
    found = track.versions()
    if not found:
        st.info("No graded boards yet. Scan a slate before tip-off, then grade the next day.", icon=":material/schedule:")
        return
    for version in reversed(found):
        _render_version_card(track.scorecard(since=track.holdout_start(version), version=version))


def _render_version_card(card: dict) -> None:
    if not card["decided_rows"]:
        return
    st.markdown(f"**{card['version']}** · games from {card['since']} · {len(card['dates'])} date(s)")
    b = card["brier_same_props"]
    ui.tiles([
        ("Graded props", f"{card['decided_rows']:,}", ""),
        ("Dates", f"{len(card['dates'])}", ""),
        ("Brier candidate", _fmt(b["candidate"], 4), ""),
        ("Brier market", _fmt(b["market_novig"], 4), ""),
    ])
    st.caption(f"Brier on the same {b['n']:,} props (lower is better): candidate {_fmt(b['candidate'], 4)}, "
               f"production {_fmt(b['production'], 4)}, no-vig market {_fmt(b['market_novig'], 4)}.")
    rows = []
    for name, s in card["candidate_edge_bets"].items():
        rows.append({
            "Candidate edge": name, "Bets": s.get("n", 0), "Dates": s.get("dates"),
            "Hit rate": s.get("hit_rate"), "Predicted": s.get("avg_candidate_probability"),
            "Implied": s.get("avg_implied"), "Flat ROI / bet": s.get("flat_roi_per_bet"),
            "ROI date SE": s.get("roi_date_se"),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.caption(card["note"])


def _render_expanded_report() -> None:
    payload = load_expanded_report()
    if not payload:
        return
    report = payload.get("report") or {}
    coverage = report.get("coverage") or {}
    ui.section("30-game cohort", "leak-free backtest on games the model never saw")
    if coverage.get("reason"):
        st.warning(coverage["reason"])
    if coverage.get("model_c_reason"):
        st.caption(coverage["model_c_reason"])
    if coverage.get("potential_assists"):
        st.caption(coverage["potential_assists"])
    if coverage.get("line_rule"):
        st.caption(coverage["line_rule"])
    overs = report.get("complete_input_overs") or {}
    ui.tiles([
        ("Games", str(report.get("n_games") or 0), ""),
        ("Dates", str(report.get("n_dates") or 0), ""),
        ("Complete-input overs", f"{overs.get('n') or 0:,}", "court"),
    ])
    if report.get("variants_over"):
        st.dataframe(pd.DataFrame(report["variants_over"]), width="stretch", hide_index=True)
    effect = report.get("positional_effect") or {}
    if effect:
        st.write(
            f"Positional Brier change (Model B minus Model A): {_fmt(effect.get('mean_brier_difference'), 4)}. "
            f"Game-cluster SE {_fmt(effect.get('game_cluster_se'), 4)}. "
            f"Date-cluster SE {_fmt(effect.get('date_cluster_se'), 4)}."
        )
    if report.get("favorite_band"):
        st.markdown("#### Odds from -600 to -200")
        st.dataframe(
            pd.DataFrame([
                {"variant": name, **values}
                for name, values in (report.get("favorite_band") or {}).items()
            ]),
            width="stretch",
            hide_index=True,
        )
    features = ((coverage.get("feature_coverage") or {}).get("features") or {})
    if features:
        st.markdown("#### Advanced-feature coverage")
        st.dataframe(
            pd.DataFrame([{"feature": name, **values} for name, values in features.items()]),
            width="stretch",
            hide_index=True,
        )


def _render_interaction_report() -> None:
    payload = load_interaction_report()
    if not payload:
        return
    report = payload.get("report") or {}
    february = report.get("february") or {}
    ui.section("Opportunity × matchup experiment")
    st.caption((report.get("production_change") or {}).get("reason") or "")
    st.caption((report.get("coverage") or {}).get("potential_assists") or "")
    rows = []
    for name, block in (february.get("variants") or {}).items():
        rows.append({
            "variant": name,
            "n": block.get("n"),
            "brier": block.get("brier"),
            "log_loss": block.get("log_loss"),
            "games": block.get("n_games"),
            "dates": block.get("n_dates"),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    effect = february.get("e_minus_a") or {}
    if effect:
        st.write(
            f"February Model E minus Model A Brier: {_fmt(effect.get('mean_brier_difference'), 4)}. "
            f"Date-cluster SE {_fmt(effect.get('date_cluster_se'), 4)}."
        )


def _render_accuracy_report() -> None:
    payload = load_accuracy_report()
    ui.section("Prediction accuracy", "projection error and calibration, separate from betting edges")
    if not payload:
        st.caption(
            "No accuracy report yet. Run `python main.py backtest --experiment accuracy`. "
            "That fits weights on 2025 games before January, then scores March once. "
            "It does not change the production model."
        )
        return
    report = payload.get("report") or {}
    st.caption((report.get("production_change") or {}).get("reason") or "")
    for note in report.get("missing") or []:
        st.warning(note)
    freeze = report.get("freeze") or {}
    weights = []
    for stat, fit in (freeze.get("fits") or {}).items():
        coef = fit.get("coefficients") or {}
        weights.append({
            "stat": stat,
            "channel": fit.get("defense_channel"),
            "december_mae": fit.get("december_mae"),
            "prior_mae": fit.get("baseline_mae"),
            **coef,
        })
    if weights:
        st.markdown("Learned factor weights")
        st.dataframe(pd.DataFrame(weights), width="stretch", hide_index=True)
    projection = (report.get("march_projection") or {}).get("by_stat") or {}
    if projection:
        st.markdown("March projection error")
        rows = []
        for stat, block in projection.items():
            rows.append({
                "stat": stat,
                "n": block.get("n"),
                "prior_mae": block.get("prior_mae"),
                "context_mae": block.get("context_mae"),
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    probability = report.get("march_probability") or {}
    models = probability.get("models") or {}
    if models:
        st.markdown("March probability comparison")
        st.dataframe(
            pd.DataFrame([
                {
                    "model": name,
                    "n": block.get("n"),
                    "brier": block.get("brier"),
                    "log_loss": block.get("log_loss"),
                    "date_se": block.get("date_cluster_se"),
                }
                for name, block in models.items()
            ]),
            width="stretch",
            hide_index=True,
        )
        effect = probability.get("c_minus_a") or {}
        st.write(
            f"Context model minus Model A Brier: {_fmt(effect.get('mean_brier_difference'), 4)}. "
            f"Date-cluster SE {_fmt(effect.get('date_cluster_se'), 4)}."
        )
        high = probability.get("high_probability_70_90") or {}
        if high:
            st.dataframe(
                pd.DataFrame([{"model": name, **block} for name, block in high.items()]),
                width="stretch",
                hide_index=True,
            )


def _fmt(value, places: int = 3) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{places}f}"
