"""
Model-agreement check, the shared board step, and the advisor's wider pool and pass list.

No network: the candidate model and OpenAI are stubbed.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from domain.enums import ConfidenceTier, PropSide
from engine.advisor.candidates import select_candidates
from engine.advisor.report import to_markdown
from engine.advisor.run import AI_OK, AdvisorOptions, run_advisor
from engine.advisor.schema import AdvisorResponseError, parse_response, validate_response
from engine.model_agreement import (
    MODEL_DISAGREES_FLAG,
    flag_model_disagreement,
    model_agrees,
    own_model_probability,
)
from tests.test_advisor import FakeClient, GAME_DATE, good_response, make_prop


def disagreeing_prop(name="Giannis Under", edge=0.029):
    """Weight-0 blend: true probability is consensus, but the model's own chance is far below the price."""
    p = make_prop(name, odds=398, edge=edge, true_prob=0.229, side=PropSide.UNDER, line=7.5)
    p.raw_model_probability = 0.08
    p.probability_method = "market_blend"
    return p


def agreeing_prop(name="Wiggins Under", edge=0.069):
    p = make_prop(name, odds=159, edge=edge, true_prob=0.455, side=PropSide.UNDER, line=9.5)
    p.raw_model_probability = 0.55
    p.probability_method = "market_blend"
    return p


# ---------------------------------------------------------------------------
# Model agreement
# ---------------------------------------------------------------------------

class TestModelAgreement:
    def test_own_probability_by_method(self):
        blend = disagreeing_prop()
        assert own_model_probability(blend) == pytest.approx(0.08)
        cand = make_prop("Cand", true_prob=0.6, edge=0.05)
        cand.probability_method = "candidate"
        assert own_model_probability(cand) == pytest.approx(0.6)
        unknown = make_prop("Unknown")  # raw_model_probability left at 0
        assert own_model_probability(unknown) is None and model_agrees(unknown)

    def test_flags_downgrades_and_is_idempotent(self):
        bad, good = disagreeing_prop(), agreeing_prop()
        negative = disagreeing_prop("Negative", edge=-0.02)
        counts = flag_model_disagreement([bad, good, negative])
        assert counts == {"positive_edge": 2, "model_disagrees": 1}
        assert bad.model_agrees is False and good.model_agrees is True and negative.model_agrees is False
        assert bad.confidence == ConfidenceTier.LOW  # one tier down from MEDIUM
        assert any(w.startswith(MODEL_DISAGREES_FLAG) for w in bad.calibration_warnings)
        assert good.confidence == ConfidenceTier.MEDIUM and not good.calibration_warnings
        assert negative.confidence == ConfidenceTier.MEDIUM  # no edge, nothing to flag

        flag_model_disagreement([bad])
        assert bad.confidence == ConfidenceTier.LOW
        assert sum(w.startswith(MODEL_DISAGREES_FLAG) for w in bad.calibration_warnings) == 1

    def test_advisor_filter_drops_disagreeing_props_by_default(self):
        props = [disagreeing_prop(), agreeing_prop()]
        top, _ranked, stats = select_candidates(props, min_odds=-600, max_odds=400)
        assert [p.player_name for p in top] == ["Wiggins Under"]
        assert (stats.confidence_ok, stats.model_agrees) == (2, 1)
        top, _ranked, stats = select_candidates(props, min_odds=-600, max_odds=400, require_model_agreement=False)
        assert len(top) == 2 and stats.model_agrees == 2

    def test_ledger_records_agreement(self, tmp_path):
        from engine.prediction_ledger import save_pregame_predictions

        bad = disagreeing_prop()
        flag_model_disagreement([bad])
        path = save_pregame_predictions(date(2026, 11, 5), [bad], directory=tmp_path)
        assert json.loads(path.read_text(encoding="utf-8"))[0]["model_agrees"] is False


# ---------------------------------------------------------------------------
# Shared board step
# ---------------------------------------------------------------------------

class TestBoard:
    def test_candidate_failure_keeps_board_and_still_checks_agreement(self, monkeypatch):
        import engine.candidate.live as live
        from engine.board import enrich_props

        def boom(*a, **k):
            raise RuntimeError("logs offline")

        monkeypatch.setattr(live, "attach_candidate", boom)
        bad = disagreeing_prop()
        out = enrich_props([bad], date(2026, 11, 5), matchups=False)
        assert out["candidate_status"] == {"status": "error", "reason": "logs offline"}
        assert out["agreement"]["model_disagrees"] == 1 and bad.model_agrees is False

    def test_candidate_edges_feed_the_agreement_check(self, monkeypatch):
        """The candidate replaces the probability first, so a prop it backs is no longer flagged."""
        import engine.candidate.live as live
        from engine.board import enrich_props

        def attach(props, slate, games=None):
            for p in props:
                p.candidate_probability = 0.30
            return {"status": "ok", "attached": len(props)}

        def apply(props):
            for p in props:
                p.production_probability, p.true_probability = p.true_probability, p.candidate_probability
                p.edge = p.true_probability - p.implied_probability
                p.probability_method = "candidate"
            return {"applied": len(props), "enabled": True}

        monkeypatch.setattr(live, "attach_candidate", attach)
        monkeypatch.setattr(live, "apply_candidate_edges", apply)
        prop = disagreeing_prop()
        out = enrich_props([prop], date(2026, 11, 5), matchups=False)
        assert out["candidate_status"]["edges"]["applied"] == 1
        assert prop.probability_method == "candidate" and prop.model_agrees is True
        assert out["agreement"]["model_disagrees"] == 0

    def test_advisor_scan_uses_the_board(self, monkeypatch, tmp_path):
        import engine.board as board_mod

        calls = {}

        def fake_board(day, *, prop_types=None, is_playoff=False, matchups=True):
            calls["matchups"] = matchups
            return board_mod.Board(game_date=day, props=[agreeing_prop()], odds_source_counts={"kalshi": 1},
                                   pregame_games=1)

        monkeypatch.setattr(board_mod, "scan_board", fake_board)
        result = run_advisor(GAME_DATE, AdvisorOptions(min_odds=-600, max_odds=400, use_llm=False,
                                                       output_dir=tmp_path))
        assert calls == {"matchups": False}
        assert result.pregame_games == 1 and result.odds_source_counts == {"kalshi": 1}
        assert [r.prop.player_name for r in result.picks] == ["Wiggins Under"]


# ---------------------------------------------------------------------------
# Wider pool and pass list
# ---------------------------------------------------------------------------

def eight_props():
    return [make_prop(f"Player {i}", odds=-220, edge=0.08 - i * 0.005, true_prob=0.76) for i in range(8)]


class TestPool:
    def _opts(self, tmp_path, **kw):
        return AdvisorOptions(output_dir=tmp_path, top_n=3, pool_n=6, save=False, **kw)

    def test_pool_shortlist_and_parlays(self, tmp_path):
        result = run_advisor(GAME_DATE, self._opts(tmp_path, use_llm=False), props=eight_props())
        assert [r.id for r in result.picks] == ["P1", "P2", "P3", "P4", "P5", "P6"]
        assert [r.id for r in result.shortlist] == ["P1", "P2", "P3"]
        assert result.stats.pool == 6 and result.stats.selected == 3
        assert result.deterministic.top_pick_ids == ["P1", "P2", "P3"]
        assert result.parlays and all(set(p.leg_ids) <= {"P1", "P2", "P3"} for p in result.parlays)
        payload = result.payload
        assert payload["settings"]["top_n"] == 3
        assert [p["in_shortlist"] for p in payload["picks"]] == [True] * 3 + [False] * 3
        first = payload["picks"][0]
        assert first["model_agrees_with_edge"] is True and "venue_gap" in first

    def test_ai_can_pick_from_the_pool_and_pass(self, tmp_path):
        text = good_response(
            ("P5", "P1"),
            passes=[{"id": "P2", "reason": "Edge is only the venue price."},
                    {"id": "P3", "reason": "Thin minutes."}],
        )
        client = FakeClient(texts=[text])
        result = run_advisor(GAME_DATE, self._opts(tmp_path), props=eight_props(), client=client)
        assert result.ai_status == AI_OK, result.ai_error
        assert [tp.id for tp in result.ai_response.top_picks] == ["P5", "P1"]
        md = to_markdown(result)
        assert "**Passed:**" in md and "P2 Player 1" in md and "Thin minutes." in md
        assert "| P5 | 1 | Player 4 |" in md and "| P2 | pass | Player 1 |" in md

    def test_track_record_reaches_the_payload(self, tmp_path, monkeypatch):
        import engine.advisor.run as run_mod

        monkeypatch.setattr(run_mod, "track_record", lambda: {"decided_props": 10})
        client = FakeClient(texts=[good_response(("P1",))])
        result = run_advisor(GAME_DATE, self._opts(tmp_path), props=eight_props(), client=client)
        assert result.payload["track_record"] == {"decided_props": 10}
        sent = client.calls[0][0]["content"]
        assert '"track_record"' in sent and '"in_shortlist"' in sent


class TestPassValidation:
    def _validate(self, text, pick_ids=("P1", "P2", "P3"), parlays=None, max_picks=5):
        parlays = parlays or {}
        return validate_response(
            parse_response(text),
            pick_ids=list(pick_ids),
            parlay_legs={k: v for k, v in parlays.items()},
            parlay_numbers={k: {"total_return": 300.0, "profit": 200.0} for k in parlays},
            max_picks=max_picks,
        )

    def test_unknown_or_duplicate_pass_rejected(self):
        with pytest.raises(AdvisorResponseError, match="Unknown passed"):
            self._validate(good_response(("P1",), passes=[{"id": "P9", "reason": "x"}]))
        with pytest.raises(AdvisorResponseError, match="Duplicate passed"):
            self._validate(good_response(("P1",), passes=[{"id": "P2", "reason": "x"}, {"id": "P2", "reason": "y"}]))

    def test_pick_cannot_be_both_picked_and_passed(self):
        with pytest.raises(AdvisorResponseError, match="both a top pick and passed"):
            self._validate(good_response(("P1",), passes=[{"id": "P1", "reason": "x"}]))

    def test_parlay_with_passed_leg_rejected(self):
        parlay = {"id": "PL1", "legs": ["P1", "P2"], "rationale": "r", "key_risks": "k",
                  "total_return": None, "profit": None}
        text = good_response(("P1",), parlay=parlay, passes=[{"id": "P2", "reason": "x"}])
        with pytest.raises(AdvisorResponseError, match="uses passed pick"):
            self._validate(text, parlays={"PL1": ["P1", "P2"]})

    def test_top_n_caps_picks_and_empty_is_valid(self):
        with pytest.raises(AdvisorResponseError, match="at most 2"):
            self._validate(good_response(("P1", "P2", "P3")), max_picks=2)
        resp, _ = self._validate(good_response((), passes=[{"id": "P1", "reason": "weak"}]))
        assert resp.top_picks == [] and resp.passes[0].id == "P1"
