"""Sensitivity of the opportunity means. Other inputs stay fixed."""

from __future__ import annotations

from domain.entities import Game, Player, TeamDefense
from domain.enums import PlayerRole, Position, PropType
from models.assists_model import AssistsModel
from models.points_model import PointsModel
from models.rebounds_model import ReboundsModel
from models.threes_model import ThreesModel
from utils.distributions import negbinom_prob_over, normal_prob_over, binomial_prob_over


def _game() -> Game:
    return Game(
        game_id="g",
        home_team_id="1",
        home_team_abbr="DEN",
        away_team_id="2",
        away_team_abbr="MIN",
        blowout_risk=0.0,
    )


def _player(**overrides) -> Player:
    base = dict(
        player_id="p",
        name="Sensitivity",
        team_id="1",
        team_abbr="DEN",
        position=Position.PG,
        role=PlayerRole.STARTER,
        is_starter=True,
        minutes_per_game=32.0,
        points_per_game=20.0,
        rebounds_per_game=5.0,
        assists_per_game=6.0,
        threes_per_game=2.0,
        usage_rate=0.25,
        field_goal_attempts=16.0,
        three_point_attempts=6.0,
        three_point_pct=0.36,
        potential_assists=12.0,
        rebound_chances=10.0,
        usage_vacuum=1.0,
        minutes_vacuum=0.0,
    )
    base.update(overrides)
    return Player(**base)


def _defense(**overrides) -> TeamDefense:
    base = dict(
        team_id="2",
        team_abbr="MIN",
        pace=100.3,
        pace_is_fallback=False,
        pace_source="balldontlie",
        defensive_efficiency=113.1,
        pts_allowed_pg=22.0,
        reb_allowed_pg=3.2,
        ast_allowed_pg=6.4,
        threes_allowed_pg=2.4,
    )
    base.update(overrides)
    return TeamDefense(**base)


def _over(proj, line: float) -> float:
    if proj.prop_type == PropType.POINTS:
        return normal_prob_over(proj.projected_value, proj.dist_std, line)
    if proj.prop_type == PropType.THREES:
        return binomial_prob_over(proj.dist_n, proj.dist_p, line)
    return negbinom_prob_over(proj.projected_value, line=line)


def test_minutes_change_points_opportunities(capsys):
    model = PointsModel()
    game = _game()
    defense = _defense()
    low = model.project(_player(minutes_vacuum=0.0), game, defense, True)
    high = model.project(_player(minutes_vacuum=6.0), game, defense, True)
    print(f"minutes {low.expected_minutes:.1f}->{high.expected_minutes:.1f} "
          f"mean {low.projected_value:.2f}->{high.projected_value:.2f} "
          f"p_over_19.5 {_over(low, 19.5):.3f}->{_over(high, 19.5):.3f}")
    assert high.expected_minutes > low.expected_minutes + 3
    assert high.projected_value > low.projected_value
    assert high.expected_field_goal_attempts_proxy > low.expected_field_goal_attempts_proxy


def test_usage_vacuum_changes_scoring_volume(capsys):
    model = PointsModel()
    game = _game()
    defense = _defense()
    low = model.project(_player(usage_vacuum=1.0), game, defense, True)
    high = model.project(_player(usage_vacuum=1.20), game, defense, True)
    print(f"usage_factor {low.usage_factor:.3f}->{high.usage_factor:.3f} "
          f"fga {low.expected_field_goal_attempts_proxy:.2f}->{high.expected_field_goal_attempts_proxy:.2f} "
          f"mean {low.projected_value:.2f}->{high.projected_value:.2f} "
          f"p_over_19.5 {_over(low, 19.5):.3f}->{_over(high, 19.5):.3f}")
    assert high.usage_factor > low.usage_factor + 0.05
    assert high.expected_field_goal_attempts_proxy > low.expected_field_goal_attempts_proxy
    assert high.projected_value > low.projected_value


def test_more_field_goal_attempts_raise_points(capsys):
    model = PointsModel()
    game = _game()
    defense = _defense()
    low = model.project(_player(field_goal_attempts=14.0), game, defense, True)
    high = model.project(_player(field_goal_attempts=20.0), game, defense, True)
    print(f"fga_input 14->20 mean {low.projected_value:.2f}->{high.projected_value:.2f} "
          f"p_over_19.5 {_over(low, 19.5):.3f}->{_over(high, 19.5):.3f}")
    assert high.projected_value > low.projected_value


def test_potential_assists_change_assists(capsys):
    model = AssistsModel()
    game = _game()
    defense = _defense()
    low = model.project(_player(potential_assists=10.0), game, defense, True)
    high = model.project(_player(potential_assists=18.0), game, defense, True)
    print(f"potential 10->18 mean {low.projected_value:.2f}->{high.projected_value:.2f} "
          f"expected_pot {low.model_context['expected_potential_assists']:.2f}->"
          f"{high.model_context['expected_potential_assists']:.2f} "
          f"p_over_5.5 {_over(low, 5.5):.3f}->{_over(high, 5.5):.3f}")
    assert high.model_context["expected_potential_assists"] > low.model_context["expected_potential_assists"]
    assert high.projected_value > low.projected_value
    assert "potential_assists" not in low.model_context["missing_fields"]


def test_rebound_chances_change_rebounds(capsys):
    model = ReboundsModel()
    game = _game()
    defense = _defense()
    low = model.project(_player(rebound_chances=8.0), game, defense, True)
    high = model.project(_player(rebound_chances=14.0), game, defense, True)
    print(f"chances 8->14 mean {low.projected_value:.2f}->{high.projected_value:.2f} "
          f"expected {low.model_context['expected_rebound_chances']:.2f}->"
          f"{high.model_context['expected_rebound_chances']:.2f} "
          f"p_over_4.5 {_over(low, 4.5):.3f}->{_over(high, 4.5):.3f}")
    assert high.projected_value > low.projected_value


def test_three_attempt_volume_changes_make_probability(capsys):
    model = ThreesModel()
    game = _game()
    defense = _defense()
    low = model.project(_player(three_point_attempts=4.0, threes_per_game=1.44), game, defense, True)
    high = model.project(_player(three_point_attempts=8.0, threes_per_game=1.44), game, defense, True)
    print(f"3PA 4->8 attempts {low.expected_three_point_attempts_proxy:.2f}->"
          f"{high.expected_three_point_attempts_proxy:.2f} "
          f"mean {low.projected_value:.2f}->{high.projected_value:.2f} "
          f"p_over_1.5 {_over(low, 1.5):.3f}->{_over(high, 1.5):.3f}")
    assert high.expected_three_point_attempts_proxy > low.expected_three_point_attempts_proxy
    assert high.projected_value > low.projected_value


def test_opponent_defense_and_pace_change_points(capsys):
    model = PointsModel()
    game = _game()
    player = _player()
    strong = model.project(player, game, _defense(defensive_efficiency=100.0, pts_allowed_pg=16.0), True)
    weak = model.project(player, game, _defense(defensive_efficiency=122.0, pts_allowed_pg=28.0), True)
    slow = model.project(player, game, _defense(pace=94.0), True)
    fast = model.project(player, game, _defense(pace=108.0), True)
    print(f"defense mean {strong.projected_value:.2f}->{weak.projected_value:.2f} "
          f"p_over_19.5 {_over(strong, 19.5):.3f}->{_over(weak, 19.5):.3f}")
    print(f"pace 94->108 mean {slow.projected_value:.2f}->{fast.projected_value:.2f} "
          f"p_over_19.5 {_over(slow, 19.5):.3f}->{_over(fast, 19.5):.3f}")
    assert weak.projected_value > strong.projected_value
    assert fast.projected_value > slow.projected_value
    assert fast.model_context["pace_is_fallback"] is False


def test_missing_pace_is_flagged_and_widens_variance():
    model = PointsModel()
    measured = model.project(_player(), _game(), _defense(pace=100.3, pace_is_fallback=False), True)
    fallback = model.project(_player(), _game(), _defense(pace=100.3, pace_is_fallback=True), True)
    assert fallback.model_context["pace_is_fallback"] is True
    assert fallback.dist_std > measured.dist_std
    assert "pace_fallback_uncertainty" in fallback.projection_audit_flags
    text = " ".join(fallback.model_context["explanation_lines"])
    assert "league-average fallback" in text


def test_team_defense_uses_measured_pace_not_the_constant(monkeypatch):
    from providers.balldontlie_provider import BallDontLieProvider

    def _rows(season, date_str):
        return [{
            "team_id": "2",
            "team_abbr": "BOS",
            "pace": 106.75,
            "def_rating": 108.1,
        }]

    monkeypatch.setattr(
        "data.loaders.nba_api_loader.fetch_team_pace_batch",
        _rows,
    )
    defense = BallDontLieProvider("test-key").get_team_defense("2")
    assert defense is not None
    assert defense.pace == 106.75
    assert defense.pace_is_fallback is False
    assert defense.defensive_efficiency == 108.1
    missing = BallDontLieProvider("test-key").get_team_defense("NO-SUCH")
    assert missing.pace_is_fallback is True
    assert abs(missing.pace - 100.3) < 1e-6


def test_missing_potential_assists_is_named():
    proj = AssistsModel().project(_player(potential_assists=0), _game(), _defense(), True)
    assert "potential_assists" in proj.model_context["missing_fields"]
    assert "missing" in " ".join(proj.model_context["explanation_lines"]).lower()
