"""
One scored board for every entry point: the dashboard, the CLI and the AI Advisor.

    scan → frozen candidate probabilities and edges → model-agreement check →
    Matchup Finder tags

Before this module only the dashboard ran the steps after the scan, so the CLI
and the advisor saw production-blend edges while the board showed candidate
edges. Every step after the scan is optional: if one fails, the props keep the
probabilities they had and ``candidate_status`` says why.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from engine.model_agreement import flag_model_disagreement

logger = logging.getLogger(__name__)


@dataclass
class Board:
    game_date: date
    props: list
    games: list = field(default_factory=list)
    game_lines: list = field(default_factory=list)
    odds_source_counts: dict = field(default_factory=dict)
    odds_source_errors: dict = field(default_factory=dict)
    raw_games: int = 0
    pregame_games: int = 0
    candidate_status: Optional[dict] = None
    agreement: dict = field(default_factory=dict)
    matchup_rows: list = field(default_factory=list)


def enrich_props(props: list, game_date: date, games: Optional[list] = None, *, matchups: bool = True) -> dict:
    """
    Run the steps after the scan on ``props`` in place. Call once per scan:
    the candidate step replaces true probabilities, so a second call would
    record the candidate as the production probability.
    """
    out: dict = {"candidate_status": None, "agreement": {}, "matchup_rows": []}
    if not props:
        return out
    from engine.candidate.live import apply_candidate_edges, attach_candidate

    try:
        status = attach_candidate(props, game_date, games=games)
        status["edges"] = apply_candidate_edges(props)
    except Exception as exc:  # the board works without the candidate
        logger.warning("Candidate model unavailable: %s", exc)
        status = {"status": "error", "reason": str(exc)}
    out["candidate_status"] = status
    out["agreement"] = flag_model_disagreement(props)
    if matchups:
        try:
            from engine.candidate.matchups import find_for_slate

            out["matchup_rows"] = find_for_slate(props, game_date, min_steps=2)
        except Exception as exc:
            logger.warning("Matchup Finder unavailable: %s", exc)
    return out


def scan_board(
    game_date: date,
    *,
    prop_types: Optional[list] = None,
    is_playoff: bool = False,
    matchups: bool = True,
) -> Board:
    """Scan the slate and score it the same way the dashboard does."""
    from engine.slate_scanner import SlateScanner

    scanner = SlateScanner()
    props = scanner.scan(game_date, prop_types=prop_types, is_playoff=is_playoff)
    games = list(getattr(scanner, "last_games", []) or [])
    extra = enrich_props(props, game_date, games, matchups=matchups)
    return Board(
        game_date=game_date,
        props=props,
        games=games,
        game_lines=list(scanner.last_game_lines),
        odds_source_counts=dict(scanner.last_odds_source_counts or {}),
        odds_source_errors=dict(scanner.last_odds_source_errors or {}),
        raw_games=scanner.last_raw_slate_game_count,
        pregame_games=scanner.last_pregame_slate_game_count,
        **extra,
    )
