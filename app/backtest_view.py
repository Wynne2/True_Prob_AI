"""Backtest and calibration tab. A button is required before any Odds API call."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from engine.backtest.expanded import load_expanded_report
from engine.backtest.experiment import load_interaction_report
from engine.backtest.odds_history import estimate_sample_cost
from engine.backtest.runner import load_latest_report, run_sample_backtest
from utils.date_utils import today_eastern


def render_backtest_tab() -> None:
    _render_expanded_report()
    _render_interaction_report()
    st.subheader("Backtesting and calibration")
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
    sample_day = st.date_input("Completed game date", value=default_day, max_value=today_eastern())
    cap = st.number_input("Credit cap", min_value=1, max_value=80, value=50, step=1)
    if st.button("Run one-game historical sample"):
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

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Games", report.get("n_games") or 0)
    c2.metric("Over rows", report.get("n_over_rows") or 0)
    raw = report.get("brier_raw") or {}
    cal = report.get("brier_calibrated") or {}
    c3.metric("Brier, raw", _fmt(raw.get("brier")))
    c4.metric("Log loss, raw", _fmt(raw.get("log_loss")))
    st.caption(f"Calibrated Brier {_fmt(cal.get('brier'))} on {cal.get('n') or 0} settled overs. Pushes are excluded.")

    st.markdown("**Model A versus matchup variants**")
    variants = report.get("variants") or []
    if variants:
        st.dataframe(pd.DataFrame(variants), use_container_width=True, hide_index=True)
    else:
        st.caption("No graded rows yet.")

    st.markdown("**Predicted probability versus actual hit rate**")
    bins = report.get("calibration_raw") or []
    if any(row.get("n") for row in bins):
        frame = pd.DataFrame(bins)
        st.dataframe(frame, use_container_width=True, hide_index=True)
        chart = frame.dropna(subset=["predicted_avg", "actual_rate"])
        if not chart.empty:
            st.line_chart(chart.set_index("bin")[["predicted_avg", "actual_rate"]])
    else:
        st.caption("Calibration bins are empty.")

    st.markdown("**By prop type**")
    if report.get("by_prop"):
        st.dataframe(pd.DataFrame(report["by_prop"]), use_container_width=True, hide_index=True)
    if report.get("by_minutes"):
        st.markdown("**By projected minutes**")
        st.dataframe(pd.DataFrame(report["by_minutes"]), use_container_width=True, hide_index=True)

    st.markdown("**Unit ROI on the chosen side**")
    roi = report.get("roi") or {}
    if roi.get("by_odds_band"):
        st.dataframe(pd.DataFrame(roi["by_odds_band"]), use_container_width=True, hide_index=True)
    if roi.get("by_edge_threshold"):
        st.dataframe(pd.DataFrame(roi["by_edge_threshold"]), use_container_width=True, hide_index=True)
    favorites = report.get("favorite_band") or []
    st.caption(f"Chosen bets with American odds from -600 to -200: {len(favorites)}.")

    st.markdown("**Parameter update**")
    st.write(report.get("parameter_update") or {})

    rows = payload.get("rows") or []
    if rows:
        st.markdown("**Prediction log**")
        show = pd.DataFrame(rows)
        keep = [
            column for column in (
                "variant", "player", "prop_type", "line", "side", "american_odds",
                "raw_probability", "calibrated_probability", "implied_probability",
                "projected", "actual", "result", "edge_calibrated", "bet_side",
            )
            if column in show.columns
        ]
        st.dataframe(show[keep].head(200), use_container_width=True, hide_index=True)


def _render_expanded_report() -> None:
    payload = load_expanded_report()
    if not payload:
        return
    report = payload.get("report") or {}
    coverage = report.get("coverage") or {}
    st.markdown("**30-game cohort**")
    if coverage.get("reason"):
        st.warning(coverage["reason"])
    if coverage.get("model_c_reason"):
        st.caption(coverage["model_c_reason"])
    if coverage.get("potential_assists"):
        st.caption(coverage["potential_assists"])
    if coverage.get("line_rule"):
        st.caption(coverage["line_rule"])
    c1, c2, c3 = st.columns(3)
    c1.metric("Games", report.get("n_games") or 0)
    c2.metric("Dates", report.get("n_dates") or 0)
    overs = report.get("complete_input_overs") or {}
    c3.metric("Complete-input overs", overs.get("n") or 0)
    if report.get("variants_over"):
        st.dataframe(pd.DataFrame(report["variants_over"]), use_container_width=True, hide_index=True)
    effect = report.get("positional_effect") or {}
    if effect:
        st.write(
            f"Positional Brier change (Model B minus Model A): {effect.get('mean_brier_difference')}. "
            f"Game-cluster SE {effect.get('game_cluster_se')}. "
            f"Date-cluster SE {effect.get('date_cluster_se')}."
        )
    if report.get("favorite_band"):
        st.markdown("**Odds from -600 to -200**")
        st.dataframe(
            pd.DataFrame([
                {"variant": name, **values}
                for name, values in (report.get("favorite_band") or {}).items()
            ]),
            use_container_width=True,
            hide_index=True,
        )
    features = ((coverage.get("feature_coverage") or {}).get("features") or {})
    if features:
        st.markdown("**Advanced-feature coverage**")
        st.dataframe(
            pd.DataFrame([{"feature": name, **values} for name, values in features.items()]),
            use_container_width=True,
            hide_index=True,
        )


def _render_interaction_report() -> None:
    payload = load_interaction_report()
    if not payload:
        return
    report = payload.get("report") or {}
    february = report.get("february") or {}
    st.markdown("**Opportunity × matchup experiment**")
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
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    effect = february.get("e_minus_a") or {}
    if effect:
        st.write(
            f"February Model E minus Model A Brier: {effect.get('mean_brier_difference')}. "
            f"Date-cluster SE {effect.get('date_cluster_se')}."
        )


def _fmt(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):.3f}"
