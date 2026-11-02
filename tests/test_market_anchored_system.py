"""
Tests for the market-anchored probability system and its supporting pieces:
blend math and fitting, evaluator wiring, LLM injury-news features, copula
parlays, Kelly sizing, CLV / grading, and the advisor payload fields.
"""

from __future__ import annotations

import copy
import json
import math
import random
from datetime import date, datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

import domain.constants as constants
from domain.entities import OddsLine, PropProbability
from domain.enums import BookName, ConfidenceTier, DistributionType, PropSide, PropType
from engine.market_blend import (
    BlendWeights,
    blend_probability,
    consensus_market_probability,
    fallback_params,
    load_blend_params,
    logit,
    no_vig_side_probability,
)


def _line(over: int, under: int, line: float = 20.5, book=BookName.KALSHI, name="Test Player"):
    return OddsLine(
        book=book, player_id="1", player_name=name, prop_type=PropType.POINTS,
        line=line, over_odds=over, under_odds=under,
    )


# ---------------------------------------------------------------------------
# Blend math
# ---------------------------------------------------------------------------

class TestBlendMath:
    def test_zero_weight_returns_market(self):
        assert blend_probability(0.9, 0.6, BlendWeights(0.0, 1.0)) == pytest.approx(0.6)

    def test_zero_trust_returns_market(self):
        assert blend_probability(0.9, 0.6, BlendWeights(0.5, 1.0), trust=0.0) == pytest.approx(0.6)

    def test_clip_bounds_the_move(self):
        w = BlendWeights(1.0, 0.25)
        p = blend_probability(0.99, 0.5, w)
        assert logit(p) - logit(0.5) == pytest.approx(0.25)

    def test_half_weight_moves_halfway_in_log_odds(self):
        p = blend_probability(0.8, 0.6, BlendWeights(0.5, 99.0))
        assert logit(p) == pytest.approx((logit(0.8) + logit(0.6)) / 2)

    def test_over_under_are_complements(self):
        w = BlendWeights(0.5, 0.5)
        over = blend_probability(0.75, 0.62, w)
        under = blend_probability(0.25, 0.38, w)
        assert over + under == pytest.approx(1.0)

    def test_no_vig_side(self):
        p_over = no_vig_side_probability(-150, 130, "over")
        p_under = no_vig_side_probability(-150, 130, "under")
        assert p_over + p_under == pytest.approx(1.0)
        assert p_over > 0.5
        assert no_vig_side_probability(-150, 0, "over") is None

    def test_consensus_is_median_and_matches_line(self):
        lines = [
            _line(-150, 130), _line(-200, 170, book=BookName.POLYMARKET),
            _line(-120, 100), _line(-900, 600, line=21.5),
        ]
        expected = float(np.median([no_vig_side_probability(o, u, "over")
                                    for o, u in ((-150, 130), (-200, 170), (-120, 100))]))
        assert consensus_market_probability(lines, 20.5, "over") == pytest.approx(expected)
        assert consensus_market_probability(lines, 19.5, "over") is None

    def test_params_fallback_and_file(self, tmp_path):
        missing = load_blend_params(tmp_path / "none.json")
        assert missing.source == "fallback"
        assert missing.for_prop(PropType.POINTS).weight == fallback_params().for_prop("points").weight

        path = tmp_path / "blend.json"
        path.write_text(json.dumps({
            "weights": {"points": {"weight": 0.3, "clip": 0.75}},
            "default": {"weight": 0.05, "clip": 0.25},
        }), encoding="utf-8")
        params = load_blend_params(path)
        assert params.for_prop(PropType.POINTS) == BlendWeights(0.3, 0.75)
        assert params.for_prop(PropType.BLOCKS) == BlendWeights(0.05, 0.25)

    def test_bad_file_falls_back(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_blend_params(path).source == "fallback"
        for bad in ({"weight": 1.5, "clip": 0.5}, {"weight": 0.3, "clip": float("nan")}):
            path.write_text(json.dumps({"weights": {"points": bad}}), encoding="utf-8")
            assert load_blend_params(path).source == "fallback"


# ---------------------------------------------------------------------------
# Fitter
# ---------------------------------------------------------------------------

def _synthetic_rows(n_dates: int, per_date: int, informative: bool, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for d in range(n_dates):
        for _ in range(per_date):
            truth = rng.uniform(0.25, 0.85)
            market = min(max(truth + rng.gauss(0, 0.08), 0.05), 0.95)
            model = min(max(truth + rng.gauss(0, 0.02), 0.02), 0.98) if informative else rng.uniform(0.05, 0.95)
            rows.append({
                "date": f"2026-01-{d + 1:02d}", "prop": "points",
                "market": market, "model": model, "y": 1.0 if rng.random() < truth else 0.0,
            })
    return rows


class TestFitter:
    def test_noise_model_gets_zero_weight(self):
        from engine.market_blend_fit import fit_prop

        res = fit_prop(_synthetic_rows(8, 150, informative=False))
        assert res["weight"] == 0.0
        assert res["cv_beats_market"] is False

    def test_informative_model_gets_weight(self):
        from engine.market_blend_fit import fit_prop

        res = fit_prop(_synthetic_rows(8, 150, informative=True))
        assert res["weight"] > 0.0
        assert res["cv_delta_vs_market"] < 0

    def test_fit_blend_writes_file(self, tmp_path):
        from engine.market_blend_fit import fit_blend

        src = tmp_path / "bt.json"
        rows = []
        for i, r in enumerate(_synthetic_rows(6, 60, informative=True)):
            rows.append({
                "variant": "A_opportunity", "side": "over", "game_id": f"g{i}", "player_id": str(i),
                "prop_type": r["prop"], "line": 10.5, "game_date": r["date"],
                "novig_probability": r["market"], "raw_probability": r["model"], "outcome": int(r["y"]),
            })
        src.write_text(json.dumps({"rows": rows}), encoding="utf-8")
        out = tmp_path / "blend.json"
        result = fit_blend([src], out)
        assert out.exists()
        assert "points" in result["weights"]
        assert result["default"]["weight"] <= 0.10
        assert load_blend_params(out).for_prop("points").weight == result["weights"]["points"]["weight"]


# ---------------------------------------------------------------------------
# Evaluator wiring
# ---------------------------------------------------------------------------

@pytest.fixture
def jokic_inputs():
    from data.sample_games import get_sample_games, get_sample_odds
    from data.sample_players import PLAYER_BY_ID
    from data.sample_teams import DEFENSE_BY_TEAM_ABBR

    game = next(g for g in get_sample_games() if "den" in g.game_id)
    return PLAYER_BY_ID["p_jokic"], game, DEFENSE_BY_TEAM_ABBR.get("MIN"), get_sample_odds()


class TestEvaluatorBlend:
    def test_market_blend_path(self, jokic_inputs, monkeypatch):
        from engine.prop_evaluator import PropEvaluator

        monkeypatch.setattr(constants, "USE_MARKET_BLEND", True)
        player, game, defense, odds = jokic_inputs
        results = PropEvaluator().evaluate(player, game, defense, odds, PropType.POINTS, is_home=True)
        assert results
        params = load_blend_params()
        for r in results:
            assert r.probability_method == "market_blend"
            assert r.market_probability is not None
            w = params.for_prop(PropType.POINTS)
            assert 0.0 <= r.model_weight <= w.weight + 1e-9
            max_move = w.weight * w.clip + 1e-6
            moved = abs(logit(r.true_probability) - logit(r.market_probability))
            clamped = r.true_probability in (constants.MIN_PROBABILITY_FLOOR, constants.MAX_PROBABILITY_CEILING)
            assert moved <= max_move or clamped
            assert r.edge == pytest.approx(r.true_probability - r.implied_probability)
            assert "Market-anchored" in r.explanation

    def test_zero_weight_props_equal_market(self, jokic_inputs, monkeypatch):
        from engine import prop_evaluator
        from engine.market_blend import BlendParams

        zero = BlendParams(by_prop={}, default=BlendWeights(0.0, 0.5))
        monkeypatch.setattr(prop_evaluator, "load_blend_params", lambda *a, **k: zero)
        player, game, defense, odds = jokic_inputs
        results = prop_evaluator.PropEvaluator().evaluate(player, game, defense, odds, PropType.POINTS, is_home=True)
        for r in results:
            assert r.true_probability == pytest.approx(
                min(max(r.market_probability, constants.MIN_PROBABILITY_FLOOR), constants.MAX_PROBABILITY_CEILING)
            )

    def test_legacy_path_still_available(self, jokic_inputs, monkeypatch):
        from engine.prop_evaluator import PropEvaluator

        monkeypatch.setattr(constants, "USE_MARKET_BLEND", False)
        player, game, defense, odds = jokic_inputs
        results = PropEvaluator().evaluate(player, game, defense, odds, PropType.POINTS, is_home=True)
        assert results and all(r.probability_method == "legacy" for r in results)

    def test_team_abbr_falls_back_to_game(self, jokic_inputs):
        from engine.prop_evaluator import PropEvaluator

        player, game, defense, odds = jokic_inputs
        blank = copy.copy(player)
        blank.team_abbr = ""
        results = PropEvaluator().evaluate(blank, game, defense, odds, PropType.POINTS, is_home=True)
        assert results
        for r in results:
            assert r.team_abbr
            assert r.opponent_abbr and r.opponent_abbr != r.team_abbr

    def test_news_note_in_explanation(self, jokic_inputs):
        from engine.prop_evaluator import PropEvaluator

        player, game, defense, odds = jokic_inputs
        p = copy.copy(player)
        p.news_note = "Questionable, expected to play on a minutes limit"
        p.minutes_limit = 28.0
        results = PropEvaluator().evaluate(p, game, defense, odds, PropType.POINTS, is_home=True)
        assert results and all("Injury news" in r.explanation for r in results)


class TestMinutesLimit:
    def test_limit_caps_projection(self, jokic_inputs):
        from models.minutes_model import MinutesModel

        player, game, _, _ = jokic_inputs
        base = MinutesModel().project(player, game, is_home=True)
        capped = copy.copy(player)
        capped.minutes_limit = 20.0
        assert base > 20.0
        assert MinutesModel().project(capped, game, is_home=True) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# LLM injury-news features (no network)
# ---------------------------------------------------------------------------

class _FakeNewsClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0
        self.config = SimpleNamespace(model="fake-model")

    def complete(self, system_prompt, messages, schema, schema_name):
        self.calls += 1
        assert schema_name == "injury_news_features"
        assert "GAME DATE: 2026-11-02 (Monday)" in messages[0]["content"]
        return SimpleNamespace(text=json.dumps(self.payload))


class TestNewsFeatures:
    ROWS = [
        {"player_id": "1", "player_name": "A", "status": "Questionable",
         "injury": "Ankle; coach says he will play but on a minutes restriction (~24)"},
        {"player_id": "2", "player_name": "B", "status": "Day-To-Day",
         "injury": "Ruled out for Monday's game vs BOS"},
        {"player_id": "3", "player_name": "C", "status": "Out", "injury": ""},
    ]
    PAYLOAD = {"players": [
        {"player_id": "1", "status_tonight": "active", "minutes_limit": 24, "confidence": 0.8, "reason": "minutes cap"},
        {"player_id": "2", "status_tonight": "out", "minutes_limit": 30, "confidence": 0.9, "reason": "ruled out"},
        {"player_id": "99", "status_tonight": "out", "minutes_limit": None, "confidence": 1.0, "reason": "unknown id"},
    ]}

    def test_parse_drops_unknown_and_clamps(self):
        from engine.news_features import parse_features

        text = json.dumps({"players": [
            {"player_id": "1", "status_tonight": "active", "minutes_limit": 3, "confidence": 1.4, "reason": "x"},
            {"player_id": "1", "status_tonight": "out", "minutes_limit": None, "confidence": 0.9, "reason": "dup"},
            {"player_id": "2", "status_tonight": "maybe", "minutes_limit": None, "confidence": 0.9, "reason": "bad"},
            {"player_id": "7", "status_tonight": "out", "minutes_limit": None, "confidence": 0.9, "reason": "?"},
        ]})
        out = parse_features(text, {"1", "2"})
        assert set(out) == {"1"}
        assert out["1"].confidence == 1.0
        assert out["1"].minutes_limit == 10.0

    def test_parse_drops_nan_confidence(self):
        from engine.news_features import parse_features

        text = '{"players": [{"player_id": "1", "status_tonight": "out", "minutes_limit": null, "confidence": NaN, "reason": "x"}]}'
        assert parse_features(text, {"1"}) == {}

    def test_extract_apply_and_cache(self, tmp_path):
        from engine.news_features import apply_news_features, extract_news_features

        client = _FakeNewsClient(self.PAYLOAD)
        feats = extract_news_features(self.ROWS, date(2026, 11, 2), client=client, cache_dir=tmp_path)
        assert set(feats) == {"1", "2"}
        again = extract_news_features(self.ROWS, date(2026, 11, 2), client=client, cache_dir=tmp_path)
        assert client.calls == 1 and set(again) == {"1", "2"}

        index = {r["player_id"]: dict(r) for r in self.ROWS}
        counts = apply_news_features(index, feats)
        assert index["1"]["status"] == "active" and index["1"]["minutes_limit"] == 24
        assert index["1"]["status_raw"] == "Questionable"
        assert index["2"]["status"] == "out" and "minutes_limit" not in index["2"]
        assert counts == {"status_overrides": 2, "minutes_limits": 1}

    def test_low_confidence_is_ignored(self):
        from engine.news_features import NewsFeature, apply_news_features

        index = {"1": {"status": "Questionable"}}
        counts = apply_news_features(index, {"1": NewsFeature("1", "out", None, 0.4, "rumour")})
        assert index["1"]["status"] == "Questionable"
        assert counts["status_overrides"] == 0

    def test_failure_returns_empty(self, tmp_path):
        from engine.news_features import extract_news_features

        class Boom:
            config = SimpleNamespace(model="m")

            def complete(self, *a, **k):
                raise RuntimeError("down")

        assert extract_news_features(self.ROWS, date(2026, 11, 2), client=Boom(), cache_dir=tmp_path) == {}

    def test_disabled_by_env(self, monkeypatch):
        from engine.news_features import news_features_enabled

        monkeypatch.setenv("AI_NEWS_FEATURES", "0")
        assert news_features_enabled() is False


# ---------------------------------------------------------------------------
# Copula parlays
# ---------------------------------------------------------------------------

def _prop(pid, prop_type, game="g1", side=PropSide.OVER, p=0.7, edge=0.03, odds=-250, team="BOS"):
    return PropProbability(
        player_id=pid, player_name=f"P{pid}", team_abbr=team, opponent_abbr="MIA", game_id=game,
        prop_type=prop_type, line=10.5, side=side, projected_value=12.0,
        true_probability=p, implied_probability=p - edge, edge=edge, fair_odds=-233,
        sportsbook_odds=odds, best_book=BookName.KALSHI, confidence=ConfidenceTier.MEDIUM,
        distribution_type=DistributionType.NORMAL, explanation="", all_lines=[],
    )


class TestCopula:
    def test_different_games_are_independent(self):
        from engine.parlay_copula import parlay_joint_probability

        legs = [_prop("1", PropType.POINTS, "g1"), _prop("2", PropType.POINTS, "g2"), _prop("3", PropType.REBOUNDS, "g3")]
        assert parlay_joint_probability(legs) == pytest.approx(0.7 ** 3)

    def test_same_player_positive_correlation_raises_joint(self):
        from engine.parlay_copula import parlay_joint_probability

        legs = [_prop("1", PropType.POINTS), _prop("1", PropType.THREES)]
        joint = parlay_joint_probability(legs)
        assert 0.49 < joint <= 0.7

    def test_over_under_mix_lowers_joint(self):
        from engine.parlay_copula import parlay_joint_probability

        legs = [_prop("1", PropType.POINTS), _prop("1", PropType.THREES, side=PropSide.UNDER)]
        assert parlay_joint_probability(legs) < 0.49

    def test_fit_correlations(self, tmp_path):
        from engine.parlay_copula import fit_correlations, load_correlations

        rng = np.random.default_rng(0)
        rows = []
        for g in range(200):
            shared = rng.normal()
            for prop, noise in (("points", 0.5), ("threes", 0.5)):
                rows.append({"variant": "A_opportunity", "game_id": f"g{g}", "player_id": "1",
                             "prop_type": prop, "actual": shared + noise * rng.normal(), "projected": 0.0})
            rows.append({"variant": "A_opportunity", "game_id": f"g{g}", "player_id": "2",
                         "prop_type": "points", "actual": rng.normal(), "projected": 0.0})
        src = tmp_path / "bt.json"
        src.write_text(json.dumps({"rows": rows}), encoding="utf-8")
        out = tmp_path / "corr.json"
        res = fit_correlations([src], out)
        assert res["same_player"]["points|threes"] > 0.6
        assert load_correlations(out)["same_player"]["points|threes"] == res["same_player"]["points|threes"]

    def test_builder_uses_copula(self, monkeypatch):
        from engine.parlay_builder import ParlayConstraints, build_parlays

        monkeypatch.setattr(constants, "USE_COPULA_PARLAYS", True)
        legs = [_prop("1", PropType.POINTS, "g1"), _prop("2", PropType.POINTS, "g2")]
        parlays = build_parlays(legs, ParlayConstraints(min_edge=0.0, min_legs=2, max_legs=2,
                                                        min_leg_odds=-600, max_leg_odds=-100))
        assert len(parlays) == 1
        assert parlays[0].combined_true_probability == pytest.approx(0.49)


# ---------------------------------------------------------------------------
# Kelly
# ---------------------------------------------------------------------------

class TestKelly:
    def test_zero_without_edge(self):
        from engine.bankroll_engine import fractional_kelly

        assert fractional_kelly(0.5, 1.9) == 0.0

    def test_quarter_kelly(self):
        from engine.bankroll_engine import fractional_kelly

        p, dec = 0.75, 1.4  # full Kelly = (0.4*0.75 - 0.25)/0.4 = 0.125
        assert fractional_kelly(p, dec) == pytest.approx(0.125 * constants.KELLY_FRACTION)

    def test_cap(self):
        from engine.bankroll_engine import fractional_kelly

        assert fractional_kelly(0.95, 3.0) == constants.KELLY_MAX_BANKROLL_FRACTION

    def test_advisor_rows_and_payload(self):
        from engine.advisor.candidates import CandidateStats
        from engine.advisor.parlays import build_parlay_rows, build_pick_rows
        from engine.advisor.payload import PICK_FIELDS, build_payload

        props = [_prop("1", PropType.POINTS, "g1", p=0.80, edge=0.05),
                 _prop("2", PropType.POINTS, "g2", p=0.78, edge=0.04)]
        for pr in props:
            pr.market_probability = 0.76
            pr.model_weight = 0.25
            pr.probability_method = "market_blend"
        picks = build_pick_rows(props, 100.0)
        parlays = build_parlay_rows(picks, stake=100.0, min_odds=-600, max_odds=-200, min_edge=0.0)
        assert picks[0].kelly_fraction > 0
        assert picks[0].kelly_stake(1000.0) == round(1000.0 * picks[0].kelly_fraction, 2)
        assert picks[0].kelly_stake(None) is None
        payload = build_payload(game_date=date(2026, 11, 2), picks=picks, parlays=parlays,
                                stats=CandidateStats(), min_odds=-600, max_odds=-200, min_edge=0.0,
                                stake=100.0, bankroll=1000.0)
        pick = payload["picks"][0]
        assert set(PICK_FIELDS) == set(pick)
        assert pick["market_probability"] == 0.76 and pick["probability_method"] == "market_blend"
        assert pick["kelly"]["suggested_stake"] == picks[0].kelly_stake(1000.0)
        assert payload["settings"]["bankroll"] == 1000.0
        assert "kelly" in payload["parlays"][0]


# ---------------------------------------------------------------------------
# CLV and grading
# ---------------------------------------------------------------------------

def _ledger_row(**kw):
    row = {
        "player": "Test Player", "player_id": "1", "game_id": "g1", "prop": "points over 20.5",
        "prop_type": "points", "side": "over", "line": 20.5, "sportsbook": "kalshi", "odds": -150,
        "implied_probability": 0.60, "market_probability": 0.58, "model_probability": 0.62, "edge": 0.02,
    }
    row.update(kw)
    return row


class TestCLV:
    def test_parse_legacy_prop_text(self):
        from engine.clv import parse_ledger_row

        assert parse_ledger_row({"prop": "rebounds under 7.5"}) == ("rebounds", "under", 7.5)
        assert parse_ledger_row(_ledger_row()) == ("points", "over", 20.5)

    def test_record_closing_and_grade(self, tmp_path):
        from engine.clv import grade_predictions, record_closing

        d = date(2026, 11, 2)
        rows = [_ledger_row(), _ledger_row(player_id="2", player="Other Guy", game_id="g2",
                                           prop="rebounds under 7.5", prop_type="rebounds",
                                           side="under", line=7.5, edge=-0.01)]
        (tmp_path / f"{d.isoformat()}.json").write_text(json.dumps(rows), encoding="utf-8")

        odds = [_line(-200, 170), _line(-180, 150, book=BookName.POLYMARKET)]
        rec = record_closing(d, tmp_path, odds_lines=odds, pregame_game_ids={"g1"},
                             now=datetime(2026, 11, 2, 23, tzinfo=timezone.utc))
        assert rec["updated"] == 1
        closing = json.loads((tmp_path / f"{d.isoformat()}.closing.json").read_text(encoding="utf-8"))
        assert len(closing) == 1 and closing[0]["clv"] > 0  # price moved toward our side

        logs = [
            {"game_id": "g1", "player_id": "1", "player_name": "Test Player", "min": 34, "pts": 25, "reb": 5},
            {"game_id": "g2", "player_id": "2", "player_name": "Other Guy", "min": 30, "pts": 10, "reb": 9},
        ]
        summary = grade_predictions(d, tmp_path, final_game_ids={"g1", "g2"}, box_logs=logs)
        assert summary["all"]["decided"] == 2
        assert summary["all"]["hit_rate"] == 0.5
        assert summary["positive_edge"]["n"] == 1
        assert summary["positive_edge"]["hit_rate"] == 1.0
        assert summary["positive_edge"]["avg_clv"] > 0
        assert summary["positive_edge"]["flat_roi_per_bet"] == pytest.approx(100 / 150, abs=1e-4)
        assert "brier_market" in summary["all"]

    def test_dnp_is_void(self, tmp_path):
        from engine.clv import grade_predictions

        d = date(2026, 11, 3)
        (tmp_path / f"{d.isoformat()}.json").write_text(json.dumps([_ledger_row()]), encoding="utf-8")
        logs = [{"game_id": "g1", "player_id": "1", "player_name": "Test Player", "min": 0, "pts": 0}]
        summary = grade_predictions(d, tmp_path, final_game_ids={"g1"}, box_logs=logs)
        assert summary["all"]["n"] == 1 and summary["all"]["decided"] == 0
