"""
NBA Prop AI – Streamlit Dashboard.

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
from engine.straight_bet_audit import (
    build_favorite_band_audit_table,
    format_favorite_band_summary_markdown,
    pipeline_drop_averages_for_positive_edge_favorites,
    top_uncapped_minus_fair_gap,
)
from utils.api_debug import capture_api_responses
from utils.date_utils import today_eastern
from utils.formatting import book_display_name, format_american, format_edge, format_prob, format_currency
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="NBA Prop AI",
    page_icon="🏀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS
st.markdown("""
<style>
    .stApp { background: #0f172a; }
    .stMetric {
        background: #172033;
        border: 1px solid #28364d;
        border-radius: 12px;
        padding: 14px;
    }
    div[data-testid="stSidebar"] { background: #111827; }
    .mode-banner {
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 12px 16px;
        background: #172033;
        margin: 8px 0 20px;
    }
    .onboarding-card {
        border-left: 4px solid #38bdf8;
        padding: 12px 16px;
        background: #172033;
        border-radius: 8px;
        margin-bottom: 16px;
    }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar: Constraints
# ---------------------------------------------------------------------------

def render_sidebar() -> dict:
    """Render sidebar controls and return constraint dict."""
    with st.sidebar:
        st.title("NBA Prop AI")
        st.caption("Research dashboard for player-prop forecasts")
        st.divider()

        st.subheader("1. Run")
        selected_date = st.date_input("Slate date", value=today_eastern(), key="game_date")

        if selected_date > today_eastern():
            st.warning(
                f"Future date selected ({selected_date}). "
                "Lines may not yet be posted and data may be incomplete.",
                icon="⚠️",
            )
        elif selected_date < today_eastern():
            st.caption(f"Historical date — showing data for {selected_date}.")

        is_playoff = st.checkbox(
            "Playoff slate",
            value=False,
            help="Boosts projected minutes slightly for playoff rotation patterns.",
        )

        st.divider()
        st.subheader("2. Bet filters")

        min_edge = st.slider(
            "Minimum edge (%)", min_value=0, max_value=20, value=5, step=1,
            help="Applies to straight bets, tables, line shopping, and parlays.",
        ) / 100.0
        st.caption("Example: −600 to +400 includes typical favorites and underdogs.")
        col1, col2 = st.columns(2)
        with col1:
            min_leg_odds = st.number_input(
                "Min Leg Odds",
                value=-600,
                step=10,
                key="min_leg_odds_band_v3",
                help="American odds lower bound; favorites are negative.",
            )
        with col2:
            max_leg_odds = st.number_input(
                "Max Leg Odds",
                value=400,
                step=10,
                key="max_leg_odds_band_v3",
            )

        from config import get_settings
        live_scope = {PropType.POINTS, PropType.REBOUNDS, PropType.ASSISTS}
        all_prop_types = [
            prop_type.value
            for prop_type in PropType
            if get_settings().app_mode != "live" or prop_type in live_scope
        ]
        selected_props = st.multiselect(
            "Prop Types (blank = all)", options=all_prop_types, default=[]
        )

        st.divider()
        st.subheader("3. Parlay filters")
        st.caption("Only affects theoretical same-book parlays.")
        max_legs = st.selectbox("Maximum legs", options=[2, 3, 4, 5], index=1)
        min_legs = st.selectbox("Minimum legs", options=[2, 3], index=0)
        min_confidence = st.selectbox(
            "Minimum confidence",
            options=["Any", "low", "medium", "high"],
            index=0,
        )
        col3, col4 = st.columns(2)
        with col3:
            min_parlay_odds = st.number_input("Min parlay odds", value=-10000, step=50)
        with col4:
            max_parlay_odds = st.number_input("Max parlay odds", value=100000, step=50)

        st.divider()
        st.subheader("4. Display")
        stake = st.number_input("Stake Amount ($)", min_value=1.0, value=100.0, step=10.0)
        top_parlays = st.number_input("Top Parlays to Show", min_value=1, value=10, step=1)
        top_straight = st.number_input("Top Straight Bets to Show", min_value=1, value=15, step=5)
        sort_by = st.selectbox(
            "Sort Parlays By",
            options=[s.value for s in SortField],
            index=0,
        )

        run = st.button("Scan slate", type="primary", use_container_width=True)

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

def render_provider_status() -> None:
    from config import get_credentials, get_settings
    creds = get_credentials()
    settings = get_settings()
    available = creds.available_providers
    missing = creds.missing_providers

    with st.expander("Provider Status", expanded=False):
        from data.loaders import provider_status
        status = provider_status()
        if settings.app_mode == "demo":
            st.warning(
                "Demo mode uses deterministic sample data. Do not treat output as live odds or a wagering recommendation."
            )
        elif settings.app_mode == "replay":
            st.info("Replay mode disables live providers and reads only saved evaluation records.")
        elif status["state"] != "ready":
            st.error(f"Live provider unavailable: `{status['reason']}`. No demo fallback is used.")
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Active Providers**")
            for p in available:
                st.markdown(f"✅ `{p}`")
            if not available:
                st.markdown("_No live providers configured_")
        with col_b:
            st.markdown("**Missing Keys (no data)**")
            for p in missing:
                st.markdown(f"⬜ `{p}`")
            if not missing:
                st.markdown("_All providers configured_")


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

    st.subheader(f"Today's Slate — {len(games)} game{'s' if len(games) != 1 else ''}")
    cols = st.columns(min(len(games), 3))
    eastern = ZoneInfo("America/New_York")

    for i, game in enumerate(games):
        col = cols[i % len(cols)]
        with col:
            matchup = f"{game.away_team_abbr} @ {game.home_team_abbr}"

            # Format tip-off time in Eastern
            if game.tip_off_time:
                try:
                    tip_et = game.tip_off_time.astimezone(eastern)
                    tip_str = tip_et.strftime("%-I:%M %p ET").lstrip("0")
                except Exception:
                    tip_str = str(game.tip_off_time)
            else:
                tip_str = "TBD"

            venue_parts = [p for p in [game.arena, game.city] if p]
            venue_str = " · ".join(venue_parts) if venue_parts else ""

            st.markdown(f"**{matchup}**")
            st.caption(f"🕐 {tip_str}" + (f"  |  📍 {venue_str}" if venue_str else ""))
    st.divider()


def render_props_table(props: list) -> None:
    """Render the qualifying props as a sortable DataFrame."""
    if not props:
        st.warning("No qualifying props found for current constraints.")
        return

    rows = []
    for p in props:
        rows.append({
            "Player": p.player_name,
            "Team": p.team_abbr,
            "Opp": p.opponent_abbr,
            "Prop": p.prop_type.value,
            "Line": p.line,
            "Side": p.side.value.upper(),
            "Baseline": round(getattr(p, "baseline_projection", 0) or p.projected_value, 1),
            "Projected": round(p.projected_value, 1),
            "Exp Min": round(getattr(p, "expected_minutes", 0), 1),
            "True %": round(p.true_probability * 100, 1),
            "Implied %": round(p.implied_probability * 100, 1),
            "Edge %": round(p.edge * 100, 1),
            "Odds": format_american(p.sportsbook_odds),
            "Fair Odds": format_american(p.fair_odds),
            "Best Book": book_display_name(p.best_book, getattr(p, "best_book_key", "") or ""),
            "Confidence": p.confidence.value,
            "Data quality": "; ".join(getattr(p, "data_quality_flags", []) or []),
            "Warnings": "; ".join(getattr(p, "calibration_warnings", []) or []),
        })

    df = pd.DataFrame(rows).sort_values("Edge %", ascending=False)

    def highlight_edge(val):
        if val >= 8:
            return "color: #50fa7b; font-weight: bold"
        elif val >= 5:
            return "color: #f1fa8c"
        return "color: #ff5555"

    styled = df.style.map(highlight_edge, subset=["Edge %"])
    st.dataframe(styled, width="stretch", hide_index=True)


def _prop_leg_key(prop) -> tuple:
    """Identity for one evaluated leg (same book price can vary; odds included for dedupe)."""
    return (
        prop.player_id,
        prop.prop_type,
        prop.line,
        prop.side,
        prop.sportsbook_odds,
    )


def render_straight_bets(props: list, stake: float, top_n: int) -> None:
    """Render top straight-bet recommendations as individual cards with payouts."""
    if not props:
        st.warning(
            "No qualifying straight bets found. Try lowering min edge or widening the **leg odds** range "
            "(same filter as parlays)."
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

    st.markdown(
        f"### Top {len(sorted_props)} Straight Bets "
        f"_(of {len(props)} qualifying props)_"
    )

    # Summary row
    avg_edge = sum(p.edge for p in sorted_props) / len(sorted_props)
    avg_true = sum(p.true_probability for p in sorted_props) / len(sorted_props)
    best_edge = sorted_props[0].edge
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Qualifying Props", len(props))
    m2.metric("Avg Edge", f"{avg_edge * 100:.1f}%")
    m3.metric("Best Edge", f"{best_edge * 100:.1f}%")
    m4.metric("Avg True Prob", f"{avg_true * 100:.1f}%")

    st.divider()

    def _payout(american_odds: float, wager: float) -> float:
        if american_odds >= 100:
            return wager * (american_odds / 100)
        else:
            return wager * (100 / abs(american_odds))

    def _straight_bet_expander(rank: int, prop, expanded_top: int) -> None:
        book_label = book_display_name(prop.best_book, getattr(prop, "best_book_key", "") or "")
        net_profit = _payout(prop.sportsbook_odds, stake)
        total_return = stake + net_profit

        header = (
            f"#{rank}  {prop.player_name}  —  "
            f"{prop.prop_type.value} {prop.side.value.upper()} {prop.line}  |  "
            f"{format_american(prop.sportsbook_odds)} @ {book_label}  |  "
            f"Edge: {format_edge(prop.edge)}"
        )

        with st.expander(header, expanded=(rank <= expanded_top)):
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("True Prob", format_prob(prop.true_probability))
            c2.metric("Implied Prob", format_prob(prop.implied_probability))
            c3.metric("Edge", format_edge(prop.edge))
            c4.metric("Projected", f"{prop.projected_value:.1f}")
            c5.metric("Confidence", prop.confidence.value.capitalize())

            wm = getattr(prop, "calibration_warnings", None) or []
            if wm:
                st.caption("Calibration: " + ", ".join(wm))
            if getattr(prop, "expected_minutes", 0):
                st.caption(
                    f"Baseline ~{getattr(prop, 'baseline_projection', prop.projected_value):.1f} · "
                    f"Exp min ~{prop.expected_minutes:.0f}"
                )

            st.divider()

            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Stake", format_currency(stake))
            p2.metric("Odds", format_american(prop.sportsbook_odds))
            p3.metric("Net Profit", format_currency(net_profit))
            p4.metric("Total Return", format_currency(total_return))

            st.markdown(
                f"**Book:** `{book_label}`  &nbsp;|&nbsp;  "
                f"**Fair Odds:** `{format_american(prop.fair_odds)}`  &nbsp;|&nbsp;  "
                f"**Team:** `{prop.team_abbr}` vs `{prop.opponent_abbr}`"
            )

            if hasattr(prop, "explanation") and prop.explanation:
                st.caption(prop.explanation)

    for rank, prop in enumerate(sorted_props, 1):
        _straight_bet_expander(rank, prop, expanded_top=3)

    if favorite_not_in_top:
        st.divider()
        st.markdown(
            f"### Favorite-priced legs (minus money, not in top {top_n} by edge)"
        )
        st.caption(
            "Includes −115 through −600 style prices. Those legs can miss the headline list when "
            "higher-edge plays are mostly on plus-money. Same min-edge and leg-odds filters apply."
        )
        for rank, prop in enumerate(favorite_not_in_top, 1):
            _straight_bet_expander(rank, prop, expanded_top=2)


def render_favorite_band_straight_audit(all_props: list, params: dict) -> None:
    """
    Full audit for legs whose best American price is in the evaluator favorite band
    (see domain.constants FAVORITE_STRAIGHT_BET_AUDIT_BAND_*).
    """
    st.subheader(
        f"Favorite-band audit (evaluator: {FAVORITE_STRAIGHT_BET_AUDIT_BAND_LOW} to "
        f"{FAVORITE_STRAIGHT_BET_AUDIT_BAND_HIGH})"
    )
    st.caption(
        "Straight bets use **only** min edge + leg odds (same as the qualifying table). "
        "**Min confidence** applies to **parlays** only, not this tab."
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

    st.subheader("Top 10: uncapped true prob − fair implied (model vs de-vig market)")
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

    st.subheader("Pipeline drop averages (final edge > 0, favorite band)")
    drops = pipeline_drop_averages_for_positive_edge_favorites(all_props)
    st.json(drops)
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
    """Render each top parlay as an expander card with leg details."""
    if not parlays:
        st.warning(
            "No positive-EV same-book theoretical parlays were generated for the current filters."
        )
        return

    st.markdown(f"### Top {min(top_n, len(parlays))} Parlays  _(of {len(parlays)} total)_")
    st.caption(
        "Combined prices are theoretical products of singles available from one bookmaker. "
        "They are not provider-quoted parlay prices."
    )

    # Summary metrics row
    stats = summary_stats(parlays)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Parlays", stats.get("count", 0))
    m2.metric("Avg Edge", f"{stats.get('avg_edge', 0)*100:.1f}%")
    m3.metric("Max Edge", f"{stats.get('max_edge', 0)*100:.1f}%")
    m4.metric("Avg Legs", f"{stats.get('avg_legs', 0):.1f}")

    st.divider()

    for parlay in parlays[:top_n]:
        tags = " | ".join(parlay.risk_profile_tags) if parlay.risk_profile_tags else ""
        tag_label = f"  🏷️ {tags}" if tags else ""

        with st.expander(
            f"#{parlay.edge_rank}  {parlay.num_legs}-leg  "
            f"{format_american(parlay.combined_american_odds)}  "
            f"Edge: {format_edge(parlay.combined_edge)}"
            f"{tag_label}",
            expanded=(parlay.edge_rank == 1),
        ):
            ticket_book = book_display_name(
                parlay.ticket_book,
                getattr(parlay, "ticket_book_key", "") or "",
            )
            st.caption(
                f"Theoretical same-book ticket at **{ticket_book}**. "
                "Verify the offered parlay price before placing a wager."
            )
            # Metrics
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Combined Odds", format_american(parlay.combined_american_odds))
            c2.metric("True Prob", format_prob(parlay.combined_true_probability))
            c3.metric("Implied Prob", format_prob(parlay.combined_implied_probability))
            c4.metric("Expected Return", f"{parlay.expected_return * 100:.1f}%")
            c5.metric("Corr Risk", f"{parlay.correlation_risk_score:.2f}")

            # Payout
            if parlay.stake > 0:
                p1, p2, p3 = st.columns(3)
                p1.metric("Stake", format_currency(parlay.stake))
                p2.metric("Total Return", format_currency(parlay.total_return))
                p3.metric("Net Profit", format_currency(parlay.net_profit))

            st.divider()

            # Legs table
            leg_rows = []
            for leg in parlay.legs:
                leg_rows.append({
                    "Player": leg.player_name,
                    "Team": leg.team_abbr,
                    "vs": leg.opponent_abbr,
                    "Prop": leg.prop_type.value,
                    "Line": leg.line,
                    "Side": leg.side.value.upper(),
                    "Projected": round(leg.projected_value, 1),
                    "True %": round(leg.true_probability * 100, 1),
                    "Edge %": round(leg.edge * 100, 1),
                    "Odds": format_american(leg.sportsbook_odds),
                    "Book": book_display_name(leg.sportsbook, getattr(leg, "best_book_key", "") or ""),
                    "Confidence": leg.confidence.value,
                })

            st.dataframe(pd.DataFrame(leg_rows), width="stretch", hide_index=True)

            # Explanations
            with st.expander("Leg Explanations", expanded=False):
                for leg in parlay.legs:
                    st.markdown(f"**{leg.player_name} {leg.prop_type.value} {leg.side.value.upper()} {leg.line}**")
                    st.caption(leg.explanation)


def render_line_shopping(props: list) -> None:
    """Show a multi-book odds comparison table."""
    if not props:
        return

    st.subheader("Line Shopping – Multi-Book Comparison")

    # Aggregate all available lines
    from collections import defaultdict
    from domain.entities import OddsLine

    lines_by_player_prop: dict = defaultdict(list)
    for prop in props:
        for ol in prop.all_lines:
            key = (ol.game_id, prop.player_name, prop.prop_type.value, ol.line)
            lines_by_player_prop[key].append(ol)

    rows = []
    for (event_id, player, prop_type, line), book_lines in lines_by_player_prop.items():
        row = {"Event": event_id, "Player": player, "Prop": prop_type, "Line": line}
        for ol in book_lines:
            book_key = ol.book_key or ol.book.value
            row[f"{book_key}_over"] = format_american(ol.over_odds)
            row[f"{book_key}_under"] = format_american(ol.under_odds)
        rows.append(row)

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df.head(30), width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

def render_overview(games: list, props: list, parlays: list, params: dict) -> None:
    """Provide a clear first-run path and a compact scan summary."""
    if not props:
        st.markdown(
            """
            <div class="onboarding-card">
            <strong>Start here</strong><br/>
            1. Choose a slate date and filters in the sidebar.<br/>
            2. Select <strong>Scan slate</strong>.<br/>
            3. Review individual forecasts before exploring theoretical parlays.
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.info(
            "No scan is loaded. Demo mode uses deterministic example data; "
            "live mode requires configured providers."
        )
        return

    qualifying = _qualifying_props_for_display(props, params)
    best = max(qualifying, key=lambda prop: prop.edge) if qualifying else None
    high_confidence = sum(
        prop.confidence.value == "high" for prop in qualifying
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pregame games", len(games))
    c2.metric("Evaluated offers", len(props))
    c3.metric("Qualifying straight bets", len(qualifying))
    c4.metric("Theoretical parlays", len(parlays))

    if best:
        st.success(
            f"Highest current edge: **{best.player_name} {best.prop_type.value.title()} "
            f"{best.side.value.upper()} {best.line:g}** at "
            f"**{book_display_name(best.best_book, best.best_book_key)}** "
            f"({format_edge(best.edge)} edge)."
        )
    else:
        st.warning(
            "No offers meet the current filters. Lower minimum edge, widen the "
            "odds band, or review the full Prop Table."
        )
    st.caption(
        f"{high_confidence} qualifying offer(s) are marked high confidence. "
        "Forecasts are research outputs, not guarantees."
    )


def render_updates(updates: list, exclusions: list[dict]) -> None:
    """Show provider-observed quote changes and transparent scan exclusions."""
    with st.expander("Updates and data quality", expanded=False):
        if updates:
            st.markdown("**Quote updates**")
            for update in updates[:20]:
                st.caption(f"{update.occurred_at.isoformat()} · {update.message}")
        else:
            st.caption("No comparable quote changes have been observed in this session.")
        if exclusions:
            st.markdown("**Excluded inputs**")
            st.dataframe(pd.DataFrame(exclusions), width="stretch", hide_index=True)
        elif not updates:
            st.caption("No exclusions were recorded for the latest scan.")


# ---------------------------------------------------------------------------
# API debug tab
# ---------------------------------------------------------------------------

def render_api_debug_tab(game_date: date) -> None:
    """Render the API Diagnostics tab."""
    st.subheader("🔬 API Diagnostics")
    st.caption(
        "Fetch raw responses from every active API provider so you can verify "
        "the data before it gets normalised. API keys are never shown."
    )

    from config import get_credentials
    creds = get_credentials()

    if not creds.available_providers:
        st.warning(
            "No live API providers are configured. "
            "Add your `BALLDONTLIE_API_KEY` to `.env` to enable data fetching."
        )
        return

    st.markdown("**Active providers:** " + "  •  ".join(f"`{p}`" for p in creds.available_providers))
    st.markdown("**Endpoints probed:** games for date, player props / odds")
    st.divider()

    if st.button("▶ Run API Diagnostics", type="primary", key="run_debug"):
        # Reset registry AND bypass disk cache so we always see live HTTP calls.
        from data.loaders import reset_registry, load_games, load_odds
        reset_registry()

        # Temporarily disable disk cache so SportsDataIO / Sportradar don't
        # silently serve stale data and skip the network entirely.
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

        os.environ["DISK_CACHE_ENABLED"] = _prev_cache

        st.session_state["debug_responses"] = responses
        st.session_state["debug_date"] = game_date

    responses = st.session_state.get("debug_responses")
    last_date = st.session_state.get("debug_date")

    if responses is None:
        st.info("Click **▶ Run API Diagnostics** to fetch live data.")
        return

    if not responses:
        st.warning(
            "No HTTP calls were captured. This usually means all providers fell "
            "back to sample data without making network requests."
        )
        return

    st.success(f"Captured **{len(responses)}** API call(s) for {last_date}.")

    for i, rec in enumerate(responses, 1):
        status = rec.get("status")
        ok = rec.get("ok", False)
        provider = rec.get("provider", "Unknown")
        url = rec.get("url", "")
        item_count = rec.get("item_count")
        error = rec.get("error")

        if status is None:
            badge = "⛔ ERROR"
            badge_color = "red"
        elif ok:
            badge = f"✅ {status}"
            badge_color = "green"
        else:
            badge = f"❌ {status}"
            badge_color = "red"

        count_label = f" — {item_count} items" if item_count is not None else ""
        title = f"**#{i}  {provider}**  `{badge}`{count_label}"

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
                    st.json(raw)
                else:
                    st.code(str(raw)[:5000], language="text")


# ---------------------------------------------------------------------------
# Evaluation tab
# ---------------------------------------------------------------------------

def render_evaluation_tab() -> None:
    """Render the append-only historical forecast scorecard."""
    st.subheader("📐 Forecast Evaluation")
    st.caption(
        "Scores only settled historical forecasts. Sample/demo results are useful for "
        "testing the ledger, not evidence of live-model profitability."
    )
    from services.evaluation_store import EvaluationStore

    report = EvaluationStore().report()
    if report.settled_count == 0:
        st.info("No settled win/loss forecasts are recorded yet.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Settled offers", report.settled_count)
    c2.metric("Brier score", f"{report.brier_score:.4f}")
    c3.metric("Log loss", f"{report.log_loss:.4f}")
    c4.metric("Quoted return / unit", f"{report.quoted_return_per_unit * 100:.1f}%")
    st.markdown("**Reliability by forecast-probability bucket**")
    st.dataframe(pd.DataFrame(report.reliability), width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# App main
# ---------------------------------------------------------------------------

def main() -> None:
    params = render_sidebar()

    from config import get_settings
    settings = get_settings()
    mode_label = settings.app_mode.upper()
    mode_copy = {
        "demo": "Deterministic examples only — not live odds or a wagering recommendation.",
        "live": "Uses configured live providers. Review quote freshness before acting.",
        "replay": "Live providers are disabled; results come from saved historical records.",
    }.get(settings.app_mode, "Unknown runtime mode.")

    st.title("NBA Prop AI")
    st.caption(f"Player-prop research workspace · Viewing {params['date']}")
    st.markdown(
        f'<div class="mode-banner"><strong>{mode_label} MODE</strong> · {mode_copy}</div>',
        unsafe_allow_html=True,
    )

    render_provider_status()

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
    if "updates" not in st.session_state:
        st.session_state.updates = []
    if "exclusions" not in st.session_state:
        st.session_state.exclusions = []

    if params["run"]:
        with st.status("Scanning slate", expanded=True) as scan_status:
            st.write("Loading scheduled games and available prices…")
            from data.loaders import load_games, reset_registry
            reset_registry()
            games = load_games(params["date"])
            st.session_state.games = games
            st.write("Evaluating eligible player props…")
            scanner = SlateScanner()
            all_props = scanner.scan(
                params["date"],
                prop_types=params["prop_types"],
                is_playoff=params.get("is_playoff", False),
            )
            st.session_state.all_props = all_props
            st.session_state.updates = scanner.last_updates
            st.session_state.exclusions = scanner.last_exclusions
            st.write("Building same-book theoretical parlays…")
            scan_status.update(label="Slate scan complete", state="complete")

        if not all_props:
            if params["date"] > today_eastern():
                st.warning(
                    f"No props found for {params['date']}. "
                    "This is a future date — lines may not be posted yet."
                )
            else:
                st.error(
                    f"No props found for {params['date']}. "
                    "Only pregame matchups are evaluated — if every game has started or finished, "
                    "the slate is empty. Otherwise check providers / API keys."
                )
            st.session_state.parlays = []
        else:
            st.success(
                f"Evaluated {len(all_props)} prop lines (pregame games only — in progress / final excluded)."
            )

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

        if settings.app_mode == "live":
            st.session_state.parlays = []
            st.info("Parlays are disabled during the initial live P/R/A rollout.")
        else:
            with st.spinner("Building parlays..."):
                parlays = build_parlays(all_props, constraints)
                ranked = rank_parlays(parlays, sort_by=params["sort_by"], top_n=500)
                apply_stake_to_all(ranked, params["stake"])
                st.session_state.parlays = ranked
            st.success(f"Generated {len(parlays)} positive-EV theoretical parlays.")

    # Games slate (shown whenever games are available)
    if st.session_state.games:
        render_games_slate(st.session_state.games)

    # Tab layout
    tab_overview, tab_straight, tab_parlays, tab_props, tab_lines, tab_evaluation, tab_debug = st.tabs([
        "Overview", "Straight Bets", "Parlays", "Prop Table", "Line Shopping",
        "Model Evaluation", "Diagnostics"
    ])

    all_props = st.session_state.all_props
    parlays = st.session_state.parlays

    with tab_overview:
        render_overview(st.session_state.games, all_props, parlays, params)
        render_updates(st.session_state.updates, st.session_state.exclusions)

    with tab_straight:
        if all_props:
            qualifying = _qualifying_props_for_display(all_props, params)
            render_straight_bets(qualifying, params["stake"], params["top_straight"])
            st.divider()
            render_favorite_band_straight_audit(all_props, params)
        else:
            st.info("Choose filters in the sidebar, then select 'Scan slate' to get started.")

    with tab_parlays:
        if parlays:
            render_parlay_cards(parlays, params["stake"], params["top_parlays"])
        else:
            st.info("Choose filters in the sidebar, then select 'Scan slate' to get started.")

    with tab_props:
        if all_props:
            qualifying = _qualifying_props_for_display(all_props, params)
            st.markdown(
                f"**{len(qualifying)} qualifying props** with ≥{format_edge(params['min_edge'])} edge "
                f"and leg odds in [{params['min_leg_odds']}, {params['max_leg_odds']}] "
                f"(out of {len(all_props)} total evaluated)"
            )
            render_props_table(qualifying)
        else:
            st.info("Run the scan to see prop analysis.")

    with tab_lines:
        if all_props:
            qualifying = _qualifying_props_for_display(all_props, params)
            render_line_shopping(qualifying[:50])
        else:
            st.info("Run the scan to see line shopping data.")

    with tab_evaluation:
        render_evaluation_tab()

    with tab_debug:
        render_api_debug_tab(params["date"])

    # Footer
    st.divider()
    st.caption(
        "NBA Prop AI | For informational and research purposes only. "
        "Bet responsibly. Past model performance does not guarantee future results."
    )


if __name__ == "__main__":
    main()
