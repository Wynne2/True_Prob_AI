"""
True Prob AI – Streamlit Dashboard.

Full interactive dashboard for scanning props, building parlays,
comparing sportsbook lines, and calculating payouts.

Run with:
    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Ensure project root is on the path when running via streamlit
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import streamlit as st

from domain.constants import (
    FAVORITE_STRAIGHT_BET_AUDIT_BAND_HIGH,
    FAVORITE_STRAIGHT_BET_AUDIT_BAND_LOW,
)
from domain.enums import ConfidenceTier, PropType, SortField
from engine.bankroll_engine import apply_stake_to_all, payout_summary
from engine.parlay_builder import ParlayConstraints, build_parlays, leg_odds_match_constraints
from engine.ranking_engine import rank_parlays, summary_stats
from engine.slate_scanner import SlateScanner
from app import ui
from app.arbitrage_view import render_arbitrage_tab
from app.backtest_view import render_backtest_tab
from engine.straight_bet_audit import (
    build_favorite_band_audit_table,
    format_favorite_band_summary_markdown,
    pipeline_drop_averages_for_positive_edge_favorites,
    top_uncapped_minus_fair_gap,
)
from utils.api_debug import capture_api_responses
from utils.date_utils import today_eastern
from utils.formatting import book_display_name, format_american, format_edge, format_prob, format_currency

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="True Prob AI",
    page_icon=":material/sports_basketball:",
    layout="wide",
    initial_sidebar_state="expanded",
)

ui.inject_css()

_PCT = st.column_config.NumberColumn(format="%.1f%%")


# ---------------------------------------------------------------------------
# Sidebar: Constraints
# ---------------------------------------------------------------------------

def render_sidebar() -> dict:
    """Render sidebar controls and return constraint dict."""
    with st.sidebar:
        st.markdown("### Slate")
        selected_date = st.date_input("Game date", value=today_eastern(), key="game_date")

        if selected_date > today_eastern():
            st.warning(
                f"{selected_date} is in the future. Lines may not be posted yet.",
                icon=":material/schedule:",
            )
        elif selected_date < today_eastern():
            st.caption(f"Historical date: showing data for {selected_date}.")

        is_playoff = st.checkbox(
            "Playoff slate",
            value=False,
            help="Boosts projected minutes slightly for playoff rotation patterns.",
        )

        run = st.button(
            "Scan slate", type="primary", icon=":material/radar:", width="stretch",
        )

        st.divider()
        st.markdown("### Edge and legs")
        min_edge = st.slider(
            "Minimum edge per leg (%)", min_value=0.0, max_value=20.0, value=1.0, step=0.5,
            help="Market-anchored edges are realistic (typically 1–4%); 5%+ is rare.",
        ) / 100.0

        col_legs_a, col_legs_b = st.columns(2)
        with col_legs_a:
            min_legs = st.selectbox("Min legs", options=[2, 3], index=0)
        with col_legs_b:
            max_legs = st.selectbox("Max legs", options=[2, 3, 4, 5], index=1)

        st.divider()
        st.markdown("### Odds range")
        st.caption(
            "Applies to straight bets, props, line shopping and parlays. "
            "Enter signed American odds in either order, for example −600 and −110 for favorites only."
        )
        col1, col2 = st.columns(2)
        with col1:
            min_leg_odds = st.number_input(
                "Min leg odds",
                value=-600,
                step=10,
                key="min_leg_odds_band_v3",
                help="American odds lower bound (signed). Favorites are negative; "
                "must be ≤ your longest prices (e.g. −600 includes −210, −250). "
                "If you still see no heavy favorites after an update, this value was reset to −600.",
            )
        with col2:
            max_leg_odds = st.number_input(
                "Max leg odds",
                value=400,
                step=10,
                key="max_leg_odds_band_v3",
            )

        st.divider()
        st.markdown("### Props")
        all_prop_types = [pt.value for pt in PropType]
        selected_props = st.multiselect(
            "Prop types",
            options=all_prop_types,
            default=[],
            format_func=ui.prop_label,
            placeholder="All prop types",
        )

        min_confidence = st.selectbox(
            "Minimum confidence (parlays)",
            options=["Any", "low", "medium", "high"],
            index=0,
            format_func=str.capitalize,
        )

        stake = st.number_input("Stake ($)", min_value=1.0, value=100.0, step=10.0)

        with st.expander("Parlay odds and display", icon=":material/tune:"):
            col3, col4 = st.columns(2)
            with col3:
                min_parlay_odds = st.number_input("Min parlay odds", value=-10000, step=50)
            with col4:
                max_parlay_odds = st.number_input("Max parlay odds", value=100000, step=50)
            top_parlays = st.number_input("Parlays to show", min_value=1, value=10, step=1)
            top_straight = st.number_input("Straight bets to show", min_value=1, value=15, step=5)
            sort_by = st.selectbox(
                "Sort parlays by",
                options=[s.value for s in SortField],
                index=0,
                format_func=lambda v: v.replace("_", " ").capitalize(),
            )

    return {
        "date": selected_date,
        "min_edge": min_edge,
        "max_legs": int(max_legs),
        "min_legs": int(min_legs),
        "min_leg_odds": int(min_leg_odds),
        "max_leg_odds": int(max_leg_odds),
        "min_parlay_odds": int(min_parlay_odds),
        "max_parlay_odds": int(max_parlay_odds),
        "prop_types": [PropType(p) for p in selected_props] if selected_props else None,
        "min_confidence": None if min_confidence == "Any" else min_confidence,
        "stake": float(stake),
        "top_parlays": int(top_parlays),
        "top_straight": int(top_straight),
        "sort_by": SortField(sort_by),
        "run": run,
        "is_playoff": is_playoff,
    }


# ---------------------------------------------------------------------------
# Main content renderers
# ---------------------------------------------------------------------------

def render_masthead(game_date: date) -> None:
    from config import get_credentials
    creds = get_credentials()
    ui.masthead(game_date, creds.available_providers, creds.missing_providers)


def _qualifying_props_for_display(all_props: list, params: dict) -> list:
    """Edge + per-leg American odds band (same rules as parlay legs)."""
    return [
        p for p in all_props
        if p.edge >= params["min_edge"]
        and leg_odds_match_constraints(
            p.sportsbook_odds, params["min_leg_odds"], params["max_leg_odds"]
        )
    ]


def render_games_slate(games: list) -> None:
    """Show today's NBA slate — game time (ET), arena, and city."""
    if not games:
        return
    ui.section("Tonight's slate", f"{len(games)} game{'s' if len(games) != 1 else ''}")
    ui.game_cards(games)


_SOURCE_LABELS = {"kalshi": "Kalshi", "polymarket": "Polymarket", "odds_api": "The Odds API"}


def render_odds_sources(counts: dict, errors: dict) -> None:
    """Show how many prop lines each odds source contributed this scan."""
    if not counts and not errors:
        return
    names = sorted(set(counts) | set(errors))
    ui.tiles([
        (f"{_SOURCE_LABELS.get(name, name)} prop lines", f"{counts.get(name, 0):,}", "")
        for name in names
    ])
    for name in names:
        if errors.get(name):
            st.caption(f"{_SOURCE_LABELS.get(name, name)}: {errors[name][:120]}")


def render_game_lines(game_lines: list) -> None:
    """Moneyline / spread / total sides from the prediction markets."""
    ui.section("Game lines", "Kalshi and Polymarket")
    if not game_lines:
        st.info("No prediction-market game lines for this date.", icon=":material/info:")
        return
    from odds.game_lines import consensus_game_lines

    consensus = consensus_game_lines(game_lines)
    if consensus:
        ui.note("Consensus main lines: the strike nearest 50/50, median across exchanges.")
        st.dataframe(
            pd.DataFrame([
                {
                    "Matchup": f"{away} @ {home}",
                    "Total": entry.get("total"),
                    "Home spread": entry.get("home_spread"),
                }
                for (away, home), entry in sorted(consensus.items())
            ]),
            width="stretch",
            hide_index=True,
        )

    ui.note("All priced sides: fee-inclusive cost converted to American odds.")
    rows = [
        {
            "Matchup": f"{gl.away_abbr} @ {gl.home_abbr}",
            "Book": book_display_name(gl.book),
            "Market": gl.market,
            "Selection": gl.selection.upper(),
            "Line": gl.line,
            "Cost": round(gl.price, 3),
            "Odds": format_american(gl.american_odds),
        }
        for gl in game_lines
    ]
    df = pd.DataFrame(rows).sort_values(["Matchup", "Market", "Selection", "Line", "Book"])
    st.dataframe(df, width="stretch", hide_index=True)


def render_props_table(props: list) -> None:
    """Render the qualifying props as a sortable DataFrame."""
    if not props:
        st.warning(
            "No props match the current filters. Lower the minimum edge or widen the odds range.",
            icon=":material/filter_alt_off:",
        )
        return

    rows = []
    for p in props:
        rows.append({
            "Player": p.player_name,
            "Team": p.team_abbr,
            "Opp": p.opponent_abbr,
            "Prop": ui.prop_label(p.prop_type),
            "Line": p.line,
            "Side": p.side.value.upper(),
            "Baseline": round(getattr(p, "baseline_projection", 0) or p.projected_value, 1),
            "Projected": round(p.projected_value, 1),
            "Exp Min": round(getattr(p, "expected_minutes", 0), 1),
            "Used %": round(p.true_probability * 100, 1),
            "Model %": round((getattr(p, "raw_model_probability", 0.0) or 0.0) * 100, 1),
            "Candidate %": (
                round(p.candidate_probability * 100, 1)
                if getattr(p, "candidate_probability", None) is not None else None
            ),
            "Cand proj": (
                round(p.candidate_projection, 1)
                if getattr(p, "candidate_projection", None) is not None else None
            ),
            "Consensus %": (
                round(p.market_probability * 100, 1)
                if getattr(p, "market_probability", None) is not None else None
            ),
            "Model wt": round(float(getattr(p, "model_weight", 0.0) or 0.0), 2),
            "Implied %": round(p.implied_probability * 100, 1),
            "Edge %": round(p.edge * 100, 1),
            "Odds": format_american(p.sportsbook_odds),
            "Fair Odds": format_american(p.fair_odds),
            "Best Book": book_display_name(p.best_book, getattr(p, "best_book_key", "") or ""),
            "Confidence": p.confidence.value.replace("_", " ").capitalize(),
            "Warnings": "; ".join(getattr(p, "calibration_warnings", []) or []),
        })

    df = pd.DataFrame(rows).sort_values("Edge %", ascending=False)

    def highlight_edge(val):
        if val >= 5:
            return "color: #34D399; font-weight: 600"
        elif val > 0:
            return "color: #A7F3D0"
        return "color: #F87171"

    styled = df.style.map(highlight_edge, subset=["Edge %"]).format(precision=1)
    st.dataframe(
        styled,
        width="stretch",
        hide_index=True,
        column_config={
            "Used %": st.column_config.ProgressColumn(
                "Used %", min_value=0, max_value=100, format="%.1f%%",
                help="Probability used for edge. When market-anchored, a blend of Consensus % and Model % by Model wt.",
            ),
            "Model %": st.column_config.NumberColumn(
                format="%.1f%%", help="The model's own distribution probability, before any market blend.",
            ),
            "Candidate %": st.column_config.NumberColumn(
                format="%.1f%%",
                help="Frozen candidate model (engine/candidate). Shown for comparison; not used for edge.",
            ),
            "Cand proj": st.column_config.NumberColumn(format="%.1f", help="Candidate model projected stat."),
            "Consensus %": st.column_config.NumberColumn(
                format="%.1f%%", help="No-vig market consensus for this side across venues.",
            ),
            "Model wt": st.column_config.NumberColumn(
                format="%.2f", help="Weight the model had against the consensus. 0 means Used % is the consensus.",
            ),
            "Implied %": _PCT,
            "Edge %": st.column_config.NumberColumn(format="%+.1f%%", help="Used % minus implied %."),
            "Line": st.column_config.NumberColumn(format="%g"),
        },
    )


def _prop_leg_key(prop) -> tuple:
    """Identity for one evaluated leg (same book price can vary; odds included for dedupe)."""
    return (
        prop.player_id,
        prop.prop_type,
        prop.line,
        prop.side,
        prop.sportsbook_odds,
    )


def _straight_bet_details(prop, stake: float) -> None:
    """Model breakdown under a pick card."""
    book_label = book_display_name(prop.best_book, getattr(prop, "best_book_key", "") or "")
    net_profit = ui.payout_profit(prop.sportsbook_odds, stake)
    with st.expander("Model breakdown", icon=":material/insights:"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Fair odds", format_american(prop.fair_odds))
        c2.metric("Projected", f"{prop.projected_value:.1f}")
        c3.metric("Expected minutes", f"{getattr(prop, 'expected_minutes', 0) or 0:.0f}")
        c4.metric("Net profit", format_currency(net_profit))

        st.caption(
            f"Best price at {book_label}. Baseline "
            f"~{getattr(prop, 'baseline_projection', 0) or prop.projected_value:.1f}."
        )
        wm = getattr(prop, "calibration_warnings", None) or []
        if wm:
            st.caption("Calibration notes: " + ", ".join(wm))
        if getattr(prop, "explanation", ""):
            st.markdown(prop.explanation.replace("$", "\\$"))


def render_straight_bets(props: list, stake: float, top_n: int) -> None:
    """Render top straight-bet recommendations as individual cards with payouts."""
    if not props:
        st.warning(
            "No straight bets match the current filters. Lower the minimum edge or widen the "
            "leg odds range (the same filter as parlays).",
            icon=":material/filter_alt_off:",
        )
        return

    # Any minus-money price: still filtered by sidebar leg band + min edge, but no longer
    # limited to ≤−200 only (e.g. −115 to −198 favorites were never shown here before).
    sorted_props = sorted(props, key=lambda p: p.edge, reverse=True)[:top_n]
    top_keys = {_prop_leg_key(p) for p in sorted_props}
    favorite_sorted = sorted(
        (p for p in props if p.sportsbook_odds < 0),
        key=lambda p: p.edge,
        reverse=True,
    )
    favorite_not_in_top = [p for p in favorite_sorted if _prop_leg_key(p) not in top_keys][:35]

    avg_edge = sum(p.edge for p in sorted_props) / len(sorted_props)
    avg_true = sum(p.true_probability for p in sorted_props) / len(sorted_props)
    best_edge = sorted_props[0].edge

    ui.section(f"Top {len(sorted_props)} straight bets", f"from {len(props)} qualifying props")
    ui.tiles([
        ("Qualifying props", f"{len(props):,}", ""),
        ("Best edge", f"{best_edge * 100:+.1f}%", "edge"),
        ("Average edge", f"{avg_edge * 100:+.1f}%", "edge"),
        ("Average chance", f"{avg_true * 100:.1f}%", ""),
    ])
    n_blend = sum(1 for p in sorted_props if ui.is_market_blend(p))
    n_market_only = sum(1 for p in sorted_props if ui.edge_basis(p) == "vs consensus")
    ui.note(
        "The green bar is the chance used for edge. The blue tick is the chance the price implies. "
        "The bright segment between them is the edge. "
        + (
            f"Probabilities are market-anchored. Of these {len(sorted_props)}, {n_blend - n_market_only} blend "
            f"the no-vig consensus with the model and {n_market_only} give the model zero weight, so their edge "
            "is line shopping against the consensus. "
            "Each card says which."
            if n_blend else ""
        )
    )

    for rank, prop in enumerate(sorted_props, 1):
        ui.pick_card(rank, prop, stake)
        _straight_bet_details(prop, stake)

    if favorite_not_in_top:
        with st.expander(
            f"More favorite-priced legs ({len(favorite_not_in_top)})", icon=":material/format_list_bulleted:",
        ):
            st.caption(
                f"Minus-money prices that missed the top {top_n} by edge, often because the "
                "highest-edge plays are plus-money. The same edge and odds filters apply."
            )
            for rank, prop in enumerate(favorite_not_in_top, 1):
                ui.pick_card(rank, prop, stake)


def render_favorite_band_straight_audit(all_props: list, params: dict) -> None:
    """
    Full audit for legs whose best American price is in the evaluator favorite band
    (see domain.constants FAVORITE_STRAIGHT_BET_AUDIT_BAND_*).
    """
    with st.expander(
        f"Favorite-band audit ({FAVORITE_STRAIGHT_BET_AUDIT_BAND_LOW} to "
        f"{FAVORITE_STRAIGHT_BET_AUDIT_BAND_HIGH})",
        icon=":material/fact_check:",
    ):
        st.caption(
            "Straight bets use only the minimum edge and leg odds filters. "
            "Minimum confidence applies to parlays only."
        )
        rows, summary = build_favorite_band_audit_table(
            all_props,
            params["min_leg_odds"],
            params["max_leg_odds"],
            params["min_edge"],
        )
        if not rows:
            st.info(
                f"No evaluated legs in [{FAVORITE_STRAIGHT_BET_AUDIT_BAND_LOW}, "
                f"{FAVORITE_STRAIGHT_BET_AUDIT_BAND_HIGH}] on this slate (or scan not run)."
            )
            return
        df = pd.DataFrame(rows)
        st.dataframe(df, width="stretch", hide_index=True)
        st.markdown(format_favorite_band_summary_markdown(summary))

        st.markdown("#### Top 10: uncapped true prob − fair implied")
        top10 = top_uncapped_minus_fair_gap(
            all_props,
            params["min_leg_odds"],
            params["max_leg_odds"],
            params["min_edge"],
            top_n=10,
        )
        if not top10:
            st.caption("_No band legs to rank._")
        else:
            st.dataframe(pd.DataFrame(top10), width="stretch", hide_index=True)

        st.markdown("#### Pipeline drop averages (final edge > 0, favorite band)")
        drops = pipeline_drop_averages_for_positive_edge_favorites(all_props)
        st.json(drops, expanded=False)
        if drops.get("n_positive_edge_in_band", 0) > 0:
            winner = drops.get("largest_among_user_steps_1_to_4")
            lav = drops.get("largest_avg_value")
            struct = drops.get("avg_drop_structural_pre_market")
            st.markdown(
                f"**Largest average drop among steps 1–4 (shrink / completeness / "
                f"market cal / final gate):** `{winner}` "
                f"(≈ **{lav}** per leg). "
                f"**Step 5 (UI)** does not change evaluated probabilities (avg drop **0**). "
                f"Structural block **after_completeness → pre-market** averages **{struct}**."
            )

        if summary["positive_edge_in_band"] and not summary["positive_edge_passing_straight_filters"]:
            st.warning(
                "Some legs in this band have **positive model edge** but fail the **current** "
                "min-edge or leg-odds widgets. Lower min edge or widen the odds band, then re-scan "
                "if you changed filters after the last scan."
            )
        if summary["props_in_evaluator_band"] and not summary["positive_edge_in_band"]:
            st.info(
                "No positive final edges in this band on this run — the model is below raw implied "
                "for every leg here (compression, completeness, gates, or projection vs line)."
            )


def render_parlay_cards(parlays: list, stake: float, top_n: int) -> None:
    """Render each top parlay as a ticket with leg details."""
    if not parlays:
        st.warning(
            "No parlays could be built. Lower the minimum edge or widen the odds range.",
            icon=":material/filter_alt_off:",
        )
        return

    stats = summary_stats(parlays)
    ui.section(f"Top {min(top_n, len(parlays))} parlays", f"from {len(parlays):,} built")
    ui.tiles([
        ("Parlays built", f"{stats.get('count', 0):,}", ""),
        ("Best edge", f"{stats.get('max_edge', 0) * 100:+.1f}%", "edge"),
        ("Average edge", f"{stats.get('avg_edge', 0) * 100:+.1f}%", "edge"),
        ("Average legs", f"{stats.get('avg_legs', 0):.1f}", ""),
    ])

    for parlay in parlays[:top_n]:
        ui.parlay_ticket(parlay)
        with st.expander("Leg details", icon=":material/insights:"):
            leg_rows = []
            for leg in parlay.legs:
                leg_rows.append({
                    "Player": leg.player_name,
                    "Team": leg.team_abbr,
                    "vs": leg.opponent_abbr,
                    "Prop": ui.prop_label(leg.prop_type),
                    "Line": leg.line,
                    "Side": leg.side.value.upper(),
                    "Projected": round(leg.projected_value, 1),
                    "Used %": round(leg.true_probability * 100, 1),
                    "Edge %": round(leg.edge * 100, 1),
                    "Odds": format_american(leg.sportsbook_odds),
                    "Book": book_display_name(leg.sportsbook, getattr(leg, "best_book_key", "") or ""),
                    "Confidence": leg.confidence.value,
                })
            st.dataframe(
                pd.DataFrame(leg_rows), width="stretch", hide_index=True,
                column_config={"Used %": _PCT, "Edge %": st.column_config.NumberColumn(format="%+.1f%%")},
            )
            for leg in parlay.legs:
                if leg.explanation:
                    st.markdown(
                        f"**{leg.player_name}**, {ui.prop_label(leg.prop_type)} "
                        f"{leg.side.value} {leg.line:g}"
                    )
                    st.caption(leg.explanation)


def render_line_shopping(props: list) -> None:
    """Show a multi-book odds comparison table."""
    if not props:
        st.warning(
            "No props match the current filters, so there are no lines to compare.",
            icon=":material/filter_alt_off:",
        )
        return

    ui.section("Line shopping", "the same prop priced across every book")

    # Aggregate all available lines
    from collections import defaultdict

    lines_by_player_prop: dict = defaultdict(list)
    for prop in props:
        for ol in prop.all_lines:
            key = (prop.player_name, ui.prop_label(prop.prop_type), ol.line)
            lines_by_player_prop[key].append(ol)

    rows = []
    for (player, prop_type, line), book_lines in lines_by_player_prop.items():
        row = {"Player": player, "Prop": prop_type, "Line": line}
        for ol in book_lines:
            label = book_display_name(ol.book, getattr(ol, "book_key", "") or "")
            row[f"{label} Over"] = format_american(ol.over_odds)
            row[f"{label} Under"] = format_american(ol.under_odds)
        rows.append(row)

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df.head(30), width="stretch", hide_index=True)
    else:
        st.info("No book-by-book lines were returned for these props.", icon=":material/info:")


# ---------------------------------------------------------------------------
# API debug tab
# ---------------------------------------------------------------------------

def render_api_debug_tab(game_date: date) -> None:
    """Render the API Diagnostics tab."""
    ui.section("API diagnostics")
    ui.note(
        "Fetch raw responses from every active provider to check the data before it is "
        "normalized. API keys are never shown."
    )

    from config import get_credentials
    creds = get_credentials()

    if not creds.available_providers:
        st.warning(
            "No live API providers are configured. "
            "Add your `BALLDONTLIE_API_KEY` to `.env`, or enable Kalshi/Polymarket "
            "(`KALSHI_ENABLED` / `POLYMARKET_ENABLED`).",
            icon=":material/key_off:",
        )
        return

    st.markdown("**Active providers:** " + ", ".join(f"`{p}`" for p in creds.available_providers))
    st.caption("Endpoints probed: games for the date, player props and odds, game lines.")

    if st.button("Run API diagnostics", type="primary", icon=":material/play_arrow:", key="run_debug"):
        # Reset registry AND bypass disk cache so we always see live HTTP calls.
        from data.loaders import reset_registry, load_games, load_game_lines, load_odds
        reset_registry()

        # Temporarily disable disk cache so a cached response doesn't
        # silently skip the network entirely.
        import os
        _prev_cache = os.environ.get("DISK_CACHE_ENABLED", "true")
        os.environ["DISK_CACHE_ENABLED"] = "false"

        with st.spinner("Calling APIs and collecting raw responses…"):
            with capture_api_responses(max_list=5) as responses:
                try:
                    load_games(game_date)
                except Exception:
                    pass
                try:
                    load_odds(game_date)
                except Exception:
                    pass
                try:
                    load_game_lines(game_date)
                except Exception:
                    pass

        os.environ["DISK_CACHE_ENABLED"] = _prev_cache

        st.session_state["debug_responses"] = responses
        st.session_state["debug_date"] = game_date

    responses = st.session_state.get("debug_responses")
    last_date = st.session_state.get("debug_date")

    if responses is None:
        st.info("Press **Run API diagnostics** to fetch live data.", icon=":material/info:")
        return

    if not responses:
        st.warning(
            "No HTTP calls were captured. This usually means all providers fell "
            "back to sample data without making network requests."
        )
        return

    st.success(f"Captured **{len(responses)}** API call(s) for {last_date}.", icon=":material/check_circle:")

    for i, rec in enumerate(responses, 1):
        status = rec.get("status")
        ok = rec.get("ok", False)
        provider = rec.get("provider", "Unknown")
        url = rec.get("url", "")
        item_count = rec.get("item_count")
        error = rec.get("error")

        if status is None:
            badge = ":red-badge[Error]"
        elif ok:
            badge = f":green-badge[{status}]"
        else:
            badge = f":red-badge[{status}]"

        count_label = f", {item_count} items" if item_count is not None else ""
        title = f"{i}. {provider} {badge}{count_label}"

        with st.expander(title, expanded=(i == 1)):
            st.markdown(f"**URL:** `{url}`")

            if error:
                st.error(f"Error: {error}")

            raw = rec.get("raw")
            preview = rec.get("preview")

            if raw is None:
                st.warning("No response body captured.")
                continue

            col_raw, col_preview = st.columns([1, 1])

            with col_preview:
                st.markdown("**Preview** _(first few items / fields)_")
                st.json(preview if preview is not None else raw)

            with col_raw:
                st.markdown("**Full raw response**")
                if isinstance(raw, (dict, list)):
                    st.json(raw, expanded=False)
                else:
                    st.code(str(raw)[:5000], language="text")


# ---------------------------------------------------------------------------
# AI advisor tab
# ---------------------------------------------------------------------------

def render_ai_advisor_tab(all_props: list, params: dict) -> None:
    """AI Advisor — deterministic picks/parlays + OpenAI reasoning over the scanned props."""
    from config import get_openai_config
    from engine.advisor.report import to_markdown
    from engine.advisor.run import AI_OK, AdvisorOptions, run_advisor
    from engine.advisor.prompts import default_user_prompt

    ui.section("AI Advisor", "a second opinion on the model's best picks")
    ui.note(
        "Python filters the scanned props (positive edge, odds band) and computes every payout "
        "and expected value. OpenAI then reasons over the top picks by ID only, so it never "
        "invents a price."
    )
    cfg = get_openai_config()
    if not cfg.is_configured:
        st.warning(
            "OPENAI_API_KEY is not set in `.env`. The advisor still runs, but only the "
            "deterministic recommendation is shown (no AI reasoning).",
            icon=":material/key_off:",
        )
    else:
        st.caption(f"Model: `{cfg.model}`, reasoning effort `{cfg.reasoning_effort}`.")

    if not all_props:
        st.info("Scan the slate first. The advisor reuses that scan.", icon=":material/radar:")
        return

    with st.container(border=True):
        c1, c2, c3, c4, c5 = st.columns(5)
        stake = c1.number_input("Stake ($)", min_value=1.0, value=100.0, step=10.0, key="adv_stake")
        min_odds = c2.number_input("Min odds", value=-600, step=10, key="adv_min_odds")
        max_odds = c3.number_input("Max odds", value=-200, step=10, key="adv_max_odds")
        min_edge_pct = c4.number_input("Edge above (%)", value=0.0, step=0.5, key="adv_min_edge")
        top_n = c5.number_input("Top picks", min_value=1, max_value=10, value=5, step=1, key="adv_top")
        k1, k2 = st.columns(2)
        min_conf = k1.selectbox(
            "Minimum confidence", ["(none)", "very_low", "low", "medium", "high"], key="adv_min_conf",
            format_func=lambda v: "None" if v == "(none)" else v.replace("_", " ").capitalize(),
        )
        bankroll = k2.number_input(
            "Bankroll for Kelly sizing ($, 0 = off)", min_value=0.0, value=0.0, step=100.0, key="adv_bankroll"
        )
        default_prompt = default_user_prompt(
            min_odds=int(min_odds), max_odds=int(max_odds), top_n=int(top_n), stake=stake
        )
        # Keyed text_areas ignore later `value=` changes; keep the default question in
        # sync with the band/stake/top-N widgets unless the user has edited it.
        prev_default = st.session_state.get("adv_prompt_default")
        if "adv_prompt" not in st.session_state or st.session_state["adv_prompt"] == prev_default:
            st.session_state["adv_prompt"] = default_prompt
        st.session_state["adv_prompt_default"] = default_prompt
        prompt = st.text_area("Question for the AI", height=100, key="adv_prompt")
        b1, b2 = st.columns([1, 2], vertical_alignment="center")
        use_llm = b2.toggle("Use OpenAI reasoning", value=cfg.is_configured, key="adv_use_llm")
        run_clicked = b1.button(
            "Run AI Advisor", type="primary", icon=":material/auto_awesome:", key="adv_run",
            width="stretch",
        )

    if run_clicked:
        options = AdvisorOptions(
            min_odds=int(min_odds),
            max_odds=int(max_odds),
            min_edge=float(min_edge_pct) / 100.0,
            top_n=int(top_n),
            stake=float(stake),
            bankroll=float(bankroll) or None,
            min_confidence=None if min_conf == "(none)" else min_conf,
            prompt=prompt,
            use_llm=use_llm,
        )
        counts = (st.session_state.get("odds_source_counts") or ({}, {}))[0]
        with st.spinner("Running AI Advisor..."):
            st.session_state.advisor_result = run_advisor(
                params["date"], options, props=all_props, odds_source_counts=counts
            )

    result = st.session_state.get("advisor_result")
    if result is None:
        return
    if result.game_date != params["date"]:
        st.caption(f"Showing the last advisor run for {result.game_date}.")

    s = result.stats
    ui.funnel([
        ("props", s.total_props),
        ("positive edge", s.positive_edge),
        ("in odds band", s.in_odds_band),
        ("after confidence", s.confidence_ok),
        ("unique", s.deduped),
        ("selected", s.selected),
    ])
    if not result.picks:
        st.warning("No props qualified with these settings, so the AI was not called.")
        return

    ui.section("Picks")
    st.dataframe(pd.DataFrame([{
        "ID": r.id,
        "Player": r.prop.player_name,
        "Prop": f"{ui.prop_label(r.prop.prop_type)} {r.prop.side.value} {r.prop.line:g}",
        "Odds": format_american(r.prop.sportsbook_odds),
        "Venue": book_display_name(r.prop.best_book, getattr(r.prop, "best_book_key", "") or ""),
        "Market": format_prob(r.prop.market_probability) if r.prop.market_probability is not None else "–",
        "Model wt": f"{r.prop.model_weight:.2f}",
        "True": format_prob(r.prop.true_probability),
        "Implied": format_prob(r.prop.implied_probability),
        "Edge": format_edge(r.prop.edge),
        "Conf": r.prop.confidence.value,
        f"Return on {format_currency(result.options.stake)}": format_currency(r.total_return),
        "EV": f"{r.expected_value:+.2f}",
        "Kelly %": f"{r.kelly_fraction:.1%}",
        "Kelly stake": format_currency(r.kelly_stake(result.options.bankroll)) if result.options.bankroll else "–",
    } for r in result.picks]), width="stretch", hide_index=True)

    if result.parlays:
        ui.section("Parlay combinations")
        st.dataframe(pd.DataFrame([{
            "ID": p.id,
            "Legs": " + ".join(f"{i} {result.pick_by_id[i].prop.player_name}" for i in p.leg_ids),
            "Odds": format_american(p.combined_american_odds),
            "Implied": format_prob(p.implied_probability),
            "Model true": format_prob(p.true_probability),
            "Edge": format_edge(p.edge),
            "Return": format_currency(p.total_return),
            "Profit": format_currency(p.profit),
            "EV": f"{p.expected_value:+.2f}",
            "Kelly %": f"{p.kelly_fraction:.1%}",
            "Venue": "single" if p.single_venue else "mixed",
        } for p in result.parlays[:10]]), width="stretch", hide_index=True)

    if result.ai_status == AI_OK:
        meta = result.llm_meta
        usage = meta.get("usage") or {}
        st.success(
            f"AI reasoning by {meta.get('model')} in {meta.get('latency_s', 0):.1f}s, "
            f"tokens in/out {usage.get('input_tokens', 0)}/{usage.get('output_tokens', 0)}.",
            icon=":material/auto_awesome:",
        )
    elif result.ai_error:
        reason = result.ai_error.rstrip(". ")
        st.error(f"AI layer failed: {reason}. Showing the deterministic recommendation.")

    md = to_markdown(result)
    section = md.split("## AI reasoning", 1)[-1] if "## AI reasoning" in md else md.split(
        "## Deterministic recommendation", 1)[-1]
    with st.container(border=True):
        # Streamlit renders $...$ as LaTeX; escape currency signs.
        st.markdown(section.replace("$", "\\$"))
    if result.json_path:
        st.caption(f"Saved: `{result.json_path}` and `{result.md_path}`")


# ---------------------------------------------------------------------------
# App main
# ---------------------------------------------------------------------------

def main() -> None:
    params = render_sidebar()

    render_masthead(params["date"])

    # Session state for results
    if "all_props" not in st.session_state:
        st.session_state.all_props = []
    if "parlays" not in st.session_state:
        st.session_state.parlays = []
    if "games" not in st.session_state:
        st.session_state.games = []
    if "debug_responses" not in st.session_state:
        st.session_state.debug_responses = None
    if "debug_date" not in st.session_state:
        st.session_state.debug_date = None
    if "game_lines" not in st.session_state:
        st.session_state.game_lines = []
    if "odds_source_counts" not in st.session_state:
        st.session_state.odds_source_counts = ({}, {})

    if params["run"]:
        with st.spinner("Scanning the slate and pulling Kalshi, Polymarket and sportsbook odds..."):
            from data.loaders import load_games, reset_registry
            from odds.game_lines import apply_game_lines
            reset_registry()
            games = load_games(params["date"])
            scanner = SlateScanner()
            all_props = scanner.scan(
                params["date"],
                prop_types=params["prop_types"],
                is_playoff=params.get("is_playoff", False),
            )
            if games and scanner.last_game_lines:
                apply_game_lines(games, scanner.last_game_lines)
            st.session_state.games = games
            st.session_state.all_props = all_props
            st.session_state.game_lines = scanner.last_game_lines
            st.session_state.odds_source_counts = (
                scanner.last_odds_source_counts,
                scanner.last_odds_source_errors,
            )
        st.session_state.candidate_status = None
        if all_props:
            with st.spinner("Scoring props with the frozen candidate model..."):
                from engine.candidate.live import attach_candidate
                try:
                    st.session_state.candidate_status = attach_candidate(all_props, params["date"], games=games)
                except Exception as exc:  # the board works without the candidate
                    st.session_state.candidate_status = {"status": "error", "reason": str(exc)}

        if not all_props:
            if params["date"] > today_eastern():
                st.warning(
                    f"No props found for {params['date']}. "
                    "This is a future date, so lines may not be posted yet.",
                    icon=":material/schedule:",
                )
            else:
                st.error(
                    f"No props found for {params['date']}. "
                    "Only pregame matchups are evaluated, so if every game has started or finished "
                    "the slate is empty. Otherwise check the data sources and API keys.",
                    icon=":material/search_off:",
                )
            st.session_state.parlays = []

        constraints = ParlayConstraints(
            min_edge=params["min_edge"],
            max_legs=params["max_legs"],
            min_legs=params["min_legs"],
            min_leg_odds=params["min_leg_odds"],
            max_leg_odds=params["max_leg_odds"],
            min_parlay_odds=params["min_parlay_odds"],
            max_parlay_odds=params["max_parlay_odds"],
            min_confidence=params["min_confidence"],
            allowed_prop_types=params["prop_types"],
            max_results=1000,
        )

        with st.spinner("Building parlays..."):
            parlays = build_parlays(all_props, constraints)
            ranked = rank_parlays(parlays, sort_by=params["sort_by"], top_n=500)
            apply_stake_to_all(ranked, params["stake"])
            st.session_state.parlays = ranked

        if all_props:
            st.toast(
                f"Evaluated {len(all_props):,} prop lines and built {len(parlays):,} parlays.",
                icon=":material/check_circle:",
            )

    all_props = st.session_state.all_props
    parlays = st.session_state.parlays

    if all_props:
        qualifying_count = len(_qualifying_props_for_display(all_props, params))
        ui.tiles([
            ("Games on the slate", f"{len(st.session_state.games)}", ""),
            ("Prop lines evaluated", f"{len(all_props):,}", ""),
            ("Pass your filters", f"{qualifying_count:,}", "court"),
            ("Parlays built", f"{len(parlays):,}", ""),
        ])
        cand = st.session_state.get("candidate_status") or {}
        if cand.get("status") == "ok":
            st.caption(
                f"Candidate model ({cand.get('version')}) scored {cand['attached']:,} of {cand['props']:,} prop lines "
                f"({cand['players_scored']} players with 5+ games this season; "
                f"{cand.get('injured_out_on_slate', 0)} rotation players listed out; "
                f"{cand.get('games_with_spread', 0)} games with a spread). "
                "It is shown for comparison and does not change edges or rankings."
            )
        elif cand:
            st.caption(f"Candidate model not shown: {cand.get('status')}. {cand.get('reason', '')}")
    elif not params["run"]:
        ui.empty_state()

    # Games slate (shown whenever games are available)
    if st.session_state.games:
        render_games_slate(st.session_state.games)
    render_odds_sources(*st.session_state.odds_source_counts)

    # Tab layout
    (tab_straight, tab_parlays, tab_props, tab_lines, tab_game_lines, tab_advisor, tab_debug, tab_backtest,
     tab_arb) = st.tabs([
        ":material/target: Straight bets",
        ":material/stacks: Parlays",
        ":material/table_chart: Props",
        ":material/compare_arrows: Line shopping",
        ":material/stadium: Game lines",
        ":material/auto_awesome: AI Advisor",
        ":material/lan: API diagnostics",
        ":material/query_stats: Backtesting",
        ":material/currency_exchange: Arbitrage",
    ], key="main_tabs")

    scan_first = "Scan the slate from the sidebar to fill this in."

    with tab_straight:
        if all_props:
            qualifying = _qualifying_props_for_display(all_props, params)
            render_straight_bets(qualifying, params["stake"], params["top_straight"])
            render_favorite_band_straight_audit(all_props, params)
        else:
            st.info(scan_first, icon=":material/radar:")

    with tab_parlays:
        if parlays:
            render_parlay_cards(parlays, params["stake"], params["top_parlays"])
        elif all_props:
            render_parlay_cards([], params["stake"], params["top_parlays"])
        else:
            st.info(scan_first, icon=":material/radar:")

    with tab_props:
        if all_props:
            qualifying = _qualifying_props_for_display(all_props, params)
            ui.section(
                "Props analysis",
                f"{len(qualifying):,} of {len(all_props):,} pass edge ≥ {format_edge(params['min_edge'])} "
                f"and odds {params['min_leg_odds']} to {params['max_leg_odds']}",
            )
            render_props_table(qualifying)
        else:
            st.info(scan_first, icon=":material/radar:")

    with tab_lines:
        if all_props:
            qualifying = _qualifying_props_for_display(all_props, params)
            render_line_shopping(qualifying[:50])
        else:
            st.info(scan_first, icon=":material/radar:")

    with tab_game_lines:
        if params["run"] or st.session_state.game_lines:
            render_game_lines(st.session_state.game_lines)
        else:
            st.info(scan_first, icon=":material/radar:")

    with tab_advisor:
        render_ai_advisor_tab(all_props, params)

    with tab_debug:
        render_api_debug_tab(params["date"])

    with tab_backtest:
        render_backtest_tab()

    with tab_arb:
        render_arbitrage_tab()

    ui.footer()


if __name__ == "__main__":
    main()
