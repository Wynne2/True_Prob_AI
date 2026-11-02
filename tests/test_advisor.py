"""
Tests for the AI Advisor (engine/advisor).

All OpenAI traffic is mocked; no network calls are made.
"""

from __future__ import annotations

import json
import random
from datetime import date

import pytest
import requests

from config import OpenAIConfig
from domain.entities import PropProbability
from domain.enums import BookName, ConfidenceTier, DistributionType, PropSide, PropType
from engine.advisor.candidates import select_candidates
from engine.advisor.client import AdvisorLLMError, LLMResult, OpenAIAdvisorClient, extract_output_text
from engine.advisor.parlays import build_parlay_rows, build_pick_rows
from engine.advisor.payload import PARLAY_FIELDS, PICK_FIELDS, build_payload, payload_to_json
from engine.advisor.prompts import default_user_prompt
from engine.advisor.run import AI_DISABLED, AI_FAILED, AI_OK, AI_SKIPPED, AdvisorOptions, run_advisor
from engine.advisor.schema import (
    AdvisorResponseError,
    parse_response,
    response_json_schema,
    validate_response,
)

FAKE_KEY = "sk-test-SECRETKEY1234567890abcdef"
GAME_DATE = date(2026, 11, 1)


def make_prop(
    name: str,
    *,
    odds: int = -250,
    edge: float = 0.04,
    true_prob: float = 0.75,
    prop_type: PropType = PropType.POINTS,
    side: PropSide = PropSide.OVER,
    line: float = 10.5,
    game_id: str | None = None,
    team: str | None = None,
    confidence: ConfidenceTier = ConfidenceTier.MEDIUM,
    book: BookName = BookName.KALSHI,
    explanation: str = "Model note",
) -> PropProbability:
    return PropProbability(
        player_id=name.lower().replace(" ", "_"),
        player_name=name,
        team_abbr=team if team is not None else f"T{name[:3].upper()}",
        opponent_abbr="OPP",
        game_id=game_id or f"g_{name}",
        prop_type=prop_type,
        line=line,
        side=side,
        projected_value=line + 2.0,
        true_probability=true_prob,
        implied_probability=true_prob - edge,
        edge=edge,
        fair_odds=-300,
        sportsbook_odds=odds,
        best_book=book,
        confidence=confidence,
        distribution_type=DistributionType.NORMAL,
        explanation=explanation,
        all_lines=[],
        expected_minutes=30.0,
    )


def five_props() -> list[PropProbability]:
    return [
        make_prop("Alpha One", odds=-210, edge=0.06, true_prob=0.74),
        make_prop("Bravo Two", odds=-230, edge=0.05, true_prob=0.75, book=BookName.POLYMARKET),
        make_prop("Charlie Three", odds=-300, edge=0.045, true_prob=0.80),
        make_prop("Delta Four", odds=-260, edge=0.04, true_prob=0.76),
        make_prop("Echo Five", odds=-400, edge=0.03, true_prob=0.83),
    ]


def test_config(**overrides) -> OpenAIConfig:
    values = dict(
        api_key=FAKE_KEY,
        model="gpt-6.1-sol",
        reasoning_effort="medium",
        timeout=5,
        max_output_tokens=2000,
        base_url="https://api.openai.test/v1",
    )
    values.update(overrides)
    return OpenAIConfig(**values)


test_config.__test__ = False  # not a pytest test


class FakeClient:
    """Stands in for OpenAIAdvisorClient; returns queued texts."""

    def __init__(self, texts=(), verify_exc=None, complete_exc=None):
        self.config = test_config()
        self.transport = "fake"
        self.texts = list(texts)
        self.verify_exc = verify_exc
        self.complete_exc = complete_exc
        self.calls: list[list[dict]] = []
        self.verified = 0

    def verify_model(self):
        self.verified += 1
        if self.verify_exc:
            raise self.verify_exc

    def complete(self, system_prompt, messages, schema):
        self.calls.append(messages)
        if self.complete_exc:
            raise self.complete_exc
        return LLMResult(
            text=self.texts.pop(0),
            model="gpt-6.1-sol",
            response_id=f"resp_{len(self.calls)}",
            status="completed",
            latency_s=0.25,
            usage={"input_tokens": 1000, "output_tokens": 200, "reasoning_tokens": 50},
            transport="fake",
        )


def good_response(pick_ids=("P1", "P2", "P3"), parlay=None, **extra) -> str:
    data = {
        "top_picks": [
            {"id": pid, "rank": i, "rationale": f"Edge is strongest for {pid}.", "risk_notes": "Minutes."}
            for i, pid in enumerate(pick_ids, start=1)
        ],
        "parlay_recommendations": [parlay] if parlay else [],
        "straight_bets_only": parlay is None,
        "summary": "Two-leg is the better risk/reward.",
        "responsible_gambling_note": "Bet only what you can afford to lose.",
    }
    data.update(extra)
    return json.dumps(data)


# ---------------------------------------------------------------------------
# Candidate filtering
# ---------------------------------------------------------------------------

class TestCandidates:
    def test_odds_band_inclusive_bounds(self):
        props = [
            make_prop("At Minus200", odds=-200),
            make_prop("At Minus600", odds=-600),
            make_prop("Out Minus199", odds=-199),
            make_prop("Out Minus601", odds=-601),
            make_prop("Out Plus150", odds=150),
        ]
        top, _, stats = select_candidates(props, top_n=10)
        names = {p.player_name for p in top}
        assert names == {"At Minus200", "At Minus600"}
        assert stats.in_odds_band == 2

    def test_zero_edge_excluded_and_min_edge_honoured(self):
        props = [
            make_prop("Zero Edge", edge=0.0),
            make_prop("Small Edge", edge=0.02),
            make_prop("Big Edge", edge=0.05),
        ]
        top, _, stats = select_candidates(props, top_n=10)
        assert {p.player_name for p in top} == {"Small Edge", "Big Edge"}
        assert stats.positive_edge == 2

        top, _, _ = select_candidates(props, min_edge=0.03, top_n=10)
        assert [p.player_name for p in top] == ["Big Edge"]

    def test_dedupe_keeps_best_price(self):
        props = [
            make_prop("Same Guy", odds=-400, line=8.5, edge=0.05),
            make_prop("Same Guy", odds=-250, line=10.5, edge=0.03),
            make_prop("Same Guy", odds=-220, side=PropSide.UNDER, edge=0.02),
        ]
        top, _, stats = select_candidates(props, top_n=10)
        assert stats.deduped == 2
        over = next(p for p in top if p.side == PropSide.OVER)
        assert over.sportsbook_odds == -250

    def test_deterministic_order(self):
        props = five_props() + [make_prop("Tie A", edge=0.03, true_prob=0.9),
                                make_prop("Tie B", edge=0.03, true_prob=0.9, confidence=ConfidenceTier.HIGH)]
        expected, _, _ = select_candidates(props, top_n=7)
        for seed in range(5):
            shuffled = props[:]
            random.Random(seed).shuffle(shuffled)
            got, _, _ = select_candidates(shuffled, top_n=7)
            assert [p.player_name for p in got] == [p.player_name for p in expected]
        assert expected[0].player_name == "Alpha One"
        # Equal edge: higher confidence first.
        idx = [p.player_name for p in expected]
        assert idx.index("Tie B") < idx.index("Tie A")

    def test_fewer_than_n(self):
        top, ranked, stats = select_candidates(five_props()[:2], top_n=5)
        assert len(top) == 2 and stats.selected == 2 and len(ranked) == 2

    def test_min_confidence(self):
        props = [make_prop("Low", confidence=ConfidenceTier.LOW), make_prop("High", confidence=ConfidenceTier.HIGH)]
        top, _, stats = select_candidates(props, min_confidence="medium", top_n=5)
        assert [p.player_name for p in top] == ["High"]
        assert stats.confidence_ok == 1


# ---------------------------------------------------------------------------
# Parlay math
# ---------------------------------------------------------------------------

class TestParlayMath:
    def test_two_leg_minus200_minus300_pays_double(self):
        picks = build_pick_rows([make_prop("A Leg", odds=-200), make_prop("B Leg", odds=-300)], 100.0)
        rows = build_parlay_rows(picks, stake=100.0, min_odds=-600, max_odds=-200, min_edge=0.0)
        assert len(rows) == 1
        row = rows[0]
        assert row.combined_decimal_odds == pytest.approx(2.0)
        assert row.combined_american_odds == 100
        assert row.total_return == pytest.approx(200.0)
        assert row.profit == pytest.approx(100.0)
        assert row.implied_probability == pytest.approx((0.75 - 0.04) * (0.75 - 0.04))
        assert row.expected_value == pytest.approx(100.0 * (row.true_probability * 2.0 - 1.0))

    def test_three_leg(self):
        picks = build_pick_rows([make_prop(n, odds=-200) for n in ("A A", "B B", "C C")], 100.0)
        rows = build_parlay_rows(picks, stake=100.0, min_odds=-600, max_odds=-200, min_edge=0.0)
        three = [r for r in rows if r.num_legs == 3]
        assert len(three) == 1 and len([r for r in rows if r.num_legs == 2]) == 3
        assert three[0].combined_decimal_odds == pytest.approx(3.375)
        assert three[0].total_return == pytest.approx(337.5)
        assert three[0].profit == pytest.approx(237.5)
        assert [r.id for r in rows] == [f"PL{i}" for i in range(1, 5)]

    def test_correlated_legs_blocked(self):
        same_player = [
            make_prop("Same Guy", prop_type=PropType.POINTS),
            make_prop("Same Guy", prop_type=PropType.PRA, line=30.5),
            make_prop("Other Guy"),
        ]
        picks = build_pick_rows(same_player, 100.0)
        rows = build_parlay_rows(picks, stake=100.0, min_odds=-600, max_odds=-200, min_edge=0.0)
        legs = [set(r.leg_ids) for r in rows]
        assert {"P1", "P2"} not in legs
        assert all(len(s) == 2 for s in legs)  # every 3-leg combo contains the blocked pair
        assert legs  # P1+P3 and P2+P3 survive

    def test_straight_bet_math(self):
        rows = build_pick_rows([make_prop("A Leg", odds=-200, true_prob=0.7)], 100.0)
        assert rows[0].total_return == pytest.approx(150.0)
        assert rows[0].profit == pytest.approx(50.0)
        assert rows[0].expected_value == pytest.approx(100.0 * (0.7 * 1.5 - 1))


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

class TestPayload:
    def _payload(self, props):
        top, _, stats = select_candidates(props)
        picks = build_pick_rows(top, 100.0)
        parlays = build_parlay_rows(picks, stake=100.0, min_odds=-600, max_odds=-200, min_edge=0.0)
        return build_payload(
            game_date=GAME_DATE, picks=picks, parlays=parlays, stats=stats,
            min_odds=-600, max_odds=-200, min_edge=0.0, stake=100.0,
            odds_source_counts={"kalshi": 10, "polymarket": 5},
        )

    def test_stable_ids_and_whitelisted_fields(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
        props = five_props()
        props[0].explanation = "x" * 2000
        p1 = self._payload(props)
        p2 = self._payload(list(reversed(props)))
        assert p1 == p2
        assert [p["id"] for p in p1["picks"]] == ["P1", "P2", "P3", "P4", "P5"]
        assert all(set(p) == set(PICK_FIELDS) for p in p1["picks"])
        assert all(set(p) == set(PARLAY_FIELDS) for p in p1["parlays"])
        assert p1["parlays"][0]["id"] == "PL1"
        assert len(p1["picks"][0]["model_notes"]) <= 280
        assert p1["picks"][1]["venue"] == "Polymarket"

    def test_no_secrets(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
        text = payload_to_json(self._payload(five_props()))
        assert FAKE_KEY not in text
        assert "sk-" not in text
        assert "api_key" not in text.lower()
        assert "http" not in text.lower()


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------

class TestValidation:
    PICKS = ["P1", "P2", "P3"]
    LEGS = {"PL1": ["P1", "P2"], "PL2": ["P1", "P2", "P3"], "PL3": ["P1", "P3"]}
    NUMBERS = {
        "PL1": {"total_return": 200.0, "profit": 100.0},
        "PL2": {"total_return": 300.0, "profit": 200.0},
        "PL3": {"total_return": 210.0, "profit": 110.0},
    }

    def _validate(self, text):
        return validate_response(
            parse_response(text), pick_ids=self.PICKS, parlay_legs=self.LEGS,
            parlay_numbers=self.NUMBERS, allowed_amounts={100.0, 200.0, 300.0, 210.0, 110.0},
        )

    def test_unknown_pick_id_rejected(self):
        with pytest.raises(AdvisorResponseError, match="Unknown pick ID"):
            self._validate(good_response(pick_ids=("P1", "P9")))

    def test_unknown_parlay_id_rejected(self):
        parlay = {"id": "PL99", "legs": ["P1", "P2"], "rationale": "r", "key_risks": "k",
                  "total_return": None, "profit": None}
        with pytest.raises(AdvisorResponseError, match="Unknown parlay ID"):
            self._validate(good_response(parlay=parlay))

    def test_leg_mismatch_rejected(self):
        parlay = {"id": "PL1", "legs": ["P1", "P3"], "rationale": "r", "key_risks": "k",
                  "total_return": None, "profit": None}
        with pytest.raises(AdvisorResponseError, match="do not match"):
            self._validate(good_response(parlay=parlay))

    def test_two_parlays_same_leg_count_rejected(self):
        data = json.loads(good_response())
        data["straight_bets_only"] = False
        data["parlay_recommendations"] = [
            {"id": "PL1", "legs": ["P1", "P2"], "rationale": "r", "key_risks": "k", "total_return": None, "profit": None},
            {"id": "PL3", "legs": ["P1", "P3"], "rationale": "r", "key_risks": "k", "total_return": None, "profit": None},
        ]
        with pytest.raises(AdvisorResponseError, match="More than one 2-leg"):
            self._validate(json.dumps(data))

    def test_wrong_payout_overwritten(self):
        parlay = {"id": "PL1", "legs": ["P2", "P1"], "rationale": "Pays $250 on $100.", "key_risks": "k",
                  "total_return": 250.0, "profit": 150.0}
        resp, warnings = self._validate(good_response(parlay=parlay))
        rec = resp.parlay_recommendations[0]
        assert rec.total_return == 200.0 and rec.profit == 100.0
        assert rec.legs == ["P1", "P2"]
        assert any("total_return" in w for w in warnings)
        assert any("$250" in w for w in warnings)

    def test_malformed_json_and_schema(self):
        with pytest.raises(AdvisorResponseError):
            parse_response("not json")
        with pytest.raises(AdvisorResponseError):
            parse_response(json.dumps({"top_picks": []}))
        with pytest.raises(AdvisorResponseError):
            parse_response(good_response(pick_ids=("P1", "P2", "P3", "P4", "P5", "P6")))

    def test_schema_is_strict(self):
        schema = response_json_schema()

        def check(node):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
                for child in node["properties"].values():
                    check(child)
            if node.get("type") == "array":
                check(node["items"])

        check(schema)


# ---------------------------------------------------------------------------
# run_advisor orchestration
# ---------------------------------------------------------------------------

class TestRunAdvisor:
    def _opts(self, tmp_path, **kw):
        return AdvisorOptions(output_dir=tmp_path, **kw)

    def test_zero_candidates_skip_llm(self, tmp_path):
        client = FakeClient()
        props = [make_prop("Too Short", odds=-150), make_prop("Neg Edge", edge=-0.01)]
        result = run_advisor(GAME_DATE, self._opts(tmp_path), props=props, client=client)
        assert result.ai_status == AI_SKIPPED
        assert client.calls == [] and client.verified == 0
        assert result.stats.total_props == 2 and result.stats.selected == 0
        assert result.json_path.exists()

    def test_successful_run_merges_and_overwrites(self, tmp_path):
        probe = run_advisor(GAME_DATE, self._opts(tmp_path, save=False, use_llm=False), props=five_props())
        pl = next(p for p in probe.parlays if p.num_legs == 2)
        parlay = {"id": pl.id, "legs": pl.leg_ids, "rationale": "Best two.", "key_risks": "Variance.",
                  "total_return": 9999.0, "profit": 1.0}
        client = FakeClient([good_response(parlay=parlay)])
        result = run_advisor(GAME_DATE, self._opts(tmp_path), props=five_props(), client=client)
        assert result.ai_status == AI_OK
        assert len(client.calls) == 1
        rec = result.ai_response.parlay_recommendations[0]
        assert rec.total_return == pytest.approx(round(pl.total_return, 2))
        assert any("overwritten" in w for w in result.warnings)
        assert result.llm_meta["model"] == "gpt-6.1-sol"
        assert result.llm_meta["usage"]["input_tokens"] == 1000
        saved = json.loads(result.json_path.read_text(encoding="utf-8"))
        assert saved["validated_output"]["parlay_recommendations"][0]["total_return"] == rec.total_return
        assert saved["request"]["reasoning_effort"] == "medium"
        assert FAKE_KEY not in result.json_path.read_text(encoding="utf-8")
        assert "AI reasoning" in result.md_path.read_text(encoding="utf-8")

    def test_malformed_then_valid_retries_once(self, tmp_path):
        client = FakeClient(["{not json", good_response()])
        result = run_advisor(GAME_DATE, self._opts(tmp_path, save=False), props=five_props(), client=client)
        assert result.ai_status == AI_OK
        assert len(client.calls) == 2
        assert result.llm_meta["attempts"] == 2
        retry_msgs = client.calls[1]
        assert retry_msgs[-1]["role"] == "user" and "failed validation" in retry_msgs[-1]["content"]

    def test_malformed_twice_falls_back(self, tmp_path):
        client = FakeClient(["{bad", good_response(pick_ids=("P42",))])
        result = run_advisor(GAME_DATE, self._opts(tmp_path, save=False), props=five_props(), client=client)
        assert result.ai_status == AI_FAILED
        assert len(client.calls) == 2
        assert "twice" in result.ai_error
        det = result.deterministic
        assert det.top_pick_ids == ["P1", "P2", "P3", "P4", "P5"]
        assert det.best_two_leg is not None and det.best_three_leg is not None

    @pytest.mark.parametrize("where", ["verify", "complete"])
    def test_client_errors_fall_back(self, tmp_path, where):
        exc = AdvisorLLMError("Model 'gpt-6.1-sol' is not available") if where == "verify" else AdvisorLLMError("timed out")
        client = FakeClient(verify_exc=exc) if where == "verify" else FakeClient(complete_exc=exc)
        result = run_advisor(GAME_DATE, self._opts(tmp_path, save=False), props=five_props(), client=client)
        assert result.ai_status == AI_FAILED
        assert result.ai_response is None
        assert str(exc) in result.ai_error
        assert result.deterministic.top_pick_ids

    def test_no_llm(self, tmp_path):
        client = FakeClient()
        result = run_advisor(GAME_DATE, self._opts(tmp_path, use_llm=False), props=five_props(), client=client)
        assert result.ai_status == AI_DISABLED
        assert client.calls == [] and client.verified == 0
        assert "Deterministic recommendation" in result.md_path.read_text(encoding="utf-8")

    def test_default_prompt(self):
        assert default_user_prompt(min_odds=-600, max_odds=-200, top_n=5, stake=100) == (
            "Run the model to find the best positive-edge props with odds between -200 and -600. "
            "Give me the top 5 picks. Reason over them to decide whether I should combine them into a "
            "2-leg or 3-leg parlay, which combination is best and why, and how much I get back if I put "
            "$100 on it."
        )


# ---------------------------------------------------------------------------
# OpenAI client (HTTP transport, mocked session)
# ---------------------------------------------------------------------------

class FakeHTTPResponse:
    def __init__(self, status_code=200, data=None, text=""):
        self.status_code = status_code
        self._data = data
        self.text = text or json.dumps(data or {})

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, get_resp=None, post_resp=None, post_exc=None):
        self.get_resp = get_resp or FakeHTTPResponse(200, {"id": "gpt-6.1-sol"})
        self.post_resp = post_resp
        self.post_exc = post_exc
        self.gets: list[tuple] = []
        self.posts: list[dict] = []

    def get(self, url, headers=None, timeout=None):
        self.gets.append((url, headers))
        return self.get_resp

    def post(self, url, headers=None, data=None, timeout=None):
        self.posts.append({"url": url, "headers": headers, "body": json.loads(data), "timeout": timeout})
        if self.post_exc:
            raise self.post_exc
        return self.post_resp


RAW_OK = {
    "id": "resp_123",
    "model": "gpt-6.1-sol",
    "status": "completed",
    "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": '{"a": 1}'}]},
    ],
    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
              "output_tokens_details": {"reasoning_tokens": 3}},
}


class TestClient:
    def test_missing_key(self):
        client = OpenAIAdvisorClient(test_config(api_key=None), transport="http", session=FakeSession())
        with pytest.raises(AdvisorLLMError, match="OPENAI_API_KEY"):
            client.verify_model()

    def test_model_unavailable(self):
        session = FakeSession(get_resp=FakeHTTPResponse(404, {"error": {"message": "nope"}}))
        client = OpenAIAdvisorClient(test_config(), transport="http", session=session)
        with pytest.raises(AdvisorLLMError, match="not available"):
            client.verify_model()
        assert session.gets[0][0].endswith("/models/gpt-6.1-sol")

    def test_timeout(self):
        session = FakeSession(post_exc=requests.Timeout("slow"))
        client = OpenAIAdvisorClient(test_config(), transport="http", session=session)
        with pytest.raises(AdvisorLLMError, match="timed out"):
            client.complete("sys", [{"role": "user", "content": "hi"}], response_json_schema())

    def test_api_error_redacts_key(self):
        session = FakeSession(post_resp=FakeHTTPResponse(500, None, text=f"boom Bearer {FAKE_KEY} {FAKE_KEY}"))
        client = OpenAIAdvisorClient(test_config(), transport="http", session=session)
        with pytest.raises(AdvisorLLMError) as info:
            client.complete("sys", [{"role": "user", "content": "hi"}], response_json_schema())
        assert FAKE_KEY not in str(info.value)
        assert "HTTP 500" in str(info.value)

    def test_request_uses_configured_model_effort_and_schema(self):
        session = FakeSession(post_resp=FakeHTTPResponse(200, RAW_OK))
        cfg = test_config(reasoning_effort="high", max_output_tokens=1234)
        client = OpenAIAdvisorClient(cfg, transport="http", session=session)
        result = client.complete("SYSTEM", [{"role": "user", "content": "hi"}], response_json_schema())
        body = session.posts[0]["body"]
        assert session.posts[0]["url"] == "https://api.openai.test/v1/responses"
        assert session.posts[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
        assert body["model"] == "gpt-6.1-sol"
        assert body["reasoning"] == {"effort": "high"}
        assert body["max_output_tokens"] == 1234
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is True
        assert body["text"]["format"]["schema"] == response_json_schema()
        assert body["input"][0] == {"role": "system", "content": "SYSTEM"}
        assert result.text == '{"a": 1}'
        assert result.usage["reasoning_tokens"] == 3
        assert result.transport == "http"

    def test_invalid_effort(self):
        client = OpenAIAdvisorClient(test_config(reasoning_effort="turbo"), transport="http", session=FakeSession())
        with pytest.raises(AdvisorLLMError, match="REASONING_EFFORT"):
            client.request_params("s", [], {})

    def test_incomplete_response(self):
        raw = dict(RAW_OK, status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        session = FakeSession(post_resp=FakeHTTPResponse(200, raw))
        client = OpenAIAdvisorClient(test_config(), transport="http", session=session)
        with pytest.raises(AdvisorLLMError, match="incomplete"):
            client.complete("s", [], response_json_schema())

    def test_extract_output_text_refusal(self):
        raw = {"output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}
        with pytest.raises(AdvisorLLMError, match="refused"):
            extract_output_text(raw)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_advisor_defaults_and_scan_defaults_unchanged(self):
        import main

        adv = main.parse_args(["advisor"])
        assert (adv.command, adv.min_odds, adv.max_odds, adv.min_edge, adv.top, adv.stake) == (
            "advisor", -600, -200, 0.0, 5, 100.0)
        adv2 = main.parse_args(["--date", "2026-11-01", "advisor", "--no-llm"])
        assert adv2.command == "advisor" and adv2.no_llm and adv2.date == "2026-11-01"
        scan = main.parse_args([])
        assert (scan.min_edge, scan.max_odds, scan.top, scan.min_odds) == (0.05, 400, 10, -600)

    def test_advisor_no_llm_end_to_end(self, monkeypatch, tmp_path):
        import main
        from rich.console import Console

        import engine.advisor.run as run_mod

        monkeypatch.setattr(run_mod, "_scan", lambda d, o: (five_props(), {"kalshi": 3, "polymarket": 2}, 2))
        monkeypatch.setattr(run_mod, "DEFAULT_OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(run_mod.AdvisorOptions, "__init__", _options_init_with_dir(tmp_path))
        console = Console(record=True, width=220, legacy_windows=False)
        monkeypatch.setattr(main, "console", console)
        monkeypatch.setattr("sys.argv", ["main.py", "advisor", "--date", "2026-11-01", "--no-llm"])

        main.main()
        out = console.export_text()
        assert "Top 5 picks" in out
        assert "Alpha One" in out
        assert "PL1" in out
        assert "AI layer: disabled" in out
        assert "Deterministic recommendation" in out
        assert list(tmp_path.glob("2026-11-01_*.json")) and list(tmp_path.glob("2026-11-01_*.md"))


def _options_init_with_dir(path):
    original = AdvisorOptions.__init__

    def init(self, *args, **kwargs):
        kwargs.setdefault("output_dir", path)
        original(self, *args, **kwargs)

    return init
