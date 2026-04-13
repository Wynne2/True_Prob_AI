"""
Date and season utilities.
"""

from __future__ import annotations

from datetime import date, datetime, timezone


def today_utc() -> date:
    """Return today's date in UTC."""
    return datetime.now(timezone.utc).date()


def today_eastern() -> date:
    """
    Return today's date in Eastern time (NBA's home timezone).
    Uses UTC-5 offset as a simple approximation (ignores DST).
    Games shown for 'today' should use this date when applicable.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        # Fallback: UTC
        return today_utc()


def nba_season_for_date(d: date) -> str:
    """
    Return the NBA season string (e.g. '2024-25') for a given date.

    The NBA season starts in October and ends (including playoffs) in June.
    """
    year = d.year
    month = d.month
    if month >= 10:
        return f"{year}-{str(year + 1)[-2:]}"
    else:
        return f"{year - 1}-{str(year)[-2:]}"


def is_nba_season(d: date) -> bool:
    """Return True if *d* falls within a typical NBA season window."""
    month = d.month
    # Regular season: October – April; Playoffs: April – June
    return month in (10, 11, 12, 1, 2, 3, 4, 5, 6)


def format_date(d: date) -> str:
    """Format date as 'YYYY-MM-DD'."""
    return d.strftime("%Y-%m-%d")


def parse_date(s: str) -> date:
    """Parse 'YYYY-MM-DD' string to date."""
    return datetime.strptime(s, "%Y-%m-%d").date()
