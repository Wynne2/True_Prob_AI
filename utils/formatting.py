"""
Output formatting helpers for CLI and Streamlit display.
"""

from __future__ import annotations


def format_american(odds: int) -> str:
    """Format American odds with explicit sign, e.g. '+150', '-110'."""
    return f"+{odds}" if odds > 0 else str(odds)


def format_edge(edge: float) -> str:
    """Format edge as percentage string, e.g. '7.3%'."""
    return f"{edge * 100:.1f}%"


def format_prob(prob: float) -> str:
    """Format probability as percentage string, e.g. '56.2%'."""
    return f"{prob * 100:.1f}%"


def format_decimal_odds(decimal: float) -> str:
    """Format decimal odds to 3 d.p., e.g. '1.909'."""
    return f"{decimal:.3f}"


def format_currency(amount: float, prefix: str = "$") -> str:
    """Format a dollar amount, e.g. '$142.50'."""
    return f"{prefix}{amount:,.2f}"


def format_stat(value: float) -> str:
    """Format a projected stat to 1 d.p., e.g. '26.4'."""
    return f"{value:.1f}"


def parlay_summary_line(
    player: str,
    prop: str,
    line: float,
    side: str,
    odds: int,
    edge: float,
) -> str:
    """One-line summary of a parlay leg for CLI output."""
    side_str = side.upper()
    return (
        f"  {player:<28s}  {prop:<12s}  {side_str:4s} {line:5.1f}  "
        f"({format_american(odds)})  Edge: {format_edge(edge)}"
    )


def truncate(text: str, max_len: int = 40, suffix: str = "…") -> str:
    """Truncate *text* to *max_len* characters."""
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix
