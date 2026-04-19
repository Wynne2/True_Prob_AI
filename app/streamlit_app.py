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


def render_straight_bets(props: list, stake: float, top_n: int) -> None:
    """Render top straight-bet recommendations as individual cards with payouts."""
    if not props:
        st.warning("No qualifying straight bets found. Try lowering the min edge.")
        return

    sorted_props = sorted(props, key=lambda p: p.edge, reverse=True)[:top_n]

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

    for rank, prop in enumerate(sorted_props, 1):
        edge_pct = prop.edge * 100
        book_label = book_display_name(prop.best_book, getattr(prop, "best_book_key", "") or "")
        net_profit = _payout(prop.sportsbook_odds, stake)
        total_return = stake + net_profit

        if edge_pct >= 8:
            edge_color = "#50fa7b"
        elif edge_pct >= 5:
            edge_color = "#f1fa8c"
        else:
            edge_color = "#ff9955"

        header = (
            f"#{rank}  {prop.player_name}  —  "
            f"{prop.prop_type.value} {prop.side.value.upper()} {prop.line}  |  "
            f"{format_american(prop.sportsbook_odds)} @ {book_label}  |  "
            f"Edge: {format_edge(prop.edge)}"
        )

        with st.expander(header, expanded=(rank <= 3)):
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
            row[f"{ol.book.value}_over"] = format_american(ol.over_odds)
            row[f"{ol.book.value}_under"] = format_american(ol.under_odds)
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
    if "games" not in st.session_state:
        st.session_state.games = []
    if "debug_responses" not in st.session_state:
        st.session_state.debug_responses = None
    if "debug_date" not in st.session_state:
        st.session_state.debug_date = None

    if params["run"]:
        with st.spinner("Scanning slate and evaluating props..."):
            from data.loaders import load_games, reset_registry
            reset_registry()
            games = load_games(params["date"])
            st.session_state.games = games
            scanner = SlateScanner()
            all_props = scanner.scan(
                params["date"],
                prop_types=params["prop_types"],
                is_playoff=params.get("is_playoff", False),
            )
            st.session_state.all_props = all_props

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

    # Tab layout
    tab_straight, tab_parlays, tab_props, tab_lines, tab_debug = st.tabs([
        "🎲 Straight Bets", "🎯 Parlays", "📊 Props Analysis", "🛒 Line Shopping", "🔬 API Debug"
    ])

    all_props = st.session_state.all_props
    parlays = st.session_state.parlays

    with tab_straight:
        if all_props:
            qualifying = [p for p in all_props if p.edge >= params["min_edge"]]
            render_straight_bets(qualifying, params["stake"], params["top_straight"])
        else:
            st.info("Click 'Scan & Build' in the sidebar to get started.")

    with tab_parlays:
        if parlays:
            render_parlay_cards(parlays, params["stake"], params["top_parlays"])
        else:
            st.info("Click 'Scan & Build' in the sidebar to get started.")

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
