"""
Prompt text for the AI Advisor reasoning layer.

The LLM never computes numbers: every probability, price, payout and EV is
produced in Python and passed in as JSON. The model only ranks, explains and
chooses among IDs that already exist in that JSON.
"""

from __future__ import annotations

SYSTEM_PROMPT = """You are a disciplined NBA player-prop betting analyst working on top of a quantitative true-probability model.

Rules you must follow:
1. Use ONLY the JSON data provided in the user message. Do not invent players, lines, odds, injuries, news or statistics.
2. Never compute or restate your own probabilities, odds, payouts or expected values. Every number already exists in the JSON; refer to picks and parlays only by their IDs (P1, P2, ... and PL1, PL2, ...).
3. Every ID you return must exist in the JSON. Parlay legs must exactly match the legs listed for that parlay ID.
4. For each top pick explain, in one or two sentences, why it ranks where it does: edge versus the venue price, how far the statistical model moved the market-anchored probability (model_weight 0 means the price is pure market consensus and any edge comes from venue price differences), confidence tier, projected value vs. the line, calibration warnings, minutes risk and injury news in model_notes.
5. For parlays, weigh correlation risk (same game / same team legs), the compounding risk of stacking heavy favorites (each leg can lose; the combined hit rate falls fast), single-venue availability (legs priced on Kalshi and Polymarket cannot be combined into one ticket), and the trade-off between a 2-leg and a 3-leg parlay (higher payout vs. lower hit rate and EV).
6. Recommend at most one 2-leg parlay and at most one 3-leg parlay. If no parlay has positive expected value or the risks outweigh the edge, set straight_bets_only to true and return no parlays.
7. Be concise and specific. No hype, no guarantees.
8. Finish with a one-line responsible-gambling note."""


DEFAULT_USER_PROMPT_TEMPLATE = (
    "Run the model to find the best positive-edge props with odds between {max_odds} and {min_odds}. "
    "Give me the top {top_n} picks. Reason over them to decide whether I should combine them into a "
    "2-leg or 3-leg parlay, which combination is best and why, and how much I get back if I put "
    "{stake} on it."
)


def default_user_prompt(*, min_odds: int, max_odds: int, top_n: int, stake: float) -> str:
    """Return the default user question, matching the requested odds band and stake."""
    stake_txt = f"${stake:,.0f}" if float(stake).is_integer() else f"${stake:,.2f}"
    return DEFAULT_USER_PROMPT_TEMPLATE.format(
        min_odds=min(min_odds, max_odds),
        max_odds=max(min_odds, max_odds),
        top_n=top_n,
        stake=stake_txt,
    )


def build_user_message(user_prompt: str, payload_json: str) -> str:
    """Combine the user's question with the deterministic JSON payload."""
    return (
        f"{user_prompt.strip()}\n\n"
        "All numbers below were computed by the model in Python. Payout and profit figures in the "
        "JSON are authoritative; reference picks and parlays by ID only.\n\n"
        f"DATA (JSON):\n{payload_json}"
    )


def build_retry_message(error: str) -> str:
    """Instruction appended for the single validation retry."""
    return (
        "Your previous answer failed validation and was discarded:\n"
        f"{error}\n\n"
        "Answer again using only IDs that exist in the JSON, with parlay legs that exactly match the "
        "listed legs for that parlay ID, at most 5 top picks, and at most one parlay per leg count."
    )
