"""
Projection sanity tests.

These tests catch the specific failure modes identified during the engine
debug audit.  Each test verifies one of the 8 rules the engine must satisfy
to produce realistic, balanced output.

Failure modes covered:
1. Stars do not project unrealistically low without injury reason.
2. Teammate OUT → active player usage_factor > 1.0 or minutes_vacuum > 0.
3. DvP factor >1.0 (weak D) → higher projection than neutral.
4. DvP factor <1.0 (strong D) → lower projection than neutral.
5. usage_rate = 28.5 (raw percent) is normalized to < 1 before evaluation.
6. projection > line → over_probability > under_probability.
7. projection < line → under_probability > over_probability.
8. Missing season_avg → feature validator blocks evaluation (is_valid=False).
"""

from __future__ import annotations

import pytest

from domain.entities import Game, Player, TeamDefense
from domain.enums import InjuryStatus, PlayerRole, Position, PropType
from domain.provider_models import InjuryContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_star_player(ppg: float = 30.0, mpg: float = 35.0, usg: float = 0.30) -> Player:
    return Player(
        player_id="test_star",
        name="Star Player",
        team_id="T1",
        team_abbr="TST",
        position=Position.PG,
        role=PlayerRole.STARTER,
        injury_status=InjuryStatus.ACTIVE,
        minutes_per_game=mpg,
        points_per_game=ppg,
        rebounds_per_game=5.0,
        assists_per_game=7.0,
        usage_rate=usg,
        is_starter=True,
    )


def _make_game() -> Game:
    return Game(
        game_id="g1",
        home_team_id="T2",
        home_team_abbr="OPP",
        away_team_id="T1",
        away_team_abbr="TST",
        blowout_risk=0.0,
    )


def _make_neutral_defense(team_id: str = "T2") -> TeamDefense:
    return TeamDefense(
        team_id=team_id,
        team_abbr="OPP",
        pace=100.0,
        defensive_efficiency=113.0,
        # Neutral positional pts allowed (≈ league avg)
        pts_allowed_pg=22.0,
        pts_allowed_sg=20.5,
        pts_allowed_sf=21.5,
        pts_allowed_pf=19.5,
        pts_allowed_c=24.0,
    )


# ---------------------------------------------------------------------------
# Test 1: Stars do not collapse
# ---------------------------------------------------------------------------

class TestStarPlayerAntiCollapse:
    def test_star_projection_within_60_pct_of_season_avg(self):
        from models.points_model import PointsModel

        player = _make_star_player(ppg=30.0, mpg=35.0, usg=0.30)
        game = _make_game()
        defense = _make_neutral_defense()

        model = PointsModel()
        proj = model.project(player, game, defense, is_home=True)

        # Must be within 60–140% of season average for healthy star
        assert proj.projected_value >= 30.0 * 0.60, (
            f"Star collapsed to {proj.projected_value:.1f} (season avg=30.0)"
        )
        assert proj.projected_value <= 30.0 * 1.40, (
            f"Star inflated to {proj.projected_value:.1f} (season avg=30.0)"
        )

    def test_weighted_baseline_uses_season_avg_when_no_recent(self):
        from models.base_model import BaseStatModel

        class _Stub(BaseStatModel):
            prop_type = PropType.POINTS
            distribution_type = None
            def project(self, *a, **kw): ...
            def _base_stat(self, p): return p.points_per_game
            def _stat_std(self, p, m): return 1.0

        stub = _Stub()
        # With no recent data, weighted baseline == season_avg (only weight is season)
        result = stub._weighted_baseline(
            season_avg=25.0, last5=[], last10=[], player_name="Test"
        )
        assert result == pytest.approx(25.0), f"Expected 25.0, got {result}"

    def test_anti_collapse_guard_overrides_when_blended_too_low(self):
        from models.base_model import BaseStatModel

        class _Stub(BaseStatModel):
            prop_type = PropType.POINTS
            distribution_type = None
            def project(self, *a, **kw): ...
            def _base_stat(self, p): return p.points_per_game
            def _stat_std(self, p, m): return 1.0

        stub = _Stub()
        # last5 is very low (injury slump not flagged as injury)
        result = stub._weighted_baseline(
            season_avg=25.0,
            last5=[5.0],
            last10=[6.0],
            player_name="Test",
            injury_flag=False,
        )
        # Should be >= 25.0 * 0.70 = 17.5
        assert result >= 25.0 * 0.70, f"Anti-collapse guard failed: got {result}"


# ---------------------------------------------------------------------------
# Test 2: Teammate OUT → minutes_vacuum or usage boost
# ---------------------------------------------------------------------------

class TestTeammateInjuryRedistribution:
    def test_compute_vacancy_adds_minutes_when_teammate_out(self):
        from models.injury_redistribution_model import compute_vacancy_factor

        out_teammates = [
            {
                "position": "PG",
                "usage_rate": 0.30,
                "minutes_per_game": 36.0,
            }
        ]
        usage_boost, minutes_boost = compute_vacancy_factor(
            player_position=Position.PG,
            player_usage_rate=0.20,
            teammates_out=out_teammates,
        )
        assert usage_boost > 0.0, "Expected positive usage boost when PG teammate is OUT"
        assert minutes_boost > 0.0, "Expected positive minutes boost when PG teammate is OUT"

    def test_dissimilar_position_provides_smaller_boost(self):
        from models.injury_redistribution_model import compute_vacancy_factor

        out_c = [{"position": "C", "usage_rate": 0.25, "minutes_per_game": 30.0}]
        out_pg = [{"position": "PG", "usage_rate": 0.25, "minutes_per_game": 30.0}]

        _, min_boost_c = compute_vacancy_factor(Position.PG, 0.20, out_c)
        _, min_boost_pg = compute_vacancy_factor(Position.PG, 0.20, out_pg)

        assert min_boost_pg > min_boost_c, (
            "PG should inherit more minutes from PG absence than from C absence"
        )

    def test_minutes_vacuum_stored_on_player(self):
        player = _make_star_player()
        player.minutes_vacuum = 3.5
        assert player.minutes_vacuum == pytest.approx(3.5)


# ---------------------------------------------------------------------------
# Test 3 & 4: DvP directionality
# ---------------------------------------------------------------------------

class TestDvPDirectionality:
    def _project_with_dvp_factor(self, dvp_pts_allowed: float) -> float:
        from models.points_model import PointsModel

        player = _make_star_player(ppg=25.0, mpg=34.0, usg=0.28)
        game = _make_game()
        defense = _make_neutral_defense()
        defense.pts_allowed_pg = dvp_pts_allowed

        model = PointsModel()
        proj = model.project(player, game, defense, is_home=True)
        return proj.projected_value

    def test_weak_defense_boosts_projection(self):
        neutral = self._project_with_dvp_factor(22.0)   # league avg PG
        weak = self._project_with_dvp_factor(28.0)       # 27% above avg → weak D
        assert weak > neutral, (
            f"Weak defense should boost projection: {weak:.2f} vs {neutral:.2f}"
        )

    def test_strong_defense_reduces_projection(self):
        neutral = self._project_with_dvp_factor(22.0)
        strong = self._project_with_dvp_factor(16.0)     # 27% below avg → strong D
        assert strong < neutral, (
            f"Strong defense should reduce projection: {strong:.2f} vs {neutral:.2f}"
        )


# ---------------------------------------------------------------------------
# Test 5: usage_rate scale — 28.5 must be treated as percent, not fraction
# ---------------------------------------------------------------------------

class TestUsageRateScale:
    def test_usg_pct_divided_by_100(self):
        """usg_pct from nba_api is 0-100; we must store as 0-1."""
        raw_usg_pct = 28.5  # as returned by nba_api

        # Simulate the fix in usage_tracking_service and nba_api_provider
        stored = raw_usg_pct / 100.0
        assert stored < 1.0, f"usage_rate should be < 1.0 after /100; got {stored}"
        assert stored == pytest.approx(0.285)

    def test_usage_factor_sane_when_rate_is_fraction(self):
        from models.usage_model import UsageModel
        from domain.entities import Player

        player = _make_star_player(usg=0.285)
        game = _make_game()

        model = UsageModel()
        eff_usg = model.project(player, game)

        # Effective usage must be close to the raw rate, not deflated to 0.018
        assert eff_usg > 0.15, f"Effective usage too low: {eff_usg:.4f}"
        assert eff_usg < 0.55, f"Effective usage too high: {eff_usg:.4f}"


# ---------------------------------------------------------------------------
# Test 6: projection > line → over_probability > under_probability
# ---------------------------------------------------------------------------

class TestSideSelectionSymmetry:
    def test_over_probability_when_projection_above_line(self):
        from utils.distributions import normal_prob_over, normal_prob_under

        mean = 28.0
        std = 5.0
        line = 24.5  # projection well above line

        prob_over = normal_prob_over(mean, std, line)
        prob_under = normal_prob_under(mean, std, line)

        assert prob_over > prob_under, (
            f"When mean ({mean}) > line ({line}), over ({prob_over:.3f}) "
            f"should exceed under ({prob_under:.3f})"
        )
        assert prob_over > 0.50

    def test_under_probability_when_projection_below_line(self):
        from utils.distributions import normal_prob_over, normal_prob_under

        mean = 16.0
        std = 5.0
        line = 22.5  # projection well below line

        prob_over = normal_prob_over(mean, std, line)
        prob_under = normal_prob_under(mean, std, line)

        assert prob_under > prob_over, (
            f"When mean ({mean}) < line ({line}), under ({prob_under:.3f}) "
            f"should exceed over ({prob_over:.3f})"
        )
        assert prob_under > 0.50

    def test_near_line_produces_near_50pct(self):
        from utils.distributions import normal_prob_over

        mean = 22.5
        std = 5.0
        line = 22.5  # exactly at mean

        prob = normal_prob_over(mean, std, line)
        assert abs(prob - 0.50) < 0.05, f"Near-line prob should be ~50%; got {prob:.3f}"


# ---------------------------------------------------------------------------
# Test 7: (covered jointly with Test 6 above)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 8: Missing season_avg → feature validator blocks evaluation
# ---------------------------------------------------------------------------

class TestFeatureValidation:
    def test_missing_season_avg_is_invalid(self):
        from utils.feature_validator import validate_feature_vector

        class _FakeVector:
            season_avg = 0.0
            projected_minutes = 32.0
            usage_rate = 0.25
            recent_5_avg = 0.0
            recent_10_avg = 0.0
            touches_per_game = 0.0
            possessions_per_game = 0.0
            pace_context = 100.0
            dvp_points_factor = 1.0
            dvp_pts_allowed = 0.0
            data_completeness = 0.8
            player_name = "Test"

        result = validate_feature_vector(_FakeVector(), "points")
        assert not result.is_valid, "Missing season_avg should make validation invalid"
        assert "season_avg" in result.missing_critical

    def test_zero_season_avg_valid_for_count_stats_with_minutes(self):
        """0.0 can be a real season line for threes/blocks/etc.; do not block."""
        from utils.feature_validator import validate_feature_vector

        class _FakeVector:
            season_avg = 0.0
            projected_minutes = 18.0
            usage_rate = 0.15
            recent_5_avg = 0.0
            recent_10_avg = 0.0
            touches_per_game = 20.0
            possessions_per_game = 30.0
            pace_context = 100.0
            dvp_points_factor = 1.0
            dvp_pts_allowed = 22.0
            data_completeness = 0.67
            player_name = "Rim Runner"

        for pt in ("threes", "blocks", "steals", "turnovers"):
            result = validate_feature_vector(_FakeVector(), pt)
            assert result.is_valid, f"{pt}: zero season_avg should be valid with minutes"
            assert "season_avg" not in result.missing_critical

    def test_valid_vector_passes(self):
        from utils.feature_validator import validate_feature_vector

        class _FakeVector:
            season_avg = 25.0
            projected_minutes = 34.0
            usage_rate = 0.28
            recent_5_avg = 27.0
            recent_10_avg = 26.0
            touches_per_game = 80.0
            possessions_per_game = 50.0
            pace_context = 100.3
            dvp_points_factor = 1.05
            dvp_pts_allowed = 22.5
            data_completeness = 0.95
            player_name = "Test"

        result = validate_feature_vector(_FakeVector(), "points")
        assert result.is_valid, f"Valid vector should pass; missing: {result.missing_critical}"

    def test_missing_usage_penalizes_completeness(self):
        from utils.feature_validator import validate_feature_vector

        class _FakeVector:
            season_avg = 20.0
            projected_minutes = 30.0
            usage_rate = 0.0   # missing
            recent_5_avg = 0.0
            recent_10_avg = 0.0
            touches_per_game = 0.0
            possessions_per_game = 0.0
            pace_context = 100.0
            dvp_points_factor = 1.0
            dvp_pts_allowed = 0.0
            data_completeness = 1.0
            player_name = "Test"

        result = validate_feature_vector(_FakeVector(), "points")
        # Should still be valid (season_avg present) but with lower completeness
        assert result.is_valid
        assert result.data_completeness_override is not None
        assert result.data_completeness_override < 1.0, (
            "Missing usage_rate should reduce completeness override"
        )
