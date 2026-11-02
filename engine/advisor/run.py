"""
AI Advisor orchestration — the single entry point used by the CLI and Streamlit.

Pipeline:
    scan (or reuse supplied props) → candidate filter → deterministic pick and
    parlay table → whitelisted JSON payload → OpenAI structured reasoning
    (one call + at most one validation retry) → validate / merge → save.

If the LLM layer is disabled, has no key, or fails twice, a deterministic
recommendation (top picks + best 2-leg and 3-leg parlays by EV) is returned
and the failure is reported plainly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

from domain.entities import PropProbability
from engine.advisor.candidates import CandidateStats, select_candidates
from engine.advisor.client import AdvisorLLMError, OpenAIAdvisorClient
from engine.advisor.parlays import ParlayRow, PickRow, best_parlay, build_parlay_rows, build_pick_rows
from engine.advisor.payload import build_payload, payload_to_json
from engine.advisor.prompts import (
    SYSTEM_PROMPT,
    build_retry_message,
    build_user_message,
    default_user_prompt,
)
from engine.advisor.schema import (
    AdvisorResponse,
    AdvisorResponseError,
    parse_response,
    response_json_schema,
    validate_response,
)

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "advisor"

AI_OK = "ok"
AI_DISABLED = "disabled"
AI_SKIPPED = "skipped_no_candidates"
AI_FAILED = "failed"


@dataclass
class AdvisorOptions:
    min_odds: int = -600
    max_odds: int = -200
    min_edge: float = 0.0
    top_n: int = 5
    stake: float = 100.0
    bankroll: Optional[float] = None
    min_confidence: Optional[str] = None
    prompt: Optional[str] = None
    use_llm: bool = True
    save: bool = True
    output_dir: Path = DEFAULT_OUTPUT_DIR
    is_playoff: bool = False


@dataclass
class DeterministicRecommendation:
    top_pick_ids: list[str]
    best_two_leg: Optional[str]
    best_three_leg: Optional[str]
    straight_bets_only: bool
    note: str


@dataclass
class AdvisorResult:
    game_date: date
    options: AdvisorOptions
    stats: CandidateStats
    picks: list[PickRow]
    parlays: list[ParlayRow]
    payload: dict
    user_prompt: str
    deterministic: DeterministicRecommendation
    ai_status: str = AI_DISABLED
    ai_error: str = ""
    ai_response: Optional[AdvisorResponse] = None
    llm_meta: dict[str, Any] = field(default_factory=dict)
    raw_responses: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    odds_source_counts: dict[str, int] = field(default_factory=dict)
    pregame_games: Optional[int] = None
    json_path: Optional[Path] = None
    md_path: Optional[Path] = None

    @property
    def pick_by_id(self) -> dict[str, PickRow]:
        return {p.id: p for p in self.picks}

    @property
    def parlay_by_id(self) -> dict[str, ParlayRow]:
        return {p.id: p for p in self.parlays}


def _deterministic(picks: list[PickRow], parlays: list[ParlayRow]) -> DeterministicRecommendation:
    two = best_parlay(parlays, 2)
    three = best_parlay(parlays, 3)
    positive = [p for p in (two, three) if p is not None and p.expected_value > 0]
    if not picks:
        note = "No props qualified, so there is nothing to recommend."
    elif not parlays:
        note = "Fewer than two non-correlated qualifying legs: straight bets only."
    elif not positive:
        note = "No 2- or 3-leg parlay has positive model EV after calibration: straight bets only."
    else:
        note = "Best parlays chosen by model expected value (EV) at the requested stake."
    return DeterministicRecommendation(
        top_pick_ids=[p.id for p in picks],
        best_two_leg=two.id if two else None,
        best_three_leg=three.id if three else None,
        straight_bets_only=not positive,
        note=note,
    )


def _allowed_amounts(
    picks: list[PickRow], parlays: list[ParlayRow], stake: float, bankroll: Optional[float] = None,
) -> set[float]:
    amounts = {round(stake, 2)}
    if bankroll:
        amounts.add(round(bankroll, 2))
    for row in [*picks, *parlays]:
        amounts.update({
            round(row.total_return, 2), round(row.profit, 2), round(row.expected_value, 2),
            round(abs(row.expected_value), 2),
        })
        kelly = row.kelly_stake(bankroll)
        if kelly:
            amounts.add(kelly)
    return amounts


def _scan(game_date: date, options: AdvisorOptions) -> tuple[list[PropProbability], dict, Optional[int]]:
    from engine.slate_scanner import SlateScanner

    scanner = SlateScanner()
    props = scanner.scan(game_date, is_playoff=options.is_playoff)
    counts = dict(getattr(scanner, "last_odds_source_counts", {}) or {})
    pregame = getattr(scanner, "last_pregame_slate_game_count", None)
    return props, counts, pregame


def _run_llm(result: AdvisorResult, client: OpenAIAdvisorClient) -> None:
    schema = response_json_schema()
    user_message = build_user_message(result.user_prompt, payload_to_json(result.payload))
    messages: list[dict] = [{"role": "user", "content": user_message}]
    pick_ids = [p.id for p in result.picks]
    parlay_legs = {p.id: list(p.leg_ids) for p in result.parlays}
    parlay_numbers = {
        p.id: {"total_return": p.total_return, "profit": p.profit} for p in result.parlays
    }
    allowed = _allowed_amounts(result.picks, result.parlays, result.options.stake, result.options.bankroll)

    cfg = client.config
    meta: dict[str, Any] = {
        "model": cfg.model,
        "reasoning_effort": cfg.reasoning_effort,
        "max_output_tokens": cfg.max_output_tokens,
        "timeout_s": cfg.timeout,
        "transport": client.transport,
        "attempts": 0,
        "latency_s": 0.0,
        "usage": {},
        "response_ids": [],
        "errors": [],
    }
    result.llm_meta = meta

    try:
        client.verify_model()
    except AdvisorLLMError as exc:
        result.ai_status = AI_FAILED
        result.ai_error = str(exc)
        meta["errors"].append(str(exc))
        logger.error("AI Advisor LLM unavailable: %s", exc)
        return

    for attempt in (1, 2):
        meta["attempts"] = attempt
        try:
            llm = client.complete(SYSTEM_PROMPT, messages, schema)
        except AdvisorLLMError as exc:
            result.ai_status = AI_FAILED
            result.ai_error = str(exc)
            meta["errors"].append(str(exc))
            logger.error("AI Advisor call failed: %s", exc)
            return

        meta["model"] = llm.model or meta["model"]
        meta["latency_s"] = round(meta["latency_s"] + llm.latency_s, 3)
        meta["response_ids"].append(llm.response_id)
        for k, v in llm.usage.items():
            meta["usage"][k] = meta["usage"].get(k, 0) + v
        result.raw_responses.append({"attempt": attempt, "text": llm.text, "status": llm.status})

        try:
            parsed = parse_response(llm.text)
            validated, warnings = validate_response(
                parsed,
                pick_ids=pick_ids,
                parlay_legs=parlay_legs,
                parlay_numbers=parlay_numbers,
                allowed_amounts=allowed,
            )
        except AdvisorResponseError as exc:
            meta["errors"].append(f"attempt {attempt}: {exc}")
            logger.warning("AI Advisor response invalid (attempt %d): %s", attempt, exc)
            if attempt == 1:
                messages = [
                    *messages,
                    {"role": "assistant", "content": llm.text or "(empty)"},
                    {"role": "user", "content": build_retry_message(str(exc))},
                ]
                continue
            result.ai_status = AI_FAILED
            result.ai_error = f"Model output failed validation twice: {exc}"
            return

        result.ai_response = validated
        result.warnings.extend(warnings)
        for rec in validated.parlay_recommendations:
            row = result.parlay_by_id[rec.id]
            if row.expected_value <= 0:
                result.warnings.append(
                    f"AI recommended {rec.id} but its model EV is {row.expected_value:+.2f} (not positive)."
                )
            if not row.single_venue:
                result.warnings.append(
                    f"{rec.id} mixes venues ({', '.join(sorted(set(row.venues)))}); it cannot be one ticket."
                )
        result.ai_status = AI_OK
        result.ai_error = ""
        return


def run_advisor(
    game_date: Optional[date] = None,
    options: Optional[AdvisorOptions] = None,
    *,
    props: Optional[list[PropProbability]] = None,
    odds_source_counts: Optional[dict[str, int]] = None,
    client: Optional[OpenAIAdvisorClient] = None,
) -> AdvisorResult:
    """
    Run the full advisor pipeline and return an :class:`AdvisorResult`.

    Pass *props* to reuse an existing scan (Streamlit, tests); otherwise the
    slate for *game_date* (default: today, US/Eastern) is scanned.
    """
    from utils.date_utils import today_eastern

    options = options or AdvisorOptions()
    game_date = game_date or today_eastern()

    pregame: Optional[int] = None
    if props is None:
        props, scanned_counts, pregame = _scan(game_date, options)
        odds_source_counts = odds_source_counts or scanned_counts

    top, _ranked, stats = select_candidates(
        props,
        min_odds=options.min_odds,
        max_odds=options.max_odds,
        min_edge=options.min_edge,
        min_confidence=options.min_confidence,
        top_n=options.top_n,
    )
    picks = build_pick_rows(top, options.stake)
    parlays = build_parlay_rows(
        picks,
        stake=options.stake,
        min_odds=options.min_odds,
        max_odds=options.max_odds,
        min_edge=options.min_edge,
    )
    payload = build_payload(
        game_date=game_date,
        picks=picks,
        parlays=parlays,
        stats=stats,
        min_odds=options.min_odds,
        max_odds=options.max_odds,
        min_edge=options.min_edge,
        stake=options.stake,
        odds_source_counts=odds_source_counts,
        bankroll=options.bankroll,
    )
    user_prompt = (options.prompt or "").strip() or default_user_prompt(
        min_odds=options.min_odds, max_odds=options.max_odds,
        top_n=options.top_n, stake=options.stake,
    )

    result = AdvisorResult(
        game_date=game_date,
        options=options,
        stats=stats,
        picks=picks,
        parlays=parlays,
        payload=payload,
        user_prompt=user_prompt,
        deterministic=_deterministic(picks, parlays),
        odds_source_counts=dict(odds_source_counts or {}),
        pregame_games=pregame,
    )

    if not picks:
        result.ai_status = AI_SKIPPED
        result.ai_error = "No props passed the filters; the LLM was not called."
    elif not options.use_llm:
        result.ai_status = AI_DISABLED
    else:
        _run_llm(result, client or OpenAIAdvisorClient())

    if options.save:
        from engine.advisor.report import save_result

        try:
            save_result(result, options.output_dir)
        except OSError as exc:
            logger.error("Could not save advisor output: %s", exc)
            result.warnings.append(f"Could not save advisor output: {exc}")

    return result
