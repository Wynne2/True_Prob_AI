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
    .stMetric { background: #1e1e2e; border-radius: 8px; padding: 12px; }
    .edge-high { color: #50fa7b; font-weight: bold; }
    .edge-med  { color: #f1fa8c; font-weight: bold; }
    .edge-low  { color: #ff5555; }
    div[data-testid="stSidebar"] { background-color: #181825; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar: Constraints
# ---------------------------------------------------------------------------

def render_sidebar() -> dict:
    """Render sidebar controls and return constraint dict."""
    with st.sidebar:
        st.title("🏀 NBA Prop AI")
        st.caption("True Probability Parlay Builder")
        st.divider()

        # Date selector
        selected_date = st.date_input("Game Date", value=today_eastern(), key="game_date")

        if selected_date > today_eastern():
            st.warning(
                f"Future date selected ({selected_date}). "
                "Lines may not yet be posted and data may be incomplete.",
                icon="⚠️",
            )
        elif selected_date < today_eastern():
            st.caption(f"Historical date — showing data for {selected_date}.")

        st.divider()
        is_playoff = st.checkbox(
            "Playoff slate",
            value=False,
            help="Boosts projected minutes slightly for playoff rotation patterns.",
        )

        st.divider()
        st.subheader("Parlay Constraints")

        min_edge = st.slider(
            "Min Edge per Leg (%)", min_value=0.0, max_value=20.0, value=1.0, step=0.5,
            help="Market-anchored edges are realistic (typically 1–4%); 5%+ is rare.",
        ) / 100.0

        max_legs = st.selectbox("Max Legs", options=[2, 3, 4, 5], index=1)
        min_legs = st.selectbox("Min Legs", options=[2, 3], index=0)

        st.divider()
        st.subheader("Odds Range")
        st.caption(
            "Applies to **Straight Bets**, **Props Analysis**, **Line Shopping**, and **Parlays**. "
            "Bounds are numeric (e.g. favorites −600 to −110: enter −600 and −110; order can be swapped). "
            "**Min Leg Odds** must be ≤ the steepest favorite you want (e.g. −600 includes −210 and −250)."
        )
        col1, col2 = st.columns(2)
        with col1:
            min_leg_odds = st.number_input(
                "Min Leg Odds",
                value=-600,
                step=10,
                key="min_leg_odds_band_v3",
                help="American odds lower bound (signed). Favorites are negative; "
                "must be ≤ your longest prices (e.g. −600 includes −210, −250). "
                "If you still see no heavy favorites after an update, this value was reset to −600.",
            )
        with col2:
            max_leg_odds = st.number_input(
                "Max Leg Odds",
                value=400,
                step=10,
                key="max_leg_odds_band_v3",
            )

        col3, col4 = st.columns(2)
        with col3:
            min_parlay_odds = st.number_input("Min Parlay Odds", value=-10000, step=50)
        with col4:
            max_parlay_odds = st.number_input("Max Parlay Odds", value=100000, step=50)

        st.divider()
        st.subheader("Prop Filters")

        all_prop_types = [pt.value for pt in PropType]
        selected_props = st.multiselect(
            "Prop Types (blank = all)", options=all_prop_types, default=[]
        )

        min_confidence = st.selectbox(
            "Min Confidence",
            options=["Any", "low", "medium", "high"],
            index=0,
        )

        st.divider()
        st.subheader("Stake")
        stake = st.number_input("Stake Amount ($)", min_value=1.0, value=100.0, step=10.0)

        st.divider()
        st.subheader("Display")
        top_parlays = st.number_input("Top Parlays to Show", min_value=1, value=10, step=1)
        top_straight = st.number_input("Top Straight Bets to Show", min_value=1, value=15, step=5)
        sort_by = st.selectbox(
            "Sort Parlays By",
            options=[s.value for s in SortField],
            index=0,
        )

        run = st.button("🔍 Scan & Build", type="primary", use_container_width=True)

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
    from config import get_credentials
    creds = get_credentials()
    available = creds.available_providers
    missing = creds.missing_providers

    with st.expander("Provider Status", expanded=False):
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
            if game.game_total or game.home_spread:
                spread = f"{game.home_team_abbr} {game.home_spread:+g}" if game.home_spread else "PK"
                st.caption(
                    f"📈 Total {game.game_total:g} · Spread {spread}"
                    + (
                        f" · Implied {game.away_team_abbr} {game.away_implied_total:g}"
                        f" / {game.home_team_abbr} {game.home_implied_total:g}"
                        if game.home_implied_total else ""
                    )
                )
    st.divider()


_SOURCE_LABELS = {"kalshi": "Kalshi", "polymarket": "Polymarket", "odds_api": "The Odds API"}


def render_odds_sources(counts: dict, errors: dict) -> None:
    """Show how many prop lines each odds source contributed this scan."""
    if not counts and not errors:
        return
    names = sorted(set(counts) | set(errors))
    cols = st.columns(len(names))
    for col, name in zip(cols, names):
        with col:
            st.metric(f"{_SOURCE_LABELS.get(name, name)} prop lines", counts.get(name, 0))
            if errors.get(name):
                st.caption(f"⚠️ {errors[name][:120]}")


def render_game_lines(game_lines: list) -> None:
    """Moneyline / spread / total sides from the prediction markets."""
    if not game_lines:
        st.info("No prediction-market game lines for this date.")
        return
    from odds.game_lines import consensus_game_lines

    consensus = consensus_game_lines(game_lines)
    if consensus:
        st.markdown("**Consensus main lines** (strike nearest 50/50, median across exchanges)")
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

    st.markdown("**All priced sides** (fee-inclusive cost → American odds)")
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
        st.warning("No parlays generated. Try lowering min edge or expanding odds range.")
        return

    st.markdown(f"### Top {min(top_n, len(parlays))} Parlays  _(of {len(parlays)} total)_")

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
            # Metrics
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Combined Odds", format_american(parlay.combined_american_odds))
            c2.metric("True Prob", format_prob(parlay.combined_true_probability))
            c3.metric("Implied Prob", format_prob(parlay.combined_implied_probability))
            c4.metric("Edge", format_edge(parlay.combined_edge))
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
            key = (prop.player_name, prop.prop_type.value, ol.line)
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
            "Add your `BALLDONTLIE_API_KEY` to `.env`, or enable Kalshi/Polymarket "
            "(`KALSHI_ENABLED` / `POLYMARKET_ENABLED`)."
        )
        return

    st.markdown("**Active providers:** " + "  •  ".join(f"`{p}`" for p in creds.available_providers))
    st.markdown("**Endpoints probed:** games for date, player props / odds, game lines")
    st.divider()

    if st.button("▶ Run API Diagnostics", type="primary", key="run_debug"):
        # Reset registry AND bypass disk cache so we always see live HTTP calls.
        from data.loaders import reset_registry, load_games, load_game_lines, load_odds
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
# App main
# ---------------------------------------------------------------------------

def render_ai_advisor_tab(all_props: list, params: dict) -> None:
    """🤖 AI Advisor — deterministic picks/parlays + OpenAI reasoning over the scanned props."""
    from config import get_openai_config
    from engine.advisor.report import to_markdown
    from engine.advisor.run import AI_OK, AdvisorOptions, run_advisor
    from engine.advisor.prompts import default_user_prompt

    st.subheader("🤖 AI Advisor")
    st.caption(
        "Python filters the scanned props (positive edge, odds band), computes every payout and EV, "
        "then OpenAI reasons over the top picks by ID only. Odds come from Kalshi / Polymarket."
    )
    cfg = get_openai_config()
    if not cfg.is_configured:
        st.warning(
            "OPENAI_API_KEY is not set in `.env`. The advisor will still run, but only the "
            "deterministic recommendation is shown (no AI reasoning)."
        )
    else:
        st.caption(f"Model: `{cfg.model}` · reasoning effort `{cfg.reasoning_effort}`")

    if not all_props:
        st.info("Click 'Scan & Build' in the sidebar first; the advisor reuses that scan.")
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    stake = c1.number_input("Stake ($)", min_value=1.0, value=100.0, step=10.0, key="adv_stake")
    min_odds = c2.number_input("Min odds", value=-600, step=10, key="adv_min_odds")
    max_odds = c3.number_input("Max odds", value=-200, step=10, key="adv_max_odds")
    min_edge_pct = c4.number_input("Edge > (%)", value=0.0, step=0.5, key="adv_min_edge")
    top_n = c5.number_input("Top N", min_value=1, max_value=10, value=5, step=1, key="adv_top")
    k1, k2 = st.columns(2)
    min_conf = k1.selectbox(
        "Minimum confidence", ["(none)", "very_low", "low", "medium", "high"], key="adv_min_conf"
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
    use_llm = st.checkbox("Use OpenAI reasoning", value=cfg.is_configured, key="adv_use_llm")

    if st.button("Run AI Advisor", type="primary", key="adv_run"):
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
    st.markdown(
        f"**Filter funnel:** {s.total_props} props → {s.positive_edge} positive edge → "
        f"{s.in_odds_band} in odds band → {s.confidence_ok} after confidence → "
        f"{s.deduped} unique → **{s.selected} selected**"
    )
    if not result.picks:
        st.warning("No props qualified with these settings, so the AI was not called.")
        return

    st.dataframe(pd.DataFrame([{
        "ID": r.id,
        "Player": r.prop.player_name,
        "Prop": f"{r.prop.prop_type.value.upper()} {r.prop.side.value} {r.prop.line:g}",
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
            f"AI reasoning by {meta.get('model')} · {meta.get('latency_s', 0):.1f}s · "
            f"tokens in/out {usage.get('input_tokens', 0)}/{usage.get('output_tokens', 0)}"
        )
    elif result.ai_error:
        reason = result.ai_error.rstrip(". ")
        st.error(f"AI layer failed: {reason}. Showing the deterministic recommendation.")

    md = to_markdown(result)
    section = md.split("## AI reasoning", 1)[-1] if "## AI reasoning" in md else md.split(
        "## Deterministic recommendation", 1)[-1]
    # Streamlit renders $...$ as LaTeX; escape currency signs.
    st.markdown(section.replace("$", "\\$"))
    if result.json_path:
        st.caption(f"Saved: `{result.json_path}` and `{result.md_path}`")


def main() -> None:
    params = render_sidebar()

    st.title("🏀 NBA Prop AI – True Probability Parlay Builder")
    st.caption(
        f"Scan today's NBA props, find positive-edge plays, and build optimised parlays. "
        f"Viewing: **{params['date']}**"
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
    if "game_lines" not in st.session_state:
        st.session_state.game_lines = []
    if "odds_source_counts" not in st.session_state:
        st.session_state.odds_source_counts = ({}, {})

    if params["run"]:
        with st.spinner("Scanning slate and pulling Kalshi / Polymarket / sportsbook odds..."):
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

        with st.spinner("Building parlays..."):
            parlays = build_parlays(all_props, constraints)
            ranked = rank_parlays(parlays, sort_by=params["sort_by"], top_n=500)
            apply_stake_to_all(ranked, params["stake"])
            st.session_state.parlays = ranked

        st.success(f"Generated {len(parlays)} valid parlays.")

    # Games slate (shown whenever games are available)
    if st.session_state.games:
        render_games_slate(st.session_state.games)
    render_odds_sources(*st.session_state.odds_source_counts)

    # Tab layout
    tab_straight, tab_parlays, tab_props, tab_lines, tab_game_lines, tab_advisor, tab_debug, tab_backtest = st.tabs([
        "🎲 Straight Bets", "🎯 Parlays", "📊 Props Analysis", "🛒 Line Shopping",
        "🏟️ Game Lines", "🤖 AI Advisor", "🔬 API Debug", "📏 Backtesting",
    ], key="main_tabs")

    all_props = st.session_state.all_props
    parlays = st.session_state.parlays

    with tab_straight:
        if all_props:
            qualifying = _qualifying_props_for_display(all_props, params)
            render_straight_bets(qualifying, params["stake"], params["top_straight"])
            st.divider()
            render_favorite_band_straight_audit(all_props, params)
        else:
            st.info("Click 'Scan & Build' in the sidebar to get started.")

    with tab_parlays:
        if parlays:
            render_parlay_cards(parlays, params["stake"], params["top_parlays"])
        else:
            st.info("Click 'Scan & Build' in the sidebar to get started.")

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

    with tab_game_lines:
        if params["run"] or st.session_state.game_lines:
            render_game_lines(st.session_state.game_lines)
        else:
            st.info("Run the scan to see Kalshi / Polymarket game lines.")

    with tab_advisor:
        render_ai_advisor_tab(all_props, params)

    with tab_debug:
        render_api_debug_tab(params["date"])

    with tab_backtest:
        render_backtest_tab()

    # Footer
    st.divider()
    st.caption(
        "NBA Prop AI | For informational and research purposes only. "
        "Bet responsibly. Past model performance does not guarantee future results."
    )


if __name__ == "__main__":
    main()
