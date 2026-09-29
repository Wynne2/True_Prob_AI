"""
Tests for providers.

Covers deterministic demo mode and normalisation contracts.
"""

import pytest
from datetime import date

from providers.sample_provider import SampleProvider
from providers.provider_registry import ProviderRegistry
from domain.enums import PropType


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
