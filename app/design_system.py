"""Visual foundation and small presentation primitives for the Streamlit app."""

from __future__ import annotations

from html import escape

import streamlit as st


_THEME_CSS = """
<style>
:root {
    --tp-bg: #080b12;
    --tp-bg-elevated: #0d111b;
    --tp-surface: rgba(17, 23, 36, 0.88);
    --tp-surface-strong: #151b29;
    --tp-surface-soft: rgba(255, 255, 255, 0.035);
    --tp-border: rgba(148, 163, 184, 0.16);
    --tp-border-strong: rgba(148, 163, 184, 0.28);
    --tp-text: #f7f9fc;
    --tp-text-secondary: #a7b0c0;
    --tp-text-muted: #737f93;
    --tp-accent: #8b5cf6;
    --tp-accent-strong: #7c3aed;
    --tp-cyan: #22d3ee;
    --tp-green: #34d399;
    --tp-amber: #fbbf24;
    --tp-red: #fb7185;
    --tp-radius-sm: 10px;
    --tp-radius-md: 16px;
    --tp-radius-lg: 24px;
    --tp-shadow: 0 18px 50px rgba(0, 0, 0, 0.28);
    --tp-font: Inter, "SF Pro Display", "Segoe UI Variable Display", "Segoe UI", sans-serif;
}

html, body, [class*="css"] {
    font-family: var(--tp-font);
}

.stApp {
    color: var(--tp-text);
    background:
        radial-gradient(circle at 82% 0%, rgba(139, 92, 246, 0.13), transparent 28rem),
        radial-gradient(circle at 18% 16%, rgba(34, 211, 238, 0.07), transparent 24rem),
        var(--tp-bg);
}

[data-testid="stAppViewContainer"] > .main {
    background: transparent;
}

.main .block-container {
    max-width: 1440px;
    padding: 2.1rem 2.4rem 4rem;
}

[data-testid="stHeader"] {
    background: rgba(8, 11, 18, 0.72);
    backdrop-filter: blur(18px);
}

[data-testid="stToolbar"] {
    right: 1rem;
}

#MainMenu, footer {
    visibility: hidden;
}

div[data-testid="stSidebar"] {
    background:
        linear-gradient(180deg, rgba(139, 92, 246, 0.08), transparent 14rem),
        #0b0f18;
    border-right: 1px solid var(--tp-border);
}

div[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
    padding: 1.15rem 0.9rem 2rem;
}

div[data-testid="stSidebar"] hr {
    border-color: var(--tp-border);
    margin: 1rem 0;
}

h1, h2, h3 {
    color: var(--tp-text);
    letter-spacing: -0.035em;
}

h1 {
    font-size: clamp(2.15rem, 4vw, 4.25rem);
    line-height: 0.98;
    font-weight: 720;
}

h2 {
    font-size: clamp(1.45rem, 2.4vw, 2.15rem);
    line-height: 1.12;
    font-weight: 680;
}

h3 {
    font-size: 1.08rem;
    font-weight: 650;
}

p, label, [data-testid="stCaptionContainer"] {
    color: var(--tp-text-secondary);
}

a {
    color: var(--tp-cyan);
}

.tp-brand {
    display: flex;
    align-items: center;
    gap: 0.72rem;
    margin: 0.1rem 0 1.3rem;
}

.tp-brand-mark {
    width: 2.35rem;
    height: 2.35rem;
    display: grid;
    place-items: center;
    border-radius: 12px;
    color: white;
    font-size: 1.05rem;
    font-weight: 800;
    background: linear-gradient(145deg, var(--tp-accent), #4338ca);
    box-shadow: 0 10px 26px rgba(124, 58, 237, 0.34);
}

.tp-brand-name {
    color: var(--tp-text);
    font-size: 1rem;
    font-weight: 720;
    letter-spacing: -0.025em;
}

.tp-brand-meta {
    color: var(--tp-text-muted);
    font-size: 0.72rem;
    margin-top: 0.08rem;
}

.tp-hero {
    position: relative;
    overflow: hidden;
    padding: clamp(1.5rem, 4vw, 3.1rem);
    margin: 0.25rem 0 1.4rem;
    border: 1px solid var(--tp-border);
    border-radius: var(--tp-radius-lg);
    background:
        linear-gradient(120deg, rgba(139, 92, 246, 0.14), transparent 48%),
        linear-gradient(180deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0.01)),
        var(--tp-bg-elevated);
    box-shadow: var(--tp-shadow);
    animation: tp-enter 420ms cubic-bezier(.2,.8,.2,1) both;
}

.tp-hero::after {
    content: "";
    position: absolute;
    width: 22rem;
    height: 22rem;
    right: -9rem;
    top: -12rem;
    border-radius: 50%;
    background: rgba(34, 211, 238, 0.08);
    filter: blur(2px);
}

.tp-eyebrow {
    color: #c4b5fd;
    font-size: 0.73rem;
    line-height: 1;
    font-weight: 760;
    letter-spacing: 0.13em;
    text-transform: uppercase;
    margin-bottom: 0.8rem;
}

.tp-hero h1 {
    max-width: 820px;
    margin: 0;
}

.tp-hero p {
    max-width: 720px;
    margin: 1rem 0 0;
    color: var(--tp-text-secondary);
    font-size: clamp(0.98rem, 1.6vw, 1.15rem);
    line-height: 1.65;
}

.tp-chip-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.55rem;
    margin-top: 1.3rem;
}

.tp-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.42rem;
    min-height: 1.85rem;
    padding: 0.28rem 0.68rem;
    border: 1px solid var(--tp-border);
    border-radius: 999px;
    color: var(--tp-text-secondary);
    background: rgba(255, 255, 255, 0.035);
    font-size: 0.75rem;
    font-weight: 650;
}

.tp-chip::before {
    content: "";
    width: 0.42rem;
    height: 0.42rem;
    border-radius: 50%;
    background: currentColor;
    opacity: 0.86;
}

.tp-chip--success { color: var(--tp-green); }
.tp-chip--accent { color: #c4b5fd; }
.tp-chip--warning { color: var(--tp-amber); }
.tp-chip--danger { color: var(--tp-red); }
.tp-chip--neutral { color: var(--tp-text-secondary); }

.tp-section-heading {
    display: flex;
    align-items: end;
    justify-content: space-between;
    gap: 1rem;
    margin: 2rem 0 0.85rem;
}

.tp-section-heading h2 {
    margin: 0;
}

.tp-section-heading p {
    margin: 0.35rem 0 0;
    color: var(--tp-text-muted);
    font-size: 0.88rem;
}

.tp-kicker {
    color: #a78bfa;
    font-size: 0.69rem;
    font-weight: 760;
    letter-spacing: 0.12em;
    text-transform: uppercase;
}

.tp-empty {
    text-align: center;
    padding: clamp(2.4rem, 7vw, 5.3rem) 1.4rem;
    border: 1px dashed var(--tp-border-strong);
    border-radius: var(--tp-radius-lg);
    background:
        radial-gradient(circle at 50% 0%, rgba(139, 92, 246, 0.1), transparent 17rem),
        var(--tp-surface-soft);
}

.tp-empty-icon {
    width: 3.3rem;
    height: 3.3rem;
    margin: 0 auto 1.05rem;
    display: grid;
    place-items: center;
    border-radius: 16px;
    color: #ddd6fe;
    background: rgba(139, 92, 246, 0.14);
    border: 1px solid rgba(167, 139, 250, 0.28);
    font-size: 1.35rem;
    font-weight: 760;
}

.tp-empty h3 {
    margin: 0 0 0.55rem;
    font-size: 1.25rem;
}

.tp-empty p {
    max-width: 560px;
    margin: 0 auto;
    line-height: 1.65;
}

.tp-opportunity {
    padding: 1.15rem 1.2rem;
    margin: 0 0 0.75rem;
    border: 1px solid var(--tp-border);
    border-radius: var(--tp-radius-md);
    background: linear-gradient(145deg, rgba(255,255,255,0.04), rgba(255,255,255,0.018));
    transition: transform 180ms ease, border-color 180ms ease, background 180ms ease;
}

.tp-opportunity:hover {
    transform: translateY(-2px);
    border-color: rgba(167, 139, 250, 0.42);
    background: linear-gradient(145deg, rgba(139,92,246,0.085), rgba(255,255,255,0.02));
}

.tp-opportunity-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
}

.tp-rank {
    color: var(--tp-text-muted);
    font-size: 0.73rem;
    font-weight: 740;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}

.tp-opportunity-name {
    color: var(--tp-text);
    font-size: 1.02rem;
    font-weight: 700;
    margin-top: 0.2rem;
}

.tp-opportunity-market {
    color: var(--tp-text-secondary);
    font-size: 0.84rem;
    margin-top: 0.24rem;
}

.tp-edge {
    color: var(--tp-green);
    font-size: 1.22rem;
    font-weight: 760;
    letter-spacing: -0.03em;
    white-space: nowrap;
}

.tp-opportunity-meta {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 0.65rem;
    margin-top: 1rem;
}

.tp-stat {
    padding: 0.68rem 0.75rem;
    border-radius: var(--tp-radius-sm);
    background: rgba(255,255,255,0.028);
    border: 1px solid rgba(148,163,184,0.1);
}

.tp-stat-label {
    color: var(--tp-text-muted);
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.07em;
    text-transform: uppercase;
}

.tp-stat-value {
    color: var(--tp-text);
    font-size: 0.9rem;
    font-weight: 680;
    margin-top: 0.22rem;
}

.tp-game {
    min-height: 7.2rem;
    padding: 1rem;
    border: 1px solid var(--tp-border);
    border-radius: var(--tp-radius-md);
    background: var(--tp-surface-soft);
}

.tp-game-time {
    color: #c4b5fd;
    font-size: 0.7rem;
    font-weight: 720;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}

.tp-game-matchup {
    color: var(--tp-text);
    font-size: 1.05rem;
    font-weight: 720;
    margin: 0.55rem 0 0.2rem;
}

.tp-game-venue {
    color: var(--tp-text-muted);
    font-size: 0.78rem;
}

.tp-sidebar-status {
    padding: 0.8rem;
    margin: 0 0 1rem;
    border: 1px solid var(--tp-border);
    border-radius: var(--tp-radius-md);
    background: rgba(255,255,255,0.025);
}

.tp-sidebar-status strong {
    color: var(--tp-text);
    font-size: 0.82rem;
}

.tp-sidebar-status p {
    margin: 0.28rem 0 0;
    color: var(--tp-text-muted);
    font-size: 0.72rem;
    line-height: 1.45;
}

.tp-footer {
    margin-top: 3.2rem;
    padding-top: 1.35rem;
    border-top: 1px solid var(--tp-border);
    color: var(--tp-text-muted);
    font-size: 0.75rem;
    line-height: 1.6;
}

[data-testid="stMetric"] {
    min-height: 7.2rem;
    padding: 1rem 1.05rem;
    border: 1px solid var(--tp-border);
    border-radius: var(--tp-radius-md);
    background: linear-gradient(145deg, rgba(255,255,255,0.043), rgba(255,255,255,0.018));
    box-shadow: none;
    transition: border-color 180ms ease, transform 180ms ease;
}

[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    border-color: rgba(167, 139, 250, 0.35);
}

[data-testid="stMetricLabel"] {
    color: var(--tp-text-muted);
    font-size: 0.75rem;
    font-weight: 680;
    letter-spacing: 0.02em;
}

[data-testid="stMetricValue"] {
    color: var(--tp-text);
    font-weight: 720;
    letter-spacing: -0.04em;
}

[data-testid="stMetricDelta"] {
    font-size: 0.74rem;
}

.stButton > button {
    min-height: 2.75rem;
    border-radius: 12px;
    border: 1px solid var(--tp-border-strong);
    color: var(--tp-text);
    background: var(--tp-surface-strong);
    font-weight: 680;
    transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
}

.stButton > button:hover {
    transform: translateY(-1px);
    border-color: rgba(167, 139, 250, 0.7);
    box-shadow: 0 10px 25px rgba(0,0,0,0.22);
}

.stButton > button[kind="primary"] {
    border: 0;
    color: white;
    background: linear-gradient(135deg, var(--tp-accent), #4f46e5);
    box-shadow: 0 10px 28px rgba(124, 58, 237, 0.34);
}

.stButton > button[kind="primary"]:hover {
    box-shadow: 0 14px 32px rgba(124, 58, 237, 0.46);
}

.stButton > button:focus-visible,
button:focus-visible,
input:focus-visible,
[role="tab"]:focus-visible,
[role="radiogroup"] *:focus-visible {
    outline: 3px solid rgba(34, 211, 238, 0.48) !important;
    outline-offset: 2px;
}

[data-testid="stDateInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stTextInput"] input,
[data-baseweb="select"] > div {
    min-height: 2.65rem;
    color: var(--tp-text);
    border-color: var(--tp-border) !important;
    border-radius: 11px !important;
    background: rgba(255,255,255,0.035) !important;
}

[data-testid="stExpander"] {
    overflow: hidden;
    border: 1px solid var(--tp-border);
    border-radius: var(--tp-radius-md);
    background: rgba(255,255,255,0.018);
}

[data-testid="stExpander"] summary:hover {
    color: var(--tp-text);
}

[data-testid="stTabs"] [data-baseweb="tab-list"] {
    gap: 0.35rem;
    padding: 0.3rem;
    border: 1px solid var(--tp-border);
    border-radius: 13px;
    background: rgba(255,255,255,0.025);
}

[data-testid="stTabs"] [data-baseweb="tab"] {
    min-height: 2.45rem;
    padding: 0 0.9rem;
    border-radius: 9px;
    color: var(--tp-text-secondary);
}

[data-testid="stTabs"] [aria-selected="true"] {
    color: var(--tp-text);
    background: rgba(139, 92, 246, 0.14);
}

[data-testid="stSegmentedControl"] {
    margin: 0 0 1.35rem;
}

[data-testid="stSegmentedControl"] [data-baseweb="button-group"] {
    width: 100%;
    padding: 0.28rem;
    border: 1px solid var(--tp-border);
    border-radius: 14px;
    background: rgba(255,255,255,0.022);
}

[data-testid="stSegmentedControl"] button {
    min-height: 2.5rem;
    color: var(--tp-text-secondary);
    border-radius: 10px !important;
}

[data-testid="stSegmentedControl"] button[aria-pressed="true"] {
    color: white;
    background: rgba(139, 92, 246, 0.2);
    box-shadow: inset 0 0 0 1px rgba(167,139,250,0.25);
}

[data-testid="stDataFrame"] {
    overflow: hidden;
    border: 1px solid var(--tp-border);
    border-radius: var(--tp-radius-md);
}

[data-testid="stAlert"] {
    border-radius: var(--tp-radius-md);
    border: 1px solid var(--tp-border);
}

[data-testid="stStatusWidget"] {
    border-radius: var(--tp-radius-md);
    border-color: var(--tp-border);
}

@keyframes tp-enter {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
}

@media (max-width: 900px) {
    .main .block-container {
        padding: 1.35rem 1rem 3rem;
    }

    .tp-opportunity-meta {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .tp-hero {
        border-radius: 19px;
    }
}

@media (max-width: 640px) {
    .tp-opportunity-top {
        align-items: flex-start;
    }

    .tp-opportunity-meta {
        grid-template-columns: 1fr 1fr;
    }

    .tp-section-heading {
        display: block;
    }
}

@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        scroll-behavior: auto !important;
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
    }
}
</style>
"""


def inject_design_system() -> None:
    """Install the app-wide theme once per render."""
    st.markdown(_THEME_CSS, unsafe_allow_html=True)


def render_brand() -> None:
    """Render the compact sidebar brand."""
    st.markdown(
        """
        <div class="tp-brand">
          <div class="tp-brand-mark" aria-hidden="true">TP</div>
          <div>
            <div class="tp-brand-name">True Prob</div>
            <div class="tp-brand-meta">NBA research intelligence</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def chip(label: str, tone: str = "neutral") -> str:
    """Return a safe status-chip fragment for trusted app layouts."""
    safe_tone = tone if tone in {"success", "accent", "warning", "danger", "neutral"} else "neutral"
    return f'<span class="tp-chip tp-chip--{safe_tone}">{escape(label)}</span>'


def render_hero(
    *,
    eyebrow: str,
    title: str,
    description: str,
    chips: list[tuple[str, str]],
) -> None:
    """Render the primary product statement and live context."""
    rendered_chips = "".join(chip(label, tone) for label, tone in chips)
    st.markdown(
        f"""
        <section class="tp-hero">
          <div class="tp-eyebrow">{escape(eyebrow)}</div>
          <h1>{escape(title)}</h1>
          <p>{escape(description)}</p>
          <div class="tp-chip-row">{rendered_chips}</div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def section_heading(kicker: str, title: str, description: str = "") -> None:
    """Render consistent section hierarchy."""
    description_html = f"<p>{escape(description)}</p>" if description else ""
    st.markdown(
        f"""
        <div class="tp-section-heading">
          <div>
            <div class="tp-kicker">{escape(kicker)}</div>
            <h2>{escape(title)}</h2>
            {description_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def empty_state(icon: str, title: str, description: str) -> None:
    """Render a polished, accessible no-data state."""
    st.markdown(
        f"""
        <section class="tp-empty">
          <div class="tp-empty-icon" aria-hidden="true">{escape(icon)}</div>
          <h3>{escape(title)}</h3>
          <p>{escape(description)}</p>
        </section>
        """,
        unsafe_allow_html=True,
    )
