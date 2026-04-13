"""
CSV Import provider.

Allows users to import data from structured CSV files when no live API is
available.  This is a bridge between manual/exported data and the platform.

Expected CSV schemas are documented below.  Any missing file is silently
skipped; the registry will fall back to sample data.

File paths are configured via environment variables:
  CSV_PLAYERS_PATH=data/import/players.csv
  CSV_ODDS_PATH=data/import/odds.csv
  CSV_DEFENSE_PATH=data/import/defense.csv

Players CSV columns (all required):
  player_id, name, team_id, team_abbr, position, minutes_per_game,
  points_per_game, rebounds_per_game, assists_per_game, threes_per_game,
  blocks_per_game, steals_per_game, turnovers_per_game, usage_rate,
  three_point_pct, injury_status

Odds CSV columns (all required):
  book, player_id, player_name, prop_type, line, over_odds, under_odds,
  game_id, team_abbr, opponent_abbr

Defense CSV columns (all required):
  team_id, team_abbr, defensive_efficiency, pace,
  pts_allowed_pg, pts_allowed_sg, pts_allowed_sf, pts_allowed_pf, pts_allowed_c,
  reb_allowed_pg, reb_allowed_sg, reb_allowed_sf, reb_allowed_pf, reb_allowed_c,
  fpa_pg, fpa_sg, fpa_sf, fpa_pf, fpa_c
"""

from __future__ import annotations

import csv
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from config import get_settings
from data.normalizers import raw_dict_to_odds_line, raw_dict_to_player, raw_dict_to_team_defense
from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import DataSource, PropType
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


def _read_csv(path: str) -> list[dict]:
    """Read a CSV file and return a list of row dicts.  Returns [] if file absent."""
    p = Path(path)
    if not p.exists():
        logger.debug("CSV file not found: %s", path)
        return []
    try:
        with p.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception as exc:
        logger.warning("Failed to read CSV %s: %s", path, exc)
        return []


class CSVImportProvider(BaseProvider):
    """
    CSV file import provider.

    No API key required.  Silently skips missing files.
    """

    source_name = DataSource.CSV_IMPORT

    def __init__(self) -> None:
        cfg = get_settings()
        self._players_path = cfg.csv_players_path
        self._odds_path = cfg.csv_odds_path
        self._defense_path = cfg.csv_defense_path

    def is_available(self) -> bool:
        """Available if any CSV file exists."""
        return any(
            Path(p).exists()
            for p in (self._players_path, self._odds_path, self._defense_path)
        )

    def get_games_for_date(self, game_date: date) -> list[Game]:
        # CSV import does not provide a game schedule
        raise NotImplementedError("CSV import: games not supported; use schedule provider")

    def get_players_for_game(self, game_id: str) -> list[Player]:
        rows = _read_csv(self._players_path)
        players = []
        for row in rows:
            player = raw_dict_to_player(row, DataSource.CSV_IMPORT)
            if player:
                players.append(player)
        return players

    def get_team_defense(self, team_id: str) -> Optional[TeamDefense]:
        rows = _read_csv(self._defense_path)
        for row in rows:
            if row.get("team_id") == team_id or row.get("team_abbr") == team_id:
                return raw_dict_to_team_defense(row, DataSource.CSV_IMPORT)
        return None

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        rows = _read_csv(self._odds_path)
        lines = []
        for row in rows:
            line = raw_dict_to_odds_line(row, DataSource.CSV_IMPORT)
            if line:
                lines.append(line)
        logger.info("CSV import: loaded %d odds lines from %s", len(lines), self._odds_path)
        return lines
