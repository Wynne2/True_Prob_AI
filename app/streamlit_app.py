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

from domain.enums import ConfidenceTier, PropType, SortField
from engine.bankroll_engine import apply_stake_to_all, payout_summary
from engine.parlay_builder import ParlayConstraints, build_parlays
from engine.ranking_engine import rank_parlays, summary_stats
from engine.slate_scanner import SlateScanner
from utils.date_utils import today_eastern
from utils.formatting import format_american, format_edge, format_prob, format_currency

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

        st.divider()
        st.subheader("Parlay Constraints")

        min_edge = st.slider(
            "Min Edge per Leg (%)", min_value=0, max_value=20, value=5, step=1
        ) / 100.0

        max_legs = st.selectbox("Max Legs", options=[2, 3, 4, 5], index=1)
        min_legs = st.selectbox("Min Legs", options=[2, 3], index=0)

        st.divider()
        st.subheader("Odds Range")
        col1, col2 = st.columns(2)
        with col1:
            min_leg_odds = st.number_input("Min Leg Odds", value=-200, step=10)
        with col2:
            max_leg_odds = st.number_input("Max Leg Odds", value=400, step=10)

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
        sort_by = st.selectbox(
            "Sort Parlays By",
            options=[s.value for s in SortField],
            index=0,
        )

        run = st.button("🔍 Scan & Build Parlays", type="primary", use_container_width=True)

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
        "sort_by": SortField(sort_by),
        "run": run,
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
            st.markdown("**Using Sample Data Fallback**")
            for p in missing:
                st.markdown(f"⬜ `{p}`")


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
            "Projected": round(p.projected_value, 1),
            "True %": round(p.true_probability * 100, 1),
            "Implied %": round(p.implied_probability * 100, 1),
            "Edge %": round(p.edge * 100, 1),
            "Odds": format_american(p.sportsbook_odds),
            "Fair Odds": format_american(p.fair_odds),
            "Best Book": p.best_book.value,
            "Confidence": p.confidence.value,
        })

    df = pd.DataFrame(rows).sort_values("Edge %", ascending=False)

    def highlight_edge(val):
        if val >= 8:
            return "color: #50fa7b; font-weight: bold"
        elif val >= 5:
            return "color: #f1fa8c"
        return "color: #ff5555"

    styled = df.style.applymap(highlight_edge, subset=["Edge %"])
    st.dataframe(styled, use_container_width=True, hide_index=True)


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
                    "Book": leg.sportsbook.value,
                    "Confidence": leg.confidence.value,
                })

            st.dataframe(pd.DataFrame(leg_rows), use_container_width=True, hide_index=True)

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
            row[f"{ol.book.value}_over"] = format_american(ol.over_odds)
            row[f"{ol.book.value}_under"] = format_american(ol.under_odds)
        rows.append(row)

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df.head(30), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# App main
# ---------------------------------------------------------------------------

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

    if params["run"]:
        with st.spinner("Scanning slate and evaluating props..."):
            scanner = SlateScanner()
            all_props = scanner.scan(params["date"], prop_types=params["prop_types"])
            st.session_state.all_props = all_props

        st.success(f"Evaluated {len(all_props)} prop lines.")

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

    # Tab layout
    tab_parlays, tab_props, tab_lines = st.tabs([
        "🎯 Parlays", "📊 Props Analysis", "🛒 Line Shopping"
    ])

    all_props = st.session_state.all_props
    parlays = st.session_state.parlays

    with tab_parlays:
        if parlays:
            render_parlay_cards(parlays, params["stake"], params["top_parlays"])
        else:
            st.info("Click 'Scan & Build Parlays' in the sidebar to get started.")

    with tab_props:
        if all_props:
            qualifying = [p for p in all_props if p.edge >= params["min_edge"]]
            st.markdown(
                f"**{len(qualifying)} qualifying props** with ≥{format_edge(params['min_edge'])} edge "
                f"(out of {len(all_props)} total evaluated)"
            )
            render_props_table(qualifying)
        else:
            st.info("Run the scan to see prop analysis.")

    with tab_lines:
        if all_props:
            qualifying = [p for p in all_props if p.edge >= params["min_edge"]]
            render_line_shopping(qualifying[:50])
        else:
            st.info("Run the scan to see line shopping data.")

    # Footer
    st.divider()
    st.caption(
        "NBA Prop AI | For informational and research purposes only. "
        "Bet responsibly. Past model performance does not guarantee future results."
    )


if __name__ == "__main__":
    main()
