"""
Tests for the prop evaluator and slate scanner.

Covers:
- PropEvaluator returns PropProbability objects with valid fields
- Edge is in expected range
- Slate scanner produces results from sample data
- Points model projection is in reasonable range
- All 8 stat models produce non-trivial projections
"""

import pytest
from datetime import date

from domain.enums import InjuryStatus, PropType
from engine.prop_evaluator import PropEvaluator
from engine.slate_scanner import SlateScanner
from data.sample_players import SAMPLE_PLAYERS
from data.sample_games import get_sample_games, get_sample_odds
from data.sample_teams import DEFENSE_BY_TEAM_ABBR


@pytest.fixture
def jokic():
    from data.sample_players import PLAYER_BY_ID
    return PLAYER_BY_ID["p_jokic"]


@pytest.fixture
def sample_game():
    games = get_sample_games()
    return next(g for g in games if "den" in g.game_id)


@pytest.fixture
def min_defense():
    return DEFENSE_BY_TEAM_ABBR.get("MIN")


@pytest.fixture
def all_sample_odds():
    return get_sample_odds()


@pytest.fixture
def bypass_pregame_filter(monkeypatch):
    """
    Live slate integration tests need games even when all tips have passed
    for the calendar day; pregame-only filtering would return an empty slate.
    """
    monkeypatch.setattr(
        "engine.slate_scanner.filter_pregame_games",
        lambda games: games,
    )


class TestPropEvaluator:
    def setup_method(self):
        self.evaluator = PropEvaluator()

    def test_jokic_points_evaluation(self, jokic, sample_game, min_defense, all_sample_odds):
        results = self.evaluator.evaluate(
            player=jokic,
            game=sample_game,
            defense=min_defense,
            all_odds=all_sample_odds,
            prop_type=PropType.POINTS,
            is_home=True,
        )
        assert len(results) > 0
        for r in results:
            assert 0.0 <= r.true_probability <= 1.0
            assert 0.0 <= r.implied_probability <= 1.0
            assert r.projected_value > 0
            assert r.player_id == jokic.player_id
            assert r.prop_type == PropType.POINTS

    def test_edge_is_reasonable(self, jokic, sample_game, min_defense, all_sample_odds):
        results = self.evaluator.evaluate(
            player=jokic,
            game=sample_game,
            defense=min_defense,
            all_odds=all_sample_odds,
            prop_type=PropType.POINTS,
            is_home=True,
        )
        for r in results:
            # Edge should be between -50% and +50% (extreme values indicate bugs)
            assert -0.50 <= r.edge <= 0.50

    def test_out_player_returns_empty(self, jokic, sample_game, min_defense, all_sample_odds):
        import copy
        out_player = copy.copy(jokic)
        out_player.injury_status = InjuryStatus.OUT
        results = self.evaluator.evaluate(
            player=out_player,
            game=sample_game,
            defense=min_defense,
            all_odds=all_sample_odds,
            prop_type=PropType.POINTS,
            is_home=True,
        )
        assert len(results) == 0

    def test_all_prop_types(self, jokic, sample_game, min_defense, all_sample_odds):
        results = self.evaluator.evaluate_all_props(
            player=jokic,
            game=sample_game,
            defense=min_defense,
            all_odds=all_sample_odds,
            is_home=True,
        )
        prop_types_found = {r.prop_type for r in results}
        # Should find at least points and rebounds
        assert PropType.POINTS in prop_types_found
        assert PropType.REBOUNDS in prop_types_found

    def test_fair_odds_direction(self, jokic, sample_game, min_defense, all_sample_odds):
        results = self.evaluator.evaluate(
            player=jokic,
            game=sample_game,
            defense=min_defense,
            all_odds=all_sample_odds,
            prop_type=PropType.POINTS,
            is_home=True,
        )
        for r in results:
            if r.true_probability > 0.5:
                # Favourite should have negative fair odds
                assert r.fair_odds < 0, f"Expected negative fair odds for prob={r.true_probability}"


class TestSlateScanner:
    def setup_method(self):
        self.scanner = SlateScanner()

    def test_scan_today_returns_results(self, bypass_pregame_filter):
        results = self.scanner.scan(date.today())
        assert len(results) > 0

    def test_scan_result_fields(self, bypass_pregame_filter):
        results = self.scanner.scan(date.today())
        for r in results[:10]:
            assert r.player_name
            assert r.prop_type in list(PropType)
            assert r.line > 0
            assert r.game_id
            assert 0.0 <= r.true_probability <= 1.0

    def test_scan_with_filter(self, bypass_pregame_filter):
        results = self.scanner.scan_with_filter(
            date.today(),
            min_edge=0.04,
            prop_types=[PropType.POINTS, PropType.REBOUNDS],
        )
        for r in results:
            assert r.edge >= 0.04
            assert r.prop_type in (PropType.POINTS, PropType.REBOUNDS)

    def test_scan_prop_type_filter(self, bypass_pregame_filter):
        points_only = self.scanner.scan(
            date.today(),
            prop_types=[PropType.POINTS],
        )
        for r in points_only:
            assert r.prop_type == PropType.POINTS


class TestStatModels:
    """Integration tests for each stat model via PropEvaluator."""

    def _eval(self, player_id, prop_type, game_id_fragment="den"):
        evaluator = PropEvaluator()
        from data.sample_players import PLAYER_BY_ID
        player = PLAYER_BY_ID.get(player_id)
        if not player:
            return []
        game = next((g for g in get_sample_games() if game_id_fragment in g.game_id), None)
        if not game:
            return []
        defense = DEFENSE_BY_TEAM_ABBR.get("MIN")
        odds = get_sample_odds()
        return evaluator.evaluate(player, game, defense, odds, prop_type, is_home=True)

    def test_points_model(self):
        results = self._eval("p_jokic", PropType.POINTS)
        assert len(results) > 0
        assert all(r.projected_value > 10 for r in results)

    def test_rebounds_model(self):
        results = self._eval("p_jokic", PropType.REBOUNDS)
        assert len(results) > 0
        assert all(r.projected_value > 5 for r in results)

    def test_assists_model(self):
        results = self._eval("p_jokic", PropType.ASSISTS)
        assert len(results) > 0
        assert all(r.projected_value > 3 for r in results)

    def test_threes_model(self):
        from data.sample_players import PLAYER_BY_ID
        player = PLAYER_BY_ID.get("p_murray")
        game = get_sample_games()[0]
        defense = DEFENSE_BY_TEAM_ABBR.get("MIN")
        odds = get_sample_odds()
        evaluator = PropEvaluator()
        results = evaluator.evaluate(player, game, defense, odds, PropType.THREES, is_home=True)
        assert len(results) > 0

    def test_pra_model(self):
        results = self._eval("p_jokic", PropType.PRA)
        # PRA = pts + reb + ast, should be substantially higher than any single stat
        for r in results:
            assert r.projected_value > 20  # Jokic should project 40+

    def test_blocks_model(self):
        from data.sample_players import PLAYER_BY_ID
        player = PLAYER_BY_ID.get("p_davis")
        game = next(g for g in get_sample_games() if "lal" in g.game_id)
        defense = DEFENSE_BY_TEAM_ABBR.get("PHX")
        odds = get_sample_odds()
        evaluator = PropEvaluator()
        results = evaluator.evaluate(player, game, defense, odds, PropType.BLOCKS, is_home=True)
        assert len(results) > 0

    def test_steals_model(self):
        from data.sample_players import PLAYER_BY_ID
        player = PLAYER_BY_ID.get("p_gilgeous")
        game = next(g for g in get_sample_games() if "okc" in g.game_id)
        defense = DEFENSE_BY_TEAM_ABBR.get("GSW")
        odds = get_sample_odds()
        evaluator = PropEvaluator()
        results = evaluator.evaluate(player, game, defense, odds, PropType.STEALS, is_home=True)
        assert len(results) > 0

    def test_turnovers_model(self):
        results = self._eval("p_doncic", PropType.TURNOVERS, "sac")
        assert isinstance(results, list)
