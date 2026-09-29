"""Premium Streamlit workspace for NBA player-prop research."""

from __future__ import annotations

import logging
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import streamlit as st

from app.design_system import (
    chip,
    empty_state,
    inject_design_system,
    render_brand,
    render_hero,
    section_heading,
)
from domain.constants import (
    FAVORITE_STRAIGHT_BET_AUDIT_BAND_HIGH,
    FAVORITE_STRAIGHT_BET_AUDIT_BAND_LOW,
)
from domain.enums import PropType, SortField
from engine.bankroll_engine import apply_stake_to_all
from engine.parlay_builder import ParlayConstraints, build_parlays, leg_odds_match_constraints
from engine.ranking_engine import rank_parlays, summary_stats
from engine.slate_scanner import SlateScanner
from engine.straight_bet_audit import (
    build_favorite_band_audit_table,
    format_favorite_band_summary_markdown,
    pipeline_drop_averages_for_positive_edge_favorites,
    top_uncapped_minus_fair_gap,
)
from utils.date_utils import today_eastern
from utils.formatting import (
    book_display_name,
    format_american,
    format_currency,
    format_edge,
    format_prob,
)

logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="True Prob · NBA Intelligence",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get help": None,
        "Report a bug": None,
        "About": "True Prob is a read-only NBA player-prop research workspace.",
    },
)
inject_design_system()


def _initialize_state() -> None:
    defaults = {
        "all_props": [],
        "parlays": [],
        "games": [],
        "updates": [],
        "exclusions": [],
        "debug_responses": None,
        "debug_date": None,
        "last_scan_at": None,
        "last_scan_seconds": None,
        "scan_error": None,
        "scanner": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _provider_context() -> tuple[dict[str, str], str]:
    from config import get_credentials, get_settings
    from data.loaders import provider_status

    settings = get_settings()
    status = provider_status()
    if settings.app_mode == "demo":
        provider_name = "Sample data"
    elif settings.app_mode == "replay":
        provider_name = "Offline ledger"
    else:
        provider_name = "BALLDONTLIE" if get_credentials().balldontlie_key else "No provider"
    return status, provider_name


def _status_tone(state: str) -> str:
    return {
        "ready": "success",
        "offline": "neutral",
        "blocked": "warning",
        "error": "danger",
    }.get(state, "neutral")


def render_sidebar() -> dict:
    """Render a concise setup flow with advanced controls progressively disclosed."""
    from config import get_settings

    settings = get_settings()
    status, provider_name = _provider_context()
    with st.sidebar:
        render_brand()
        state = status.get("state", "unknown")
        reason = status.get("reason") or (
            "Connected and ready" if state == "ready" else "Network disabled"
        )
        st.markdown(
            f"""
            <div class="tp-sidebar-status">
              <strong>{escape(settings.app_mode.title())} mode · {escape(provider_name)}</strong>
              <p>{escape(reason.replace("_", " ").title())}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("#### Scan setup")
        selected_date = st.date_input(
            "Slate date",
            value=today_eastern(),
            key="game_date",
            help="Only pregame events are evaluated in live mode.",
        )
        live_scope = {PropType.POINTS, PropType.REBOUNDS, PropType.ASSISTS}
        all_prop_types = [
            prop_type.value
            for prop_type in PropType
            if settings.app_mode != "live" or prop_type in live_scope
        ]
        selected_props = st.multiselect(
            "Markets",
            options=all_prop_types,
            default=[],
            placeholder="All supported markets",
            help="Leave blank to include every supported market.",
        )
        min_edge = st.slider(
            "Minimum model edge",
            min_value=0,
            max_value=20,
            value=5,
            step=1,
            format="%d%%",
            help="Difference between model probability and the posted implied probability.",
        ) / 100.0

        run = st.button(
            "Scan slate",
            type="primary",
            use_container_width=True,
            icon=":material/radar:",
        )
        if selected_date > today_eastern():
            st.caption("Future slate: markets may not be posted yet.")
        elif selected_date < today_eastern():
            st.caption("Historical slate: live pregame markets may be unavailable.")

        with st.expander("Price and display controls", expanded=False):
            odds_cols = st.columns(2)
            min_leg_odds = odds_cols[0].number_input(
                "Min odds",
                value=-600,
                step=10,
                key="min_leg_odds_band_v4",
            )
            max_leg_odds = odds_cols[1].number_input(
                "Max odds",
                value=400,
                step=10,
                key="max_leg_odds_band_v4",
            )
            stake = st.number_input(
                "Illustrative stake",
                min_value=1.0,
                value=100.0,
                step=10.0,
                help="Used only to calculate hypothetical returns.",
            )
            top_straight = st.slider(
                "Recommendations shown",
                min_value=5,
                max_value=30,
                value=12,
                step=1,
            )
            is_playoff = st.checkbox(
                "Playoff rotation context",
                value=False,
                help="Applies playoff-specific minute assumptions.",
            )

        with st.expander("Theoretical parlay lab", expanded=False):
            if settings.app_mode == "live":
                st.caption("Disabled in live mode until provider-quoted parlay pricing is supported.")
            max_legs = st.selectbox("Maximum legs", [2, 3, 4, 5], index=1)
            min_legs = st.selectbox("Minimum legs", [2, 3], index=0)
            min_confidence = st.selectbox(
                "Minimum confidence",
                ["Any", "low", "medium", "high"],
                index=0,
            )
            parlay_cols = st.columns(2)
            min_parlay_odds = parlay_cols[0].number_input(
                "Min ticket odds", value=-10000, step=50
            )
            max_parlay_odds = parlay_cols[1].number_input(
                "Max ticket odds", value=100000, step=50
            )
            top_parlays = st.slider("Parlays shown", 1, 20, 8)
            sort_by = st.selectbox(
                "Rank by",
                options=[sort_field.value for sort_field in SortField],
                index=0,
            )

        st.caption("Research only · No wagers are placed")

    return {
        "date": selected_date,
        "min_edge": min_edge,
        "max_legs": int(max_legs),
        "min_legs": int(min_legs),
        "min_leg_odds": int(min_leg_odds),
        "max_leg_odds": int(max_leg_odds),
        "min_parlay_odds": int(min_parlay_odds),
        "max_parlay_odds": int(max_parlay_odds),
        "prop_types": [PropType(value) for value in selected_props] if selected_props else None,
        "min_confidence": None if min_confidence == "Any" else min_confidence,
        "stake": float(stake),
        "top_parlays": int(top_parlays),
        "top_straight": int(top_straight),
        "sort_by": SortField(sort_by),
        "run": run,
        "is_playoff": is_playoff,
    }


def _qualifying_props_for_display(all_props: list, params: dict) -> list:
    return [
        prop
        for prop in all_props
        if prop.edge >= params["min_edge"]
        and leg_odds_match_constraints(
            prop.sportsbook_odds,
            params["min_leg_odds"],
            params["max_leg_odds"],
        )
    ]


def _format_tip(game) -> str:
    if not game.tip_off_time:
        return "Tip time TBD"
    try:
        eastern = ZoneInfo("America/New_York")
        return game.tip_off_time.astimezone(eastern).strftime("%I:%M %p ET").lstrip("0")
    except (TypeError, ValueError):
        return str(game.tip_off_time)


def render_games_slate(games: list) -> None:
    if not games:
        return
    section_heading(
        "Slate",
        f"{len(games)} pregame matchup{'s' if len(games) != 1 else ''}",
        "Tip times are shown in Eastern Time.",
    )
    columns = st.columns(min(len(games), 4))
    for index, game in enumerate(games):
        venue = " · ".join(part for part in [game.arena, game.city] if part) or "Venue TBD"
        columns[index % len(columns)].markdown(
            f"""
            <article class="tp-game">
              <div class="tp-game-time">{escape(_format_tip(game))}</div>
              <div class="tp-game-matchup">{escape(game.away_team_abbr)} @ {escape(game.home_team_abbr)}</div>
              <div class="tp-game-venue">{escape(venue)}</div>
            </article>
            """,
            unsafe_allow_html=True,
        )


def _payout(american_odds: float, wager: float) -> float:
    return wager * (american_odds / 100) if american_odds >= 100 else wager * (100 / abs(american_odds))


def _freshness_age_seconds(prop) -> float | None:
    quote = getattr(prop, "selected_quote", None)
    observed_at = getattr(quote, "fetched_at", None) if quote else None
    if observed_at is None:
        return None
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - observed_at).total_seconds())


def render_opportunity_card(rank: int, prop, stake: float) -> None:
    book = book_display_name(prop.best_book, getattr(prop, "best_book_key", "") or "")
    market = f"{prop.prop_type.value.title()} {prop.side.value.upper()} {prop.line:g}"
    freshness = _freshness_age_seconds(prop)
    freshness_label = "Unknown" if freshness is None else f"{int(freshness)}s ago"
    total_return = stake + _payout(prop.sportsbook_odds, stake)
    st.markdown(
        f"""
        <article class="tp-opportunity">
          <div class="tp-opportunity-top">
            <div>
              <div class="tp-rank">Recommendation {rank:02d}</div>
              <div class="tp-opportunity-name">{escape(prop.player_name)}</div>
              <div class="tp-opportunity-market">{escape(market)} · {escape(prop.team_abbr)} vs {escape(prop.opponent_abbr)}</div>
            </div>
            <div class="tp-edge">+{prop.edge * 100:.1f}% edge</div>
          </div>
          <div class="tp-opportunity-meta">
            <div class="tp-stat"><div class="tp-stat-label">Price</div><div class="tp-stat-value">{escape(format_american(prop.sportsbook_odds))}</div></div>
            <div class="tp-stat"><div class="tp-stat-label">True probability</div><div class="tp-stat-value">{prop.true_probability * 100:.1f}%</div></div>
            <div class="tp-stat"><div class="tp-stat-label">Projection</div><div class="tp-stat-value">{prop.projected_value:.1f}</div></div>
            <div class="tp-stat"><div class="tp-stat-label">Book</div><div class="tp-stat-value">{escape(book)}</div></div>
            <div class="tp-stat"><div class="tp-stat-label">Quote seen</div><div class="tp-stat-value">{escape(freshness_label)}</div></div>
          </div>
        </article>
        """,
        unsafe_allow_html=True,
    )
    with st.expander(f"Evidence and return · {prop.player_name} {market}", expanded=False):
        metric_columns = st.columns(4)
        metric_columns[0].metric("Model probability", format_prob(prop.true_probability))
        metric_columns[1].metric("Market probability", format_prob(prop.implied_probability))
        metric_columns[2].metric("Fair price", format_american(prop.fair_odds))
        metric_columns[3].metric("Illustrative return", format_currency(total_return))
        if getattr(prop, "explanation", ""):
            st.write(prop.explanation)
        details = []
        if getattr(prop, "expected_minutes", 0):
            details.append(f"Expected minutes: {prop.expected_minutes:.0f}")
        details.append(f"Confidence: {prop.confidence.value.title()}")
        st.caption(" · ".join(details))
        warnings = list(getattr(prop, "calibration_warnings", []) or [])
        quality = list(getattr(prop, "data_quality_flags", []) or [])
        if warnings:
            st.warning("Calibration: " + ", ".join(warnings))
        if quality:
            st.info("Data quality: " + ", ".join(quality))


def _recommendation_rows(props: list) -> list[dict]:
    return [
        {
            "Player": prop.player_name,
            "Matchup": f"{prop.team_abbr} vs {prop.opponent_abbr}",
            "Market": prop.prop_type.value.title(),
            "Pick": f"{prop.side.value.upper()} {prop.line:g}",
            "Projection": round(prop.projected_value, 1),
            "Model %": round(prop.true_probability * 100, 1),
            "Market %": round(prop.implied_probability * 100, 1),
            "Edge %": round(prop.edge * 100, 1),
            "Price": format_american(prop.sportsbook_odds),
            "Book": book_display_name(prop.best_book, getattr(prop, "best_book_key", "") or ""),
            "Confidence": prop.confidence.value.title(),
        }
        for prop in props
    ]


def render_market_table(props: list) -> None:
    if not props:
        empty_state("0", "No offers match this view", "Adjust the search or focus filter to restore recommendations.")
        return
    st.dataframe(
        pd.DataFrame(_recommendation_rows(props)),
        width="stretch",
        hide_index=True,
        column_config={
            "Edge %": st.column_config.ProgressColumn(
                "Edge",
                help="Model probability minus implied market probability.",
                format="%.1f%%",
                min_value=0,
                max_value=20,
            ),
            "Model %": st.column_config.NumberColumn("Model", format="%.1f%%"),
            "Market %": st.column_config.NumberColumn("Market", format="%.1f%%"),
            "Projection": st.column_config.NumberColumn("Projection", format="%.1f"),
        },
    )


def render_line_shopping(props: list) -> None:
    if not props:
        empty_state("↔", "No comparable prices yet", "Run a scan and broaden the recommendation filters.")
        return
    lines_by_market: dict[tuple, list] = defaultdict(list)
    for prop in props:
        for line in prop.all_lines:
            key = (line.game_id, prop.player_name, prop.prop_type.value, line.line)
            lines_by_market[key].append(line)
    rows = []
    for (event_id, player, prop_type, line), book_lines in lines_by_market.items():
        row = {"Event": event_id, "Player": player, "Market": prop_type.title(), "Line": line}
        for quote in book_lines:
            book_key = quote.book_key or quote.book.value
            row[f"{book_key} over"] = format_american(quote.over_odds)
            row[f"{book_key} under"] = format_american(quote.under_odds)
        rows.append(row)
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        empty_state("↔", "Only one price is available", "Multi-book comparison appears when equivalent quotes are observed.")


def render_parlay_lab(parlays: list, stake: float, top_n: int) -> None:
    from config import get_settings

    if get_settings().app_mode == "live":
        empty_state(
            "2+",
            "Live parlay pricing is intentionally disabled",
            "The app will not present synthetic ticket prices as executable offers. Use individual recommendations until provider-quoted parlays are supported.",
        )
        return
    if not parlays:
        empty_state(
            "2+",
            "No theoretical combinations qualify",
            "Broaden the odds band, reduce minimum edge, or run a new scan.",
        )
        return
    stats = summary_stats(parlays)
    metric_columns = st.columns(4)
    metric_columns[0].metric("Combinations", stats.get("count", 0))
    metric_columns[1].metric("Average edge", f"{stats.get('avg_edge', 0) * 100:.1f}%")
    metric_columns[2].metric("Maximum edge", f"{stats.get('max_edge', 0) * 100:.1f}%")
    metric_columns[3].metric("Average legs", f"{stats.get('avg_legs', 0):.1f}")
    st.caption("All prices below are theoretical products of single-leg prices. Verify any actual ticket independently.")
    for parlay in parlays[:top_n]:
        label = (
            f"#{parlay.edge_rank} · {parlay.num_legs} legs · "
            f"{format_american(parlay.combined_american_odds)} · "
            f"{format_edge(parlay.combined_edge)} edge"
        )
        with st.expander(label, expanded=parlay.edge_rank == 1):
            metrics = st.columns(4)
            metrics[0].metric("True probability", format_prob(parlay.combined_true_probability))
            metrics[1].metric("Expected return", f"{parlay.expected_return * 100:.1f}%")
            metrics[2].metric("Correlation risk", f"{parlay.correlation_risk_score:.2f}")
            metrics[3].metric("Illustrative return", format_currency(parlay.total_return))
            leg_rows = [
                {
                    "Player": leg.player_name,
                    "Market": leg.prop_type.value.title(),
                    "Pick": f"{leg.side.value.upper()} {leg.line:g}",
                    "Price": format_american(leg.sportsbook_odds),
                    "Edge": f"{leg.edge * 100:.1f}%",
                    "Book": book_display_name(leg.sportsbook, getattr(leg, "best_book_key", "") or ""),
                }
                for leg in parlay.legs
            ]
            st.dataframe(pd.DataFrame(leg_rows), width="stretch", hide_index=True)


def render_updates(updates: list, exclusions: list[dict]) -> None:
    update_tab, exclusion_tab = st.tabs(
        [f"Quote activity · {len(updates)}", f"Exclusions · {len(exclusions)}"]
    )
    with update_tab:
        if not updates:
            st.caption("No comparable quote changes have been observed during this session.")
        for update in updates[:30]:
            st.markdown(f"**{update.kind.replace('_', ' ').title()}**")
            st.caption(f"{update.occurred_at.astimezone().strftime('%b %d · %I:%M:%S %p')} · {update.message}")
    with exclusion_tab:
        if exclusions:
            st.dataframe(pd.DataFrame(exclusions), width="stretch", hide_index=True)
        else:
            st.caption("No inputs were excluded from the latest scan.")


def render_home(params: dict) -> None:
    all_props = st.session_state.all_props
    games = st.session_state.games
    parlays = st.session_state.parlays
    qualifying = _qualifying_props_for_display(all_props, params)
    if not all_props:
        empty_state(
            "◈",
            "Your decision board is ready",
            "Choose a slate date and select Scan slate. True Prob will collect eligible markets, evaluate the evidence, and explain every recommendation or exclusion.",
        )
        if st.session_state.scan_error:
            st.error(st.session_state.scan_error)
        render_games_slate(games)
        return

    best = max(qualifying, key=lambda prop: prop.edge) if qualifying else None
    high_confidence = sum(prop.confidence.value == "high" for prop in qualifying)
    quality_flags = sum(bool(getattr(prop, "data_quality_flags", [])) for prop in qualifying)
    metrics = st.columns(4)
    metrics[0].metric("Pregame games", len(games), "eligible slate")
    metrics[1].metric("Offers evaluated", len(all_props), "provider quotes")
    metrics[2].metric("Recommendations", len(qualifying), f"{high_confidence} high confidence")
    metrics[3].metric("Quality flags", quality_flags, "review before action")

    section_heading(
        "Priority signal",
        "Best current opportunity",
        "Ranked by model edge after availability, quote, and calibration checks.",
    )
    if best:
        render_opportunity_card(1, best, params["stake"])
    else:
        empty_state(
            "0",
            "No offer clears the current threshold",
            "Lower the minimum edge or widen the accepted odds range. The full evaluated market remains available in Research.",
        )

    left, right = st.columns([1.35, 1], gap="large")
    with left:
        section_heading("Market map", "Where the slate has signal")
        market_counts = Counter(prop.prop_type.value.title() for prop in qualifying)
        if market_counts:
            chart = pd.DataFrame(
                [{"Market": market, "Recommendations": count} for market, count in market_counts.items()]
            )
            st.bar_chart(chart, x="Market", y="Recommendations", color="#8b5cf6", height=290)
        else:
            st.caption("No qualifying market distribution is available.")
    with right:
        section_heading("Trust layer", "Scan readiness")
        status, provider_name = _provider_context()
        readiness = [
            ("Runtime", status.get("mode", "unknown").title()),
            ("Provider", provider_name),
            ("Provider state", status.get("state", "unknown").title()),
            ("Last scan", st.session_state.last_scan_at or "This session"),
            (
                "Scan duration",
                f"{st.session_state.last_scan_seconds:.1f}s"
                if st.session_state.last_scan_seconds is not None
                else "Not recorded",
            ),
        ]
        st.dataframe(
            pd.DataFrame(readiness, columns=["Check", "Status"]),
            width="stretch",
            hide_index=True,
        )

    render_games_slate(games)
    section_heading("Activity", "What changed and what was withheld")
    render_updates(st.session_state.updates, st.session_state.exclusions)


def _filter_opportunities(props: list, query: str, focus: str) -> list:
    filtered = list(props)
    normalized_query = query.strip().lower()
    if normalized_query:
        filtered = [
            prop
            for prop in filtered
            if normalized_query
            in " ".join(
                [
                    prop.player_name,
                    prop.team_abbr,
                    prop.opponent_abbr,
                    prop.prop_type.value,
                    prop.side.value,
                ]
            ).lower()
        ]
    if focus == "High confidence":
        filtered = [prop for prop in filtered if prop.confidence.value == "high"]
    elif focus == "Favorites":
        filtered = [prop for prop in filtered if prop.sportsbook_odds < 0]
    elif focus == "Needs review":
        filtered = [
            prop
            for prop in filtered
            if getattr(prop, "data_quality_flags", [])
            or getattr(prop, "calibration_warnings", [])
        ]
    return sorted(filtered, key=lambda prop: prop.edge, reverse=True)


def render_research(params: dict) -> None:
    all_props = st.session_state.all_props
    qualifying = _qualifying_props_for_display(all_props, params)
    if not all_props:
        empty_state(
            "⌕",
            "No research set is loaded",
            "Run a slate scan to unlock recommendations, market comparison, and the parlay lab.",
        )
        return

    section_heading(
        "Research workspace",
        "Triage the board",
        "Search the evaluated slate, focus the signal, then inspect the supporting evidence.",
    )
    search_col, focus_col = st.columns([1.4, 1])
    with search_col:
        query = st.text_input(
            "Search recommendations",
            placeholder="Player, team, opponent, or market",
            icon=":material/search:",
        )
    with focus_col:
        focus = st.selectbox(
            "Focus",
            ["All recommendations", "High confidence", "Favorites", "Needs review"],
        )
    focused = _filter_opportunities(qualifying, query, focus)
    st.caption(
        f"Showing {len(focused)} of {len(qualifying)} recommendations · "
        f"{len(all_props)} total offers evaluated"
    )

    board_tab, market_tab, lines_tab, parlay_tab = st.tabs(
        ["Recommendation board", "Market grid", "Line shop", "Parlay lab"]
    )
    with board_tab:
        if not focused:
            empty_state("0", "No recommendations match", "Clear the search or choose a broader focus.")
        for rank, prop in enumerate(focused[: params["top_straight"]], 1):
            render_opportunity_card(rank, prop, params["stake"])
    with market_tab:
        render_market_table(focused)
    with lines_tab:
        render_line_shopping(focused[:50])
    with parlay_tab:
        render_parlay_lab(st.session_state.parlays, params["stake"], params["top_parlays"])


def render_evaluation() -> None:
    from services.evaluation_store import EvaluationStore

    section_heading(
        "Performance",
        "Prospective model scorecard",
        "Only settled, timestamped forecasts are scored. Demo history is not evidence of live profitability.",
    )
    report = EvaluationStore().report()
    if report.settled_count == 0:
        empty_state(
            "∿",
            "No settled forecasts yet",
            "The prediction ledger is active. Performance analytics will appear after saved pregame forecasts are matched to final results.",
        )
        return
    metrics = st.columns(4)
    metrics[0].metric("Settled offers", report.settled_count)
    metrics[1].metric("Brier score", f"{report.brier_score:.4f}", "lower is better")
    metrics[2].metric("Log loss", f"{report.log_loss:.4f}", "lower is better")
    metrics[3].metric(
        "Quoted return / unit",
        f"{report.quoted_return_per_unit * 100:.1f}%",
        "historical simulation",
    )
    if report.reliability:
        section_heading(
            "Calibration",
            "Prediction confidence vs observed outcomes",
            "Well-calibrated probabilities track the diagonal as sample size grows.",
        )
        reliability = pd.DataFrame(report.reliability)
        reliability["Probability bucket"] = reliability.apply(
            lambda row: f"{row['lower_probability']:.0%}–{row['upper_probability']:.0%}",
            axis=1,
        )
        chart = reliability.rename(
            columns={"mean_predicted": "Model probability", "win_rate": "Observed win rate"}
        )
        st.line_chart(
            chart,
            x="Probability bucket",
            y=["Model probability", "Observed win rate"],
            color=["#8b5cf6", "#22d3ee"],
            height=360,
        )
        st.dataframe(
            chart[
                ["Probability bucket", "count", "Model probability", "Observed win rate"]
            ],
            width="stretch",
            hide_index=True,
            column_config={
                "count": st.column_config.NumberColumn("Sample"),
                "Model probability": st.column_config.NumberColumn("Model", format="%.1%%"),
                "Observed win rate": st.column_config.NumberColumn("Observed", format="%.1%%"),
            },
        )


def render_model_audit(all_props: list, params: dict) -> None:
    rows, summary = build_favorite_band_audit_table(
        all_props,
        params["min_leg_odds"],
        params["max_leg_odds"],
        params["min_edge"],
    )
    if not rows:
        st.caption(
            f"No evaluated offers are in the {FAVORITE_STRAIGHT_BET_AUDIT_BAND_LOW} to "
            f"{FAVORITE_STRAIGHT_BET_AUDIT_BAND_HIGH} audit band."
        )
        return
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.markdown(format_favorite_band_summary_markdown(summary))
    top_rows = top_uncapped_minus_fair_gap(
        all_props,
        params["min_leg_odds"],
        params["max_leg_odds"],
        params["min_edge"],
        top_n=10,
    )
    if top_rows:
        st.markdown("**Largest model-to-market gaps**")
        st.dataframe(pd.DataFrame(top_rows), width="stretch", hide_index=True)
    drops = pipeline_drop_averages_for_positive_edge_favorites(all_props)
    st.markdown("**Probability pipeline drop averages**")
    st.json(drops)


def render_api_diagnostics(game_date: date) -> None:
    from config import get_credentials, get_settings

    if get_settings().app_mode != "live":
        st.info("Provider diagnostics are available only in live mode; demo and replay make no external requests.")
        return
    credentials = get_credentials()
    if not credentials.available_providers:
        st.warning("Add BALLDONTLIE_API_KEY to .env before running live diagnostics.")
        return
    st.caption("Checks an uncached schedule and the first game's prop feed. The API key is never displayed.")
    if st.button(
        "Run provider diagnostics",
        type="secondary",
        key="run_debug",
        icon=":material/network_check:",
    ):
        from services.provider_diagnostics import diagnose_provider

        try:
            with st.status("Checking provider contract", expanded=True) as status:
                st.write("Requesting schedule and available player props…")
                responses = diagnose_provider(game_date, credentials.balldontlie_key)
                status.update(label="Provider diagnostics complete", state="complete")
        except Exception as exc:
            logger.exception("Provider diagnostics failed")
            st.error(f"Diagnostics failed: {exc}")
            responses = []
        st.session_state.debug_responses = responses
        st.session_state.debug_date = game_date

    responses = st.session_state.debug_responses
    if responses is None:
        return
    if not responses:
        st.warning("No diagnostic results were returned. Review the provider state and entitlements.")
        return
    st.success(f"Checked {len(responses)} endpoint(s) for {st.session_state.debug_date}.")
    for index, record in enumerate(responses, 1):
        with st.expander(
            f"{index}. {record['endpoint']} · {record['records']} records",
            expanded=index == 1,
        ):
            st.caption(f"Retrieved {record['fetched_at'].isoformat()}")
            st.json(record["preview"])


def render_system(params: dict) -> None:
    from config import get_credentials, get_settings

    settings = get_settings()
    credentials = get_credentials()
    status, provider_name = _provider_context()
    section_heading(
        "System",
        "Data trust and diagnostics",
        "Provider state, exclusions, quote activity, and model audit tools are isolated here so the primary decision workflow stays focused.",
    )
    metrics = st.columns(4)
    metrics[0].metric("Runtime mode", settings.app_mode.title())
    metrics[1].metric("Provider", provider_name)
    metrics[2].metric("Connection state", status.get("state", "unknown").title())
    metrics[3].metric("Excluded inputs", len(st.session_state.exclusions))

    status_tone = _status_tone(status.get("state", "unknown"))
    reason = status.get("reason") or "Provider contract is ready."
    st.markdown(
        '<div class="tp-chip-row">'
        + chip(f"{status.get('state', 'unknown').title()}: {reason.replace('_', ' ')}", status_tone)
        + chip(
            "Credential configured" if credentials.available_providers else "Credential missing",
            "success" if credentials.available_providers else "warning",
        )
        + chip(f"Quote age limit: {settings.max_quote_age_seconds}s", "neutral")
        + "</div>",
        unsafe_allow_html=True,
    )

    activity_tab, diagnostics_tab, audit_tab = st.tabs(
        ["Activity and exclusions", "Provider diagnostics", "Model audit"]
    )
    with activity_tab:
        render_updates(st.session_state.updates, st.session_state.exclusions)
    with diagnostics_tab:
        render_api_diagnostics(params["date"])
    with audit_tab:
        if st.session_state.all_props:
            render_model_audit(st.session_state.all_props, params)
        else:
            st.caption("Run a slate scan before opening the model audit.")


def _build_parlays(all_props: list, params: dict) -> list:
    from config import get_settings

    if get_settings().app_mode == "live":
        return []
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
    parlays = build_parlays(all_props, constraints)
    ranked = rank_parlays(parlays, sort_by=params["sort_by"], top_n=500)
    apply_stake_to_all(ranked, params["stake"])
    return ranked


def run_scan(params: dict) -> None:
    started = monotonic()
    st.session_state.scan_error = None
    try:
        with st.status("Scanning the slate", expanded=True) as scan_status:
            st.write("Connecting to the schedule and market feed…")
            scanner = st.session_state.scanner or SlateScanner()
            st.write("Resolving eligible participants and availability…")
            all_props = scanner.scan(
                params["date"],
                prop_types=params["prop_types"],
                is_playoff=params["is_playoff"],
            )
            st.write("Ranking recommendations and preserving the evidence…")
            parlays = _build_parlays(all_props, params)
            st.session_state.games = scanner.last_games
            st.session_state.all_props = all_props
            st.session_state.parlays = parlays
            st.session_state.updates = scanner.last_updates
            st.session_state.exclusions = scanner.last_exclusions
            st.session_state.last_scan_at = datetime.now().astimezone().strftime("%b %d · %I:%M %p")
            st.session_state.last_scan_seconds = monotonic() - started
            st.session_state.scanner = scanner
            scan_status.update(label="Decision board refreshed", state="complete", expanded=False)
    except Exception as exc:
        logger.exception("Slate scan failed")
        st.session_state.games = []
        st.session_state.all_props = []
        st.session_state.parlays = []
        st.session_state.updates = []
        st.session_state.exclusions = []
        st.session_state.scan_error = f"Scan failed: {exc}"
        st.error(st.session_state.scan_error)
        return

    if all_props:
        qualifying = _qualifying_props_for_display(all_props, params)
        st.success(
            f"Evaluated {len(all_props)} offers and surfaced {len(qualifying)} recommendations."
        )
        if qualifying:
            best = max(qualifying, key=lambda prop: prop.edge)
            st.success(
                f"Highest current edge: {best.player_name} {best.prop_type.value.title()} "
                f"{best.side.value.upper()} {best.line:g} ({best.edge * 100:.1f}%)."
            )
    elif params["date"] > today_eastern():
        st.warning("No eligible offers are posted for this future slate yet.")
    else:
        status, _ = _provider_context()
        reason = status.get("reason")
        if reason:
            st.error(f"No offers were returned. Provider state: {reason.replace('_', ' ')}.")
        else:
            st.warning("No eligible pregame offers were available for this slate.")


def render_shell(params: dict) -> None:
    from config import get_settings

    settings = get_settings()
    status, provider_name = _provider_context()
    mode_copy = {
        "demo": "Explore deterministic example data without making external requests.",
        "live": "Evaluate current pregame markets with explicit freshness and availability safeguards.",
        "replay": "Inspect previously recorded evidence without contacting a live provider.",
    }.get(settings.app_mode, "Review the current runtime configuration.")
    render_hero(
        eyebrow=f"{settings.app_mode} research workspace",
        title="Find signal. See the evidence.",
        description=(
            "A decision-first NBA prop workspace that ranks current opportunities, "
            "shows why they qualify, and preserves every input for later verification."
        ),
        chips=[
            (f"{settings.app_mode.title()} mode", "accent"),
            (f"{provider_name}", "success" if status.get("state") == "ready" else "warning"),
            (status.get("state", "unknown").title(), _status_tone(status.get("state", ""))),
            (params["date"].strftime("%b %d, %Y"), "neutral"),
        ],
    )
    st.caption(mode_copy)

    workspace = st.segmented_control(
        "Workspace",
        options=["Home", "Research", "Performance", "System"],
        default="Home",
        key="workspace_navigation",
        label_visibility="collapsed",
        width="stretch",
    )
    if workspace == "Research":
        render_research(params)
    elif workspace == "Performance":
        render_evaluation()
    elif workspace == "System":
        render_system(params)
    else:
        render_home(params)


def main() -> None:
    _initialize_state()
    params = render_sidebar()
    if params["run"]:
        run_scan(params)
    render_shell(params)
    st.markdown(
        """
        <footer class="tp-footer">
          True Prob provides informational research, not financial advice or wagering execution.
          Verify market availability and price independently. Past performance does not guarantee future results.
        </footer>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
