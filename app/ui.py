"""
Visual layer for the Streamlit dashboard: theme CSS and small HTML components.

Every string that came from a data feed is escaped before it reaches HTML.
"""

from __future__ import annotations

from datetime import date
from html import escape
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import streamlit as st

from utils.formatting import book_display_name, format_american, format_currency

_EASTERN = ZoneInfo("America/New_York")

_SOURCE_NAMES = {
    "balldontlie": "BALLDONTLIE",
    "odds_api": "The Odds API",
    "kalshi": "Kalshi",
    "polymarket": "Polymarket",
}

PROP_LABELS = {
    "points": "Points",
    "rebounds": "Rebounds",
    "assists": "Assists",
    "threes": "3-pointers made",
    "pra": "Pts + Reb + Ast",
    "blocks": "Blocks",
    "steals": "Steals",
    "turnovers": "Turnovers",
    "points_rebounds": "Pts + Reb",
    "points_assists": "Pts + Ast",
    "rebounds_assists": "Reb + Ast",
}

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Barlow:ital,wght@0,400;0,500;0,600;1,400&display=swap');

:root {
  --tp-bg: #0E1A2B;
  --tp-surface: #14233A;
  --tp-raised: #1B2E4A;
  --tp-line: #2A3F5F;
  --tp-text: #EAF0F8;
  --tp-muted: #9AABC4;
  --tp-court: #FF7A1A;
  --tp-court-ink: #1F1003;
  --tp-edge: #34D399;
  --tp-risk: #F87171;
  --tp-caution: #FBBF24;
  --tp-market: #8FB3E8;
  --tp-display: 'Barlow Condensed', 'Arial Narrow', system-ui, sans-serif;
  --tp-body: 'Barlow', system-ui, -apple-system, 'Segoe UI', sans-serif;
  --tp-radius-lg: 14px;
  --tp-radius-sm: 8px;
}

/* Base: a navy "arena at night" ground with one soft court glow at the top. */
.stApp {
  background:
    radial-gradient(1100px 380px at 12% -120px, rgba(255, 122, 26, 0.10), transparent 70%),
    var(--tp-bg);
  color: var(--tp-text);
}
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea,
.stApp button, .stApp [data-testid="stMarkdownContainer"],
.stApp [data-testid="stCaptionContainer"] {
  font-family: var(--tp-body);
}
.stApp h1, .stApp h2, .stApp h3, .stApp h4 {
  font-family: var(--tp-display);
  letter-spacing: 0.01em;
  color: var(--tp-text);
}
.stApp h3 { font-size: 1.55rem; font-weight: 600; }
.stApp [data-testid="stCaptionContainer"], .stApp small { color: var(--tp-muted); }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 2.2rem; max-width: 1360px; }

/* Sidebar */
[data-testid="stSidebar"] {
  background: #0B1524;
  border-right: 1px solid var(--tp-line);
}
[data-testid="stSidebar"] h3 {
  font-size: 1.05rem;
  color: var(--tp-muted);
  font-weight: 600;
  margin-top: 0.4rem;
}
[data-testid="stSidebar"] hr { margin: 0.8rem 0; border-color: var(--tp-line); }

/* Buttons */
.stApp button[kind="primary"], .stApp [data-testid="stBaseButton-primary"] {
  background: var(--tp-court);
  border: 1px solid var(--tp-court);
  color: var(--tp-court-ink);
  font-family: var(--tp-display);
  font-size: 1.08rem;
  font-weight: 700;
  letter-spacing: 0.02em;
  min-height: 44px;
  transition: background-color 160ms ease, box-shadow 160ms ease;
}
.stApp button[kind="primary"]:hover, .stApp [data-testid="stBaseButton-primary"]:hover {
  background: #FF8F3F;
  border-color: #FF8F3F;
  color: var(--tp-court-ink);
  box-shadow: 0 0 0 4px rgba(255, 122, 26, 0.18);
}
.stApp button[kind="secondary"], .stApp [data-testid="stBaseButton-secondary"] {
  min-height: 40px;
  border-color: var(--tp-line);
}
.stApp button:focus-visible {
  outline: 2px solid var(--tp-market);
  outline-offset: 2px;
}

/* Tabs: a scoreboard rail with the active tab underlined in court orange. */
.stTabs [data-baseweb="tab-list"] {
  gap: 0.25rem;
  border-bottom: 1px solid var(--tp-line);
  overflow-x: auto;
  scrollbar-width: thin;
}
.stTabs [data-baseweb="tab"] {
  font-family: var(--tp-display);
  font-size: 1.05rem;
  font-weight: 600;
  padding: 0.55rem 0.9rem;
  color: var(--tp-muted);
  white-space: nowrap;
}
.stTabs [data-baseweb="tab"] p { font-family: var(--tp-display); font-size: 1.05rem; }
.stTabs [aria-selected="true"] { color: var(--tp-text); }
.stTabs [data-baseweb="tab-highlight"] { background-color: var(--tp-court); height: 3px; }

/* Native metrics, expanders, tables */
[data-testid="stMetric"] {
  background: var(--tp-surface);
  border: 1px solid var(--tp-line);
  border-radius: var(--tp-radius-sm);
  padding: 0.7rem 0.9rem;
}
[data-testid="stMetricValue"] {
  font-family: var(--tp-display);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
[data-testid="stMetricLabel"] p { color: var(--tp-muted); }
[data-testid="stExpander"] details {
  background: var(--tp-surface);
  border: 1px solid var(--tp-line);
  border-radius: var(--tp-radius-sm);
}
[data-testid="stExpander"] summary:hover { color: var(--tp-court); }
[data-testid="stDataFrame"] {
  border: 1px solid var(--tp-line);
  border-radius: var(--tp-radius-sm);
  overflow: hidden;
}
[data-testid="stAlert"] { border-radius: var(--tp-radius-sm); }

/* ---- Custom components ---------------------------------------------- */
.tp-num { font-variant-numeric: tabular-nums; }

.tp-masthead {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 1rem 2rem;
  padding-bottom: 1.1rem;
  margin-bottom: 1.2rem;
  border-bottom: 1px solid var(--tp-line);
}
.tp-brand { display: flex; align-items: center; gap: 0.85rem; }
.tp-ball {
  width: 46px; height: 46px; flex: none;
}
.stApp h1.tp-wordmark {
  font-family: var(--tp-display);
  font-size: 2.4rem;
  font-weight: 700;
  line-height: 1;
  margin: 0;
  padding: 0;
  color: var(--tp-text);
}
.stApp h1.tp-wordmark .tp-ai { color: var(--tp-court); }
.tp-tagline { margin: 0.25rem 0 0; color: var(--tp-muted); font-size: 0.98rem; }
.tp-meta { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
.tp-pill {
  display: inline-flex; align-items: center; gap: 0.4rem;
  padding: 0.3rem 0.7rem;
  border-radius: 999px;
  border: 1px solid var(--tp-line);
  background: var(--tp-surface);
  color: var(--tp-text);
  font-size: 0.88rem;
  white-space: nowrap;
}
.tp-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--tp-muted); }
.tp-dot.on { background: var(--tp-edge); box-shadow: 0 0 0 3px rgba(52, 211, 153, 0.18); }
.tp-pill.off { color: var(--tp-muted); }

.tp-section-title {
  display: flex; align-items: baseline; justify-content: space-between; gap: 1rem;
  flex-wrap: wrap;
  margin: 1.4rem 0 0.7rem;
}
.tp-section-title h3 { margin: 0; padding: 0; }
.tp-section-title p { margin: 0; color: var(--tp-muted); }

.tp-tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0.75rem;
  margin: 0.4rem 0 1.1rem;
}
.tp-tile {
  background: var(--tp-surface);
  border: 1px solid var(--tp-line);
  border-radius: var(--tp-radius-sm);
  padding: 0.75rem 0.95rem;
}
.tp-tile .label { color: var(--tp-muted); font-size: 0.86rem; margin: 0; }
.tp-tile .value {
  font-family: var(--tp-display);
  font-size: 1.85rem;
  font-weight: 600;
  line-height: 1.15;
  margin: 0.1rem 0 0;
  font-variant-numeric: tabular-nums;
}
.tp-tile.edge .value { color: var(--tp-edge); }
.tp-tile.court .value { color: var(--tp-court); }

.tp-slate {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 0.75rem;
  margin-bottom: 1.2rem;
}
.tp-game {
  background: var(--tp-surface);
  border: 1px solid var(--tp-line);
  border-left: 3px solid var(--tp-court);
  border-radius: var(--tp-radius-sm);
  padding: 0.75rem 0.95rem;
}
.tp-game .teams {
  font-family: var(--tp-display);
  font-size: 1.45rem;
  font-weight: 700;
  margin: 0;
  line-height: 1.1;
}
.tp-game .teams .at { color: var(--tp-muted); font-weight: 500; padding: 0 0.3rem; }
.tp-game .when { margin: 0.2rem 0 0; color: var(--tp-text); font-size: 0.92rem; }
.tp-game .where { margin: 0.1rem 0 0; color: var(--tp-muted); font-size: 0.86rem; }
.tp-game .lines {
  display: flex; flex-wrap: wrap; gap: 0.4rem 0.9rem;
  margin: 0.5rem 0 0; padding-top: 0.5rem;
  border-top: 1px solid var(--tp-line);
  font-size: 0.86rem; color: var(--tp-muted);
}
.tp-game .lines b { color: var(--tp-text); font-weight: 600; }

/* Pick card: the probability meter is the signature element. */
.tp-pick {
  display: grid;
  grid-template-columns: 2.6rem 1fr;
  gap: 0.9rem;
  background: var(--tp-surface);
  border: 1px solid var(--tp-line);
  border-radius: var(--tp-radius-lg);
  padding: 1rem 1.1rem;
  margin: 0.8rem 0 0.35rem;
}
.tp-rank {
  font-family: var(--tp-display);
  font-size: 1.9rem;
  font-weight: 700;
  color: var(--tp-court);
  line-height: 1;
  text-align: center;
}
.tp-pick-head {
  display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
  gap: 0.25rem 1rem;
}
.tp-player {
  font-family: var(--tp-display);
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0;
  line-height: 1.1;
}
.tp-price {
  font-family: var(--tp-display);
  font-size: 1.5rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.tp-price small { font-family: var(--tp-body); font-size: 0.9rem; color: var(--tp-muted); font-weight: 400; margin-left: 0.35rem; }
.tp-market-line { margin: 0.2rem 0 0.75rem; color: var(--tp-muted); }
.tp-market-line strong { color: var(--tp-text); font-weight: 600; }

.tp-meter { margin: 0.2rem 0 0.7rem; }
.tp-meter-track {
  position: relative;
  height: 12px;
  border-radius: 999px;
  background: var(--tp-raised);
  overflow: hidden;
}
.tp-meter-fill {
  position: absolute; inset: 0 auto 0 0;
  background: linear-gradient(90deg, #1E5E52, #2BA57E);
  border-radius: 999px 0 0 999px;
}
.tp-meter-gap { position: absolute; top: 0; bottom: 0; background: var(--tp-edge); }
.tp-meter-gap.neg { background: var(--tp-risk); }
.tp-meter-tick {
  position: absolute; top: -3px; bottom: -3px; width: 3px; margin-left: -1px;
  background: var(--tp-market); border-radius: 2px;
}
.tp-meter-legend {
  display: flex; flex-wrap: wrap; gap: 0.3rem 1.1rem;
  margin-top: 0.4rem; font-size: 0.88rem; color: var(--tp-muted);
}
.tp-key { display: inline-flex; align-items: center; gap: 0.35rem; }
.tp-key i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; }
.tp-key b { color: var(--tp-text); font-weight: 600; font-variant-numeric: tabular-nums; }

.tp-pick-foot {
  display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 1.2rem;
  font-size: 0.94rem; color: var(--tp-muted);
}
.tp-pick-foot b { color: var(--tp-text); font-weight: 600; font-variant-numeric: tabular-nums; }
.tp-chip {
  display: inline-flex; align-items: center;
  padding: 0.18rem 0.6rem;
  border-radius: 999px;
  font-weight: 600;
  font-size: 0.9rem;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.tp-chip.pos { background: rgba(52, 211, 153, 0.14); color: var(--tp-edge); }
.tp-chip.neg { background: rgba(248, 113, 113, 0.14); color: var(--tp-risk); }
.tp-chip.neutral { background: var(--tp-raised); color: var(--tp-text); }
.tp-conf { display: inline-flex; gap: 3px; align-items: center; }
.tp-conf i { width: 7px; height: 14px; border-radius: 2px; background: var(--tp-raised); }
.tp-conf i.on { background: var(--tp-court); }

/* Parlay ticket */
.tp-ticket {
  background: var(--tp-surface);
  border: 1px solid var(--tp-line);
  border-radius: var(--tp-radius-lg);
  margin: 0.9rem 0 0.35rem;
  overflow: hidden;
}
.tp-ticket-head {
  display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center;
  gap: 0.6rem 1rem;
  padding: 0.85rem 1.1rem;
  background: var(--tp-raised);
  border-bottom: 1px dashed var(--tp-line);
}
.tp-ticket-title { display: flex; align-items: baseline; gap: 0.8rem; flex-wrap: wrap; }
.tp-ticket-title .tp-rank { font-size: 1.6rem; text-align: left; }
.tp-ticket-title h4 { margin: 0; font-size: 1.35rem; }
.tp-tags { display: flex; gap: 0.35rem; flex-wrap: wrap; }
.tp-legs { list-style: none; margin: 0; padding: 0.35rem 1.1rem; }
.tp-legs li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.2rem 1rem;
  padding: 0.6rem 0;
  border-bottom: 1px solid rgba(42, 63, 95, 0.6);
}
.tp-legs li:last-child { border-bottom: none; }
.tp-leg-name { font-weight: 600; margin: 0; }
.tp-leg-sub { margin: 0; color: var(--tp-muted); font-size: 0.9rem; }
.tp-leg-odds { text-align: right; font-variant-numeric: tabular-nums; }
.tp-leg-odds b { font-family: var(--tp-display); font-size: 1.2rem; font-weight: 600; }
.tp-ticket-foot {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 0.6rem 1rem;
  padding: 0.8rem 1.1rem;
  border-top: 1px dashed var(--tp-line);
}
.tp-ticket-foot .label { margin: 0; color: var(--tp-muted); font-size: 0.84rem; }
.tp-ticket-foot .value {
  margin: 0; font-family: var(--tp-display); font-size: 1.35rem; font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.tp-ticket-foot .value.pos { color: var(--tp-edge); }

/* Empty state */
.tp-empty {
  border: 1px dashed var(--tp-line);
  border-radius: var(--tp-radius-lg);
  padding: 1.6rem 1.4rem;
  margin: 0.6rem 0 1rem;
  background: rgba(20, 35, 58, 0.55);
}
.tp-empty h3 { margin: 0 0 0.35rem; }
.tp-empty > p { margin: 0 0 1.1rem; color: var(--tp-muted); max-width: 62ch; }
.tp-steps {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 0.8rem;
  list-style: none; margin: 0; padding: 0;
  counter-reset: step;
}
.tp-steps li {
  counter-increment: step;
  background: var(--tp-surface);
  border: 1px solid var(--tp-line);
  border-radius: var(--tp-radius-sm);
  padding: 0.8rem 0.95rem;
}
.tp-steps li::before {
  content: counter(step);
  display: inline-grid; place-items: center;
  width: 1.7rem; height: 1.7rem; margin-bottom: 0.45rem;
  border-radius: 50%;
  background: var(--tp-court); color: var(--tp-court-ink);
  font-family: var(--tp-display); font-weight: 700;
}
.tp-steps b { display: block; margin-bottom: 0.15rem; }
.tp-steps span { color: var(--tp-muted); font-size: 0.92rem; }

.tp-note { color: var(--tp-muted); font-size: 0.94rem; margin: 0.2rem 0 0.8rem; max-width: 80ch; }

.tp-funnel { display: flex; flex-wrap: wrap; align-items: center; gap: 0.35rem; margin: 0.4rem 0 1rem; }
.tp-funnel .step {
  background: var(--tp-surface); border: 1px solid var(--tp-line);
  border-radius: var(--tp-radius-sm); padding: 0.35rem 0.7rem; font-size: 0.9rem;
}
.tp-funnel .step b { font-family: var(--tp-display); font-size: 1.15rem; margin-right: 0.3rem; font-variant-numeric: tabular-nums; }
.tp-funnel .step.final { border-color: var(--tp-court); }
.tp-funnel .sep { color: var(--tp-muted); }

.tp-footer {
  display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0.5rem 1.5rem;
  margin-top: 2.2rem; padding-top: 1rem;
  border-top: 1px solid var(--tp-line);
  color: var(--tp-muted); font-size: 0.88rem;
}
.tp-footer strong { color: var(--tp-text); }

@media (max-width: 640px) {
  .block-container { padding: 4rem 1rem 1rem; }
  .stApp h1.tp-wordmark { font-size: 1.9rem; }
  .tp-pick { grid-template-columns: 1fr; gap: 0.3rem; }
  .tp-rank { text-align: left; }
}
@media (prefers-reduced-motion: reduce) {
  .stApp * { transition: none !important; animation: none !important; }
}
</style>
"""

# A basketball drawn in the brand colors; decorative, so hidden from screen readers.
_BALL_SVG = (
    '<svg class="tp-ball" viewBox="0 0 48 48" aria-hidden="true">'
    '<circle cx="24" cy="24" r="21" fill="#FF7A1A"/>'
    '<g fill="none" stroke="#1F1003" stroke-width="2.2" stroke-linecap="round">'
    '<circle cx="24" cy="24" r="21"/>'
    '<path d="M3 24h42M24 3v42"/>'
    '<path d="M9.5 9.2c5.6 4.4 8.6 9.3 8.6 14.8s-3 10.4-8.6 14.8"/>'
    '<path d="M38.5 9.2c-5.6 4.4-8.6 9.3-8.6 14.8s3 10.4 8.6 14.8"/>'
    "</g></svg>"
)


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def prop_label(prop_type) -> str:
    value = getattr(prop_type, "value", prop_type)
    return PROP_LABELS.get(value, str(value).replace("_", " ").title())


def tip_time(game) -> str:
    if not game.tip_off_time:
        return "Tip time TBD"
    try:
        tip_et = game.tip_off_time.astimezone(_EASTERN)
    except (ValueError, OSError):
        return str(game.tip_off_time)
    # %-I is not portable (fails on Windows), so strip the leading zero by hand.
    return tip_et.strftime("%I:%M %p").lstrip("0") + " ET"


def payout_profit(american_odds: float, stake: float) -> float:
    if american_odds >= 100:
        return stake * (american_odds / 100)
    return stake * (100 / abs(american_odds))


# ---------------------------------------------------------------------------
# Page chrome
# ---------------------------------------------------------------------------

def masthead(game_date: date, available: Iterable[str], missing: Iterable[str]) -> None:
    pills = [f'<span class="tp-pill tp-num">{escape(game_date.strftime("%a, %b %d, %Y"))}</span>']
    for name in available:
        pills.append(f'<span class="tp-pill"><i class="tp-dot on"></i>{escape(_SOURCE_NAMES.get(name, name))}</span>')
    for name in missing:
        pills.append(
            f'<span class="tp-pill off" title="No API key configured">'
            f'<i class="tp-dot"></i>{escape(_SOURCE_NAMES.get(name, name))} off</span>'
        )
    html(
        '<header class="tp-masthead">'
        f'<div class="tp-brand">{_BALL_SVG}<div>'
        '<h1 class="tp-wordmark">True Prob <b class="tp-ai">AI</b></h1>'
        '<p class="tp-tagline">Every NBA player prop on the slate, priced by the model and compared with the market.</p>'
        "</div></div>"
        f'<div class="tp-meta" aria-label="Slate date and data sources">{"".join(pills)}</div>'
        "</header>"
    )


def section(title: str, note: str = "") -> None:
    note_html = f"<p>{escape(note)}</p>" if note else ""
    html(f'<div class="tp-section-title"><h3>{escape(title)}</h3>{note_html}</div>')


def note(text: str) -> None:
    html(f'<p class="tp-note">{escape(text)}</p>')


def tiles(items: list[tuple[str, str, str]]) -> None:
    """items: (label, value, tone) where tone is '', 'edge' or 'court'."""
    cells = "".join(
        f'<div class="tp-tile {tone}"><p class="label">{escape(label)}</p>'
        f'<p class="value">{escape(value)}</p></div>'
        for label, value, tone in items
    )
    html(f'<div class="tp-tiles">{cells}</div>')


def empty_state() -> None:
    html(
        '<section class="tp-empty">'
        "<h3>Scan tonight&rsquo;s slate</h3>"
        "<p>The model projects minutes, shot volume and pace for every player prop, "
        "turns each projection into a probability, and lines it up against the market price.</p>"
        '<ol class="tp-steps">'
        "<li><b>Pick a date and filters</b><span>Choose the slate, minimum edge and the odds range in the sidebar.</span></li>"
        "<li><b>Press Scan slate</b><span>Props, prices and game lines are pulled and evaluated in one pass.</span></li>"
        "<li><b>Review the board</b><span>Straight bets, parlays, line shopping and the AI Advisor fill in below.</span></li>"
        "</ol></section>"
    )


def footer() -> None:
    html(
        '<footer class="tp-footer">'
        "<span><strong>21+ only.</strong> Gambling problem? Call <strong>1-800-GAMBLER</strong>.</span>"
        "<span>True Prob AI is a research tool. Probabilities are estimates, not guarantees.</span>"
        "</footer>"
    )


# ---------------------------------------------------------------------------
# Slate and picks
# ---------------------------------------------------------------------------

def game_cards(games: list) -> None:
    cards = []
    for game in games:
        venue = ", ".join(p for p in (game.arena, game.city) if p)
        lines = []
        if game.game_total:
            lines.append(f"Total <b>{game.game_total:g}</b>")
        if game.home_spread:
            lines.append(f"{escape(game.home_team_abbr)} <b>{game.home_spread:+g}</b>")
        if game.home_implied_total:
            lines.append(
                f"Implied <b>{game.away_implied_total:g}</b> to <b>{game.home_implied_total:g}</b>"
            )
        cards.append(
            '<article class="tp-game">'
            f'<p class="teams">{escape(game.away_team_abbr)}<span class="at">at</span>{escape(game.home_team_abbr)}</p>'
            f'<p class="when tp-num">{escape(tip_time(game))}</p>'
            + (f'<p class="where">{escape(venue)}</p>' if venue else "")
            + (f'<p class="lines tp-num">{"".join(f"<span>{x}</span>" for x in lines)}</p>' if lines else "")
            + "</article>"
        )
    html(f'<div class="tp-slate">{"".join(cards)}</div>')


def prob_meter(true_p: float, implied_p: float, true_label: str = "Model", implied_label: str = "Market") -> str:
    """Bar filled to the model probability, a tick at the market's, the gap shaded as edge."""
    t = max(0.0, min(1.0, true_p)) * 100
    m = max(0.0, min(1.0, implied_p)) * 100
    lo, hi = min(t, m), max(t, m)
    gap_cls = "" if t >= m else " neg"
    return (
        f'<div class="tp-meter" role="img" aria-label="{true_label} {t:.1f} percent, {implied_label} {m:.1f} percent">'
        '<div class="tp-meter-track">'
        f'<div class="tp-meter-fill" style="width:{lo:.2f}%"></div>'
        f'<div class="tp-meter-gap{gap_cls}" style="left:{lo:.2f}%;width:{hi - lo:.2f}%"></div>'
        f'<div class="tp-meter-tick" style="left:{m:.2f}%"></div>'
        "</div>"
        '<div class="tp-meter-legend">'
        f'<span class="tp-key"><i style="background:var(--tp-edge)"></i>{true_label} <b>{t:.1f}%</b></span>'
        f'<span class="tp-key"><i style="background:var(--tp-market)"></i>{implied_label} <b>{m:.1f}%</b></span>'
        "</div></div>"
    )


def edge_chip(edge: float) -> str:
    cls = "pos" if edge > 0 else ("neg" if edge < 0 else "neutral")
    return f'<span class="tp-chip {cls}">{edge * 100:+.1f}% edge</span>'


_CONF_LEVEL = {"very_low": 1, "low": 2, "medium": 3, "high": 4}


def confidence_bars(tier) -> str:
    value = getattr(tier, "value", str(tier))
    level = _CONF_LEVEL.get(value, 0)
    bars = "".join(f'<i class="{"on" if i < level else ""}"></i>' for i in range(4))
    label = value.replace("_", " ").capitalize()
    return f'<span class="tp-conf" title="Confidence: {escape(label)}">{bars}</span> {escape(label)} confidence'


def pick_card(rank: int, prop, stake: float) -> None:
    book = book_display_name(prop.best_book, getattr(prop, "best_book_key", "") or "")
    profit = payout_profit(prop.sportsbook_odds, stake)
    side = prop.side.value.capitalize()
    html(
        '<article class="tp-pick">'
        f'<div class="tp-rank" aria-label="Rank {rank}">{rank}</div>'
        "<div>"
        '<div class="tp-pick-head">'
        f'<h4 class="tp-player">{escape(prop.player_name)}</h4>'
        f'<span class="tp-price">{escape(format_american(prop.sportsbook_odds))}<small>{escape(book)}</small></span>'
        "</div>"
        f'<p class="tp-market-line"><strong>{escape(prop_label(prop.prop_type))} {side.lower()} {prop.line:g}</strong>'
        f" &nbsp;{escape(prop.team_abbr)} vs {escape(prop.opponent_abbr)}"
        f" &nbsp;Projected {prop.projected_value:.1f}</p>"
        + prob_meter(prop.true_probability, prop.implied_probability)
        + '<div class="tp-pick-foot">'
        + edge_chip(prop.edge)
        + f"<span>{escape(format_currency(stake))} returns <b>{escape(format_currency(stake + profit))}</b></span>"
        + f"<span>{confidence_bars(prop.confidence)}</span>"
        + "</div></div></article>"
    )


def parlay_ticket(parlay, rank: Optional[int] = None) -> None:
    legs = []
    for leg in parlay.legs:
        book = book_display_name(leg.sportsbook, getattr(leg, "best_book_key", "") or "")
        legs.append(
            "<li><div>"
            f'<p class="tp-leg-name">{escape(leg.player_name)}</p>'
            f'<p class="tp-leg-sub">{escape(prop_label(leg.prop_type))} {leg.side.value} {leg.line:g}'
            f" &nbsp;{escape(leg.team_abbr)} vs {escape(leg.opponent_abbr)}</p></div>"
            f'<div class="tp-leg-odds"><b>{escape(format_american(leg.sportsbook_odds))}</b>'
            f'<p class="tp-leg-sub">{escape(book)}, model {leg.true_probability * 100:.1f}%</p></div></li>'
        )
    tags = "".join(
        f'<span class="tp-chip neutral">{escape(str(t).replace("_", " ").capitalize())}</span>' for t in (parlay.risk_profile_tags or [])
    )
    foot = [
        ("Model chance", f"{parlay.combined_true_probability * 100:.1f}%", ""),
        ("Market implied", f"{parlay.combined_implied_probability * 100:.1f}%", ""),
        ("Correlation risk", f"{parlay.correlation_risk_score:.2f}", ""),
    ]
    if parlay.stake > 0:
        foot.append((f"{format_currency(parlay.stake)} returns", format_currency(parlay.total_return), "pos"))
    foot_html = "".join(
        f'<div><p class="label">{escape(a)}</p><p class="value {c}">{escape(b)}</p></div>' for a, b, c in foot
    )
    rank_html = f'<span class="tp-rank">{rank if rank is not None else parlay.edge_rank}</span>'
    html(
        '<article class="tp-ticket">'
        '<div class="tp-ticket-head"><div class="tp-ticket-title">'
        f"{rank_html}<h4>{parlay.num_legs}-leg parlay</h4>"
        f'<span class="tp-price">{escape(format_american(parlay.combined_american_odds))}</span>'
        f"{edge_chip(parlay.combined_edge)}</div>"
        f'<div class="tp-tags">{tags}</div></div>'
        f'<ul class="tp-legs">{"".join(legs)}</ul>'
        f'<div class="tp-ticket-foot">{foot_html}</div>'
        "</article>"
    )


def funnel(steps: list[tuple[str, int]]) -> None:
    parts = []
    for i, (label, n) in enumerate(steps):
        cls = "step final" if i == len(steps) - 1 else "step"
        parts.append(f'<span class="{cls}"><b>{n}</b>{escape(label)}</span>')
    html(f'<div class="tp-funnel" aria-label="Filter funnel">{"<span class=sep>&rsaquo;</span>".join(parts)}</div>')
