"""
Date and season utilities.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from domain.entities import Game


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


def _nba_schedule_tz():
    """NBA listings use US Eastern; DST handled via zoneinfo."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except Exception:
        return timezone.utc


def parse_iso_datetime(s: Optional[str]) -> Optional[datetime]:
    """
    Parse ISO-8601 datetime (Odds API ``commence_time``, SDIO ``DateTime``, etc.).

    Strings **with** an explicit offset or ``Z`` keep that timezone (Odds API uses UTC).

    **Naive** strings (no offset) are interpreted as **America/New_York** — schedule
    APIs often send local tip time without a zone; treating naive as UTC made 5:30 PM
    ET look like ``17:30 UTC`` and incorrectly marked games as already started.
    """
    if not s or not isinstance(s, str):
        return None
    t = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_nba_schedule_tz())
    return dt


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def game_is_pregame(game: "Game", now: Optional[datetime] = None) -> bool:
    """
    True if the game has not started yet — safe for pregame prop evaluation.

    Uses schedule *status* when present (Final / In Progress), otherwise
    compares *now* to ``tip_off_time`` when set.
    """
    if now is None:
        now = _now_utc()
    n = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)

    st = (getattr(game, "status", None) or "").strip().lower()
    if st:
        if st in ("final", "completed", "complete", "postponed", "canceled", "cancelled"):
            return False
        if "final" in st:
            return False
        if "progress" in st or st == "inprogress":
            return False

    tip = game.tip_off_time
    if tip is not None:
        if tip.tzinfo is None:
            tip = tip.replace(tzinfo=_nba_schedule_tz())
        t = tip.astimezone(timezone.utc)
        if n >= t:
            return False
    return True


def filter_pregame_games(games: list["Game"]) -> list["Game"]:
    """Keep only games that have not started (commence / status)."""
    return [g for g in games if game_is_pregame(g)]
