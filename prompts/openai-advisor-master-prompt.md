# Master prompt: add an OpenAI reasoning layer ("AI Advisor") to True Prob AI

You are working in the `True_Prob_AI` repository (Python 3.11+, NBA player-prop true-probability model). Implement an **AI Advisor** layer: the existing model runs and calculates true probabilities, then the qualifying results are sent to the OpenAI API, which reasons over them and returns the best picks and parlay recommendations. The whole process must work **end to end**. When you finish implementing, you must **test, regression test, then run everything end to end and give me back the real results**.

Read this entire prompt before writing code.

---

## 1. How the existing system works (read these files first)

- `main.py`: CLI entry point. `python main.py` runs a daily scan. Subcommand `backtest` exists. Uses `rich` for output.
- `config.py`: loads `.env` with `python-dotenv` (`load_dotenv` at import). Credentials live in the frozen dataclass `ProviderCredentials`, read through `_key(name)` and exposed by `get_credentials()`.
- `engine/slate_scanner.py`: `SlateScanner().scan(game_date, prop_types=None, is_playoff=False)` returns `list[PropProbability]`. It loads games (BALLDONTLIE) and odds (The Odds API), drops games that have started, warms caches, builds feature vectors, and evaluates every prop.
- `engine/prop_evaluator.py`: turns each projection into a true probability (distribution tail → shrink toward 50% → completeness blend → clamp to [0.07, 0.93] → audit-flag shrink → market calibration → final calibration gate), then `edge = true_probability - implied_probability`. The implied probability is the **raw, vig-included** number from the posted American odds.
- `domain/entities.py`:
  - `PropProbability` fields include `player_id, player_name, team_abbr, opponent_abbr, game_id, prop_type, line, side, projected_value, true_probability, implied_probability, edge, fair_odds, sportsbook_odds, best_book, best_book_key, confidence, explanation, calibration_warnings, expected_minutes`.
  - `Parlay` fields include `legs, combined_american_odds, combined_decimal_odds, combined_implied_probability, combined_true_probability, combined_edge, correlation_risk_score, stake, total_return, net_profit`.
- `engine/parlay_builder.py`: `ParlayConstraints` (min_edge, min_legs, max_legs, min_leg_odds, max_leg_odds, min_confidence, …), `build_parlays(all_props, constraints)`, and `leg_odds_match_constraints(odds, min_odds, max_odds)`. The odds band is order-independent and inclusive, so -600 to -200 means `-600 <= odds <= -200`.
- `engine/correlation_engine.py`: blocks correlated leg combinations (for example the same player twice) and scores correlation risk.
- `odds/parlay_math.py`: `parlay_combined_decimal`, `parlay_combined_american`, `parlay_combined_true_probability_calibrated`, `parlay_payout(stake, decimal)`, `parlay_profit(stake, decimal)`, `parlay_expected_value`.
- `engine/bankroll_engine.py`: `apply_stake`, `apply_stake_to_all`, `suggested_kelly_stake`.
- `engine/ranking_engine.py`: `rank_parlays`.
- `engine/prediction_ledger.py`: saves an append-only pregame snapshot to `data/predictions/<date>.json`.
- `app/streamlit_app.py`: dashboard with tabs Straight Bets, Parlays, Props Analysis, Line Shopping, API Debug, Backtesting.
- `utils/api_debug.py`: captures and redacts raw API responses.
- `tests/`: 19 pytest modules. `domain/constants.py` holds tunable constants (`DEFAULT_MIN_ODDS = -600`, `FAVORITE_STRAIGHT_BET_AUDIT_BAND_LOW/HIGH = -600/-220`).

Reuse these modules. Do not duplicate odds math, parlay math, correlation logic or vig handling. Do not change model weights, calibration constants or the probability pipeline. That is out of scope.

---

## 2. What to build

### 2.1 Pipeline (one command, end to end)

1. **Scan:** run `SlateScanner().scan(game_date)` for the requested date (default: today, Eastern, using `utils/date_utils.today_eastern()`).
2. **Filter candidates** deterministically in Python:
   - `edge > min_edge` (default `0.0`, meaning strictly positive edge; configurable),
   - `leg_odds_match_constraints(prop.sportsbook_odds, min_odds, max_odds)` with defaults **min -600, max -200** (inclusive),
   - optional `min_confidence`,
   - keep one row per (player, prop_type, side), choosing the best-priced line,
   - sort by edge descending, then confidence, then true_probability.
3. **Top N picks:** take the top `N` (default **5**).
4. **Parlays:** from those top N picks, build every valid **2-leg and 3-leg** combination by calling `build_parlays` with `ParlayConstraints(min_legs=2, max_legs=3, min_leg_odds=-600, max_leg_odds=-200, min_edge=<same>)`, so the existing correlation blocking applies. For each parlay, compute with the existing `odds/parlay_math.py` functions:
   - combined American and decimal odds,
   - combined implied probability,
   - calibrated combined true probability,
   - combined edge,
   - **total return and net profit for the stake (default $100)**,
   - expected value: `p * profit - (1 - p) * stake`.
5. **Build the LLM payload:** a compact JSON object with run metadata (date, odds band, min edge, stake, counts), the top-N picks (only the fields listed in 1.2 plus `explanation` and `calibration_warnings`), and the precomputed parlay table. Give every pick and parlay a stable ID (`P1..P5`, `PL1..PLn`).
6. **Call OpenAI** once with a system prompt, the user's master prompt and the JSON payload (section 2.3).
7. **Validate** the response (section 2.4), merge it with the deterministic numbers, and render it.
8. **Save** the full run to `data/advisor/<date>_<HHMMSS>.json` (inputs, LLM request metadata, raw response, validated output) and a readable `data/advisor/<date>_<HHMMSS>.md`.

### 2.2 Hard rules for the LLM layer

- **Python does all the math.** The LLM never calculates or invents probabilities, odds, edges, payouts or stats. It may only reference IDs and numbers that are in the payload.
- **The model's numbers are the source of truth.** The LLM reasons over them: risk, correlation, stacking, why a combination is or isn't sensible, and which 2-leg and 3-leg options it recommends. It does not change any number.
- After the response comes back, recompute the payout and odds for every parlay the LLM recommends, using Python. If the LLM's stated numbers differ, overwrite them and log a warning.
- Reject any pick or parlay ID that isn't in the payload. If the response fails validation, retry once with the validation errors appended. If it fails again, fall back to a deterministic report (top 5 plus the best 2-leg and 3-leg parlays by EV) and say clearly that the AI layer failed.
- If no props qualify, do not call OpenAI. Print a clear "no qualifying props" message with the counts at each filter stage.

### 2.3 OpenAI call

- Add `openai` to `requirements.txt` with a minimum version that supports the Responses API and structured outputs.
- New config, following the existing `config.py` pattern (a frozen dataclass plus a `get_…()` accessor), with values from `.env`:
  - `OPENAI_API_KEY` (required for the advisor only; the rest of the app must still run without it),
  - `OPENAI_MODEL`: the user wants the model they call **"6.1 sol"**. Look up the exact model ID string in OpenAI's current model list, set it as the default, and keep it overridable by env.
  - `OPENAI_REASONING_EFFORT=medium`,
  - `OPENAI_TIMEOUT=120`,
  - `OPENAI_MAX_OUTPUT_TOKENS` (a sensible default).
- At startup, verify the configured model is available to the key (`client.models.retrieve` or equivalent). If it isn't, **fail loudly with a clear message**. Never silently switch to a different model.
- Use the official `openai` Python SDK and the **Responses API** with `reasoning={"effort": <OPENAI_REASONING_EFFORT>}` and **structured output (JSON schema)**. Check the current SDK docs for the exact parameter names instead of guessing.
- Add `OPENAI_API_KEY`, `OPENAI_MODEL` and `OPENAI_REASONING_EFFORT` to `.env.template` and `.env.example` with empty or default values. Never commit a real key, never log it, and make sure `utils/api_debug.py` redaction covers it.
- One OpenAI call per run (plus at most one validation retry). Log token usage and latency.

**Default user master prompt** (overridable with `--prompt` or `--prompt-file`):

> Run the model to find the best positive-edge props with odds between -200 and -600. Give me the top 5 picks. Reason over them to decide whether I should combine them into a 2-leg or 3-leg parlay, which combination is best and why, and how much I get back if I put $100 on it.

**System prompt** (put it in `engine/advisor/prompts.py`, not inline):
- You are a sports betting analyst reviewing outputs from a quantitative NBA prop model.
- Use only the provided JSON. Never invent players, lines, odds, probabilities or payouts.
- Reference picks and parlays by ID.
- Explain the reasoning: edge size, confidence tier, calibration warnings, correlation (same game or same team), how stacking heavy favorites compounds risk, and the trade-off between 2 and 3 legs.
- Recommend at most one 2-leg and one 3-leg parlay, or recommend straight bets only if no parlay is sensible.
- Be concise and direct. Include a one-line responsible-gambling note.

**Response schema** (strict JSON):
```json
{
  "top_picks": [{"id": "P1", "rank": 1, "rationale": "string", "risk_notes": "string"}],
  "parlay_recommendations": [{"id": "PL3", "legs": 2, "rationale": "string", "key_risks": "string"}],
  "straight_bets_only": false,
  "summary": "string",
  "responsible_gambling_note": "string"
}
```

### 2.4 Validation

- Pydantic models (pydantic is already a dependency) for both the payload and the response.
- Every ID must exist. A parlay's leg count must match its table entry. At most 5 picks, and at most one parlay recommendation for each leg count.
- Merge validated LLM text with the deterministic numbers for display.

### 2.5 Interfaces

- **CLI:** add an `advisor` subcommand to `main.py`, following the existing `backtest` subcommand pattern:
  ```
  python main.py advisor [--date YYYY-MM-DD] [--min-odds -600] [--max-odds -200] [--min-edge 0.0]
                         [--top 5] [--stake 100] [--min-confidence ...] [--prompt "..."] [--prompt-file path]
                         [--no-llm]   # deterministic report only, no OpenAI call
  ```
  Output with `rich`: a picks table (player, prop, line, side, book, American odds, true probability, implied probability, edge, confidence), a parlay table (legs, combined odds, true probability, implied probability, edge, $ return, $ profit, EV), then the AI reasoning, then the saved file paths.
- **Streamlit:** add an "🤖 AI Advisor" tab to `app/streamlit_app.py` that reuses the props already scanned in session state, with stake, odds band, top N and a prompt text box, a Run button, and the same tables and reasoning. Show a clear message if `OPENAI_API_KEY` is missing.
- **Code layout:** put the logic in a new `engine/advisor/` package (`candidates.py`, `parlays.py`, `payload.py`, `client.py`, `prompts.py`, `schema.py`, `report.py`, `run.py`). The CLI and Streamlit both call one function, for example `run_advisor(game_date, options) -> AdvisorResult`.

---

## 3. Working rules

- Work on a new git branch. Do not commit `.env`, keys, `data/cache/` churn or generated `data/advisor/` output unless asked. Don't push or open a PR without asking.
- Match the existing code style: type hints, module docstrings, `logging.getLogger(__name__)`, `from __future__ import annotations`.
- Don't refactor unrelated code. If you find a real bug along the way, list it in your final report instead of fixing it silently.
- Never fabricate results. If a key is missing, an API fails or there are no games, stop and report exactly what happened.

---

## 4. Testing: you must do all of this before reporting back

### 4.1 Baseline (before changing code)
1. `pip install -r requirements.txt`
2. `pytest -q`, and record the exact pass/fail/skip counts and any failures that already exist.

### 4.2 New unit tests (`tests/test_advisor_*.py`, no network; mock the OpenAI client)
- **Odds band:** -200 and -600 are included; -199, -601 and +150 are excluded.
- **Edge filter:** edge 0 is excluded and edge > 0 is included at the default; `--min-edge` is honored.
- **Dedupe and ranking:** one row per player/prop/side, and the order is deterministic.
- **Top N:** fewer than N candidates works, and zero candidates skips the LLM.
- **Parlay math:** check against hand-calculated values. For example, two legs at -200 and -300: decimal 1.5 × 1.3333 = 2.0, so $100 returns $200.00 and profits $100.00. Also cover 3 legs, and correlated combinations getting blocked.
- **Payload:** IDs are stable, only whitelisted fields are included, and no secrets appear.
- **Validation:** an unknown ID is rejected, a wrong payout is overwritten by Python, a malformed response triggers exactly one retry and then the deterministic fallback.
- **Client:** a missing key gives a clear error; an unavailable model fails loudly; timeouts and API errors fall back cleanly; the request uses the configured model, reasoning effort and JSON schema.
- **CLI:** `python main.py advisor --no-llm` runs with mocked scan data.

### 4.3 Regression
Run the full `pytest -q` again. Every test that passed at baseline must still pass. Fix anything you broke; don't weaken or skip existing tests. Also confirm `python main.py --help` and `python main.py advisor --help` work, and that the plain `python main.py` scan path is unchanged.

### 4.4 End-to-end (real APIs)
Using the real keys in `.env` (`BALLDONTLIE_API_KEY`, `THE_ODDS_API_KEY`, `OPENAI_API_KEY`):
1. `python main.py advisor --stake 100` for today's slate. If there are no pregame games today, use the next date that has NBA games and say so.
2. `python main.py advisor --no-llm --stake 100` on the same date, to compare.
3. Launch the Streamlit app, run the AI Advisor tab once, and confirm it renders.
4. Check that the saved `data/advisor/*.json` and `.md` files exist and match what was printed.

Mind the Odds API credit usage: don't run the full scan more times than you need to.

---

## 5. What to give me back when you're done

1. **Summary of changes:** files added or modified, and the new env vars.
2. **Test results:** baseline counts, final counts, the new tests added, and any failures that already existed (verbatim).
3. **The end-to-end result, pasted as actual output:**
   - date run, games scanned, props evaluated, and how many qualified at each filter step,
   - the top 5 picks table,
   - the 2-leg and 3-leg parlay options with combined odds and the exact return and profit on $100,
   - the AI reasoning and its recommendations,
   - the model ID and reasoning effort used, token usage, latency,
   - paths to the saved files.
4. **Problems:** anything that failed, was skipped or couldn't be verified, stated plainly.
5. **How to run it:** the exact commands.

The job is done only when the advisor runs end to end on real data and you've shown me that output.
