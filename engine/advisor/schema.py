"""
Response schema and validation for the AI Advisor.

The JSON schema is sent to OpenAI as a strict structured-output format. The
pydantic models re-validate the reply and, combined with
:func:`validate_response`, enforce that the model only references IDs that
exist in the payload, never mis-states parlay legs, and never overrides the
Python-computed payout numbers.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

MAX_TOP_PICKS = 5


class AdvisorResponseError(ValueError):
    """Raised when the LLM reply is malformed or references unknown data."""


class TopPick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    rank: int = Field(ge=1)
    rationale: str
    risk_notes: str


class ParlayRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    legs: list[str]
    rationale: str
    key_risks: str
    # Optional echo of the payout; always overwritten with the Python numbers.
    total_return: Optional[float] = None
    profit: Optional[float] = None


class AdvisorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_picks: list[TopPick] = Field(max_length=MAX_TOP_PICKS)
    parlay_recommendations: list[ParlayRecommendation]
    straight_bets_only: bool
    summary: str
    responsible_gambling_note: str


def response_json_schema() -> dict:
    """Strict JSON schema for the Responses API ``text.format`` parameter."""
    nullable_number = {"type": ["number", "null"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "top_picks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string", "description": "Pick ID from the payload, e.g. P1"},
                        "rank": {"type": "integer", "description": "1 = strongest pick"},
                        "rationale": {"type": "string"},
                        "risk_notes": {"type": "string"},
                    },
                    "required": ["id", "rank", "rationale", "risk_notes"],
                },
            },
            "parlay_recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string", "description": "Parlay ID from the payload, e.g. PL1"},
                        "legs": {"type": "array", "items": {"type": "string"}},
                        "rationale": {"type": "string"},
                        "key_risks": {"type": "string"},
                        "total_return": nullable_number,
                        "profit": nullable_number,
                    },
                    "required": ["id", "legs", "rationale", "key_risks", "total_return", "profit"],
                },
            },
            "straight_bets_only": {"type": "boolean"},
            "summary": {"type": "string"},
            "responsible_gambling_note": {"type": "string"},
        },
        "required": [
            "top_picks",
            "parlay_recommendations",
            "straight_bets_only",
            "summary",
            "responsible_gambling_note",
        ],
    }


def parse_response(text: str) -> AdvisorResponse:
    """Parse raw model text into an :class:`AdvisorResponse` (raises AdvisorResponseError)."""
    if not text or not text.strip():
        raise AdvisorResponseError("Empty response from model")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AdvisorResponseError(f"Response is not valid JSON: {exc}") from exc
    try:
        return AdvisorResponse.model_validate(data)
    except ValidationError as exc:
        raise AdvisorResponseError(f"Response does not match schema: {exc}") from exc


_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)")


def _unverified_amounts(text: str, allowed: set[float]) -> list[str]:
    found = []
    for match in _MONEY.finditer(text or ""):
        value = float(match.group(1).replace(",", ""))
        if not any(abs(value - a) <= 0.51 for a in allowed):
            found.append(match.group(0))
    return found


def validate_response(
    response: AdvisorResponse,
    *,
    pick_ids: list[str],
    parlay_legs: dict[str, list[str]],
    parlay_numbers: dict[str, dict[str, float]],
    allowed_amounts: set[float] | None = None,
) -> tuple[AdvisorResponse, list[str]]:
    """
    Enforce ID integrity and overwrite LLM-stated numbers.

    Returns the corrected response and a list of warnings. Raises
    :class:`AdvisorResponseError` for unknown IDs, mismatched legs, duplicate
    picks, too many parlays per leg count, or contradictory flags.
    """
    warnings: list[str] = []
    valid_picks = set(pick_ids)

    seen: set[str] = set()
    for pick in response.top_picks:
        if pick.id not in valid_picks:
            raise AdvisorResponseError(f"Unknown pick ID {pick.id!r}; valid IDs: {sorted(valid_picks)}")
        if pick.id in seen:
            raise AdvisorResponseError(f"Duplicate pick ID {pick.id!r}")
        seen.add(pick.id)
    if len(response.top_picks) > min(MAX_TOP_PICKS, len(pick_ids)):
        raise AdvisorResponseError(f"Too many top picks ({len(response.top_picks)})")

    per_leg_count: dict[int, str] = {}
    for rec in response.parlay_recommendations:
        if rec.id not in parlay_legs:
            raise AdvisorResponseError(f"Unknown parlay ID {rec.id!r}")
        expected = parlay_legs[rec.id]
        if sorted(rec.legs) != sorted(expected):
            raise AdvisorResponseError(
                f"Parlay {rec.id} legs {rec.legs} do not match the table legs {expected}"
            )
        n = len(expected)
        if n in per_leg_count:
            raise AdvisorResponseError(
                f"More than one {n}-leg parlay recommended ({per_leg_count[n]}, {rec.id})"
            )
        per_leg_count[n] = rec.id

        truth = parlay_numbers[rec.id]
        for name in ("total_return", "profit"):
            stated = getattr(rec, name)
            actual = round(truth[name], 2)
            if stated is not None and abs(float(stated) - actual) > 0.01:
                msg = f"{rec.id} {name}: model said {stated}, overwritten with computed {actual:.2f}"
                logger.warning(msg)
                warnings.append(msg)
            setattr(rec, name, actual)
        rec.legs = list(expected)

    if response.straight_bets_only and response.parlay_recommendations:
        raise AdvisorResponseError("straight_bets_only is true but parlays were recommended")

    response.top_picks.sort(key=lambda p: p.rank)
    for i, pick in enumerate(response.top_picks, start=1):
        pick.rank = i

    if allowed_amounts:
        texts = [response.summary]
        texts += [p.rationale + " " + p.risk_notes for p in response.top_picks]
        texts += [r.rationale + " " + r.key_risks for r in response.parlay_recommendations]
        stray = sorted({amt for t in texts for amt in _unverified_amounts(t, allowed_amounts)})
        if stray:
            msg = (
                "AI text mentions dollar amounts not in the computed table "
                f"({', '.join(stray)}); use the table numbers."
            )
            logger.warning(msg)
            warnings.append(msg)

    return response, warnings
