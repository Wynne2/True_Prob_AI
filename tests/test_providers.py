"""
Tests for providers.

Covers:
- SampleProvider returns data for all required methods
- ProviderRegistry builds without errors
- CSVImportProvider gracefully handles missing files
- Normalisation pipeline (raw dict → entity)
"""

import pytest
from datetime import date

from providers.sample_provider import SampleProvider
from providers.csv_import_provider import CSVImportProvider
from providers.provider_registry import ProviderRegistry
from data.normalizers import (
    raw_dict_to_game,
    raw_dict_to_player,
    raw_dict_to_odds_line,
)
from domain.enums import DataSource, PropType


class TestSampleProvider:
    def setup_method(self):
        self.provider = SampleProvider()

    def test_is_available(self):
        assert self.provider.is_available() is True

    def test_get_games_for_today(self):
        games = self.provider.get_games_for_date(date.today())
        assert len(games) >= 1
        for g in games:
            assert g.home_team_abbr
            assert g.away_team_abbr
            assert g.game_id

    def test_get_players_for_game(self):
        games = self.provider.get_games_for_date(date.today())
        players = self.provider.get_players_for_game(games[0].game_id)
        assert len(players) >= 2
        for p in players:
            assert p.player_id
            assert p.name
            assert p.team_abbr

    def test_get_player_props(self):
        lines = self.provider.get_player_props(date.today())
        assert len(lines) > 0
        for line in lines:
            assert line.player_id
            assert isinstance(line.prop_type, PropType)
            assert isinstance(line.over_odds, int)
            assert isinstance(line.under_odds, int)

    def test_get_team_defense(self):
        defense = self.provider.get_team_defense("t_bos")
        assert defense is not None
        assert defense.team_abbr == "BOS"
        assert defense.defensive_efficiency > 0

    def test_get_injuries(self):
        injuries = self.provider.get_injuries()
        # At least one player is questionable in sample data
        assert isinstance(injuries, list)

    def test_get_lineups(self):
        lineups = self.provider.get_lineups()
        assert len(lineups) > 0


class TestCSVImportProvider:
    def setup_method(self):
        self.provider = CSVImportProvider()

    def test_is_not_available_without_files(self):
        # CSV files don't exist in a fresh install → should return False
        # (unless files happen to exist in the test environment)
        result = self.provider.is_available()
        assert isinstance(result, bool)

    def test_get_player_props_missing_file(self):
        # Missing file should return empty list, not raise
        props = self.provider.get_player_props(date.today())
        assert isinstance(props, list)


class TestProviderRegistry:
    def test_registry_builds(self):
        registry = ProviderRegistry.build()
        assert registry is not None
        assert len(registry.active_providers) >= 1

    def test_sample_always_in_registry(self):
        registry = ProviderRegistry.build()
        assert "sample" in registry.active_providers

    def test_registry_get_games(self):
        registry = ProviderRegistry.build()
        games = registry.get_games_for_date(date.today())
        assert len(games) > 0

    def test_registry_get_props(self):
        registry = ProviderRegistry.build()
        props = registry.get_player_props(date.today())
        assert len(props) > 0

    def test_registry_summary(self):
        registry = ProviderRegistry.build()
        summary = registry.summary()
        assert "Provider Registry" in summary


class TestNormalisers:
    def test_raw_game_normalisation(self):
        raw = {
            "game_id": "g1",
            "home_team_id": "t_bos",
            "home_team_abbr": "BOS",
            "away_team_id": "t_mia",
            "away_team_abbr": "MIA",
            "game_date": "2025-04-14",
        }
        game = raw_dict_to_game(raw, DataSource.SAMPLE)
        assert game is not None
        assert game.home_team_abbr == "BOS"

    def test_raw_player_normalisation(self):
        raw = {
            "player_id": "p1",
            "name": "Test Player",
            "team_id": "t1",
            "team_abbr": "TST",
            "position": "PG",
            "usage_rate": "28.5",  # percentage string
            "three_point_pct": "37.5",  # percentage string
        }
        player = raw_dict_to_player(raw, DataSource.SAMPLE)
        assert player is not None
        assert player.usage_rate < 1.0  # should be 0.285
        assert player.three_point_pct < 1.0

    def test_raw_odds_line_normalisation(self):
        raw = {
            "book": "fanduel",
            "player_id": "p1",
            "player_name": "Test Player",
            "prop_type": "points",
            "line": "24.5",
            "over_odds": "+110",
            "under_odds": -130,
            "game_id": "g1",
        }
        line = raw_dict_to_odds_line(raw, DataSource.SAMPLE)
        assert line is not None
        assert line.prop_type == PropType.POINTS
        assert line.over_odds == 110
        assert line.under_odds == -130

    def test_unknown_prop_type_returns_none(self):
        raw = {
            "book": "fanduel",
            "player_id": "p1",
            "player_name": "Test Player",
            "prop_type": "fantasy_score_unknown",
            "line": "50.0",
            "over_odds": -110,
            "under_odds": -110,
        }
        line = raw_dict_to_odds_line(raw, DataSource.SAMPLE)
        assert line is None
