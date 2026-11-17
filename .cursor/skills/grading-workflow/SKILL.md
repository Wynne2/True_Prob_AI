---
name: grading-workflow
description: Use when recording closing prices, grading a date's saved predictions, rerunning model experiments on newly graded dates, or touching anything in data/predictions/.
---
# Grading workflow

The graded files are the product's track record on `/performance`. They must stay untouched.

## Files (in `data/predictions/`)

- `<date>.json`: the pregame ledger written by a live scan (`engine/prediction_ledger.py`).
- `<date>.closing.json`: closing prices recorded before tip-off.
- `<date>.graded.json`: results after the games finish.

## Hard rules

1. **Never edit, rewrite, regenerate or delete a past ledger, closing or graded file**, even to
   fix a known problem (for example the leftover fixture rows in the Nov 4–6 ledgers; the grader
   skips them). Fix the code going forward and write the problem down in the daily log.
2. Tests must never write to `data/predictions/`. `tests/conftest.py` redirects the ledger to a
   temp folder; keep it that way. After running the suite, `git status data/predictions` must be clean.
3. Grading needs paid data: box scores from BALLDONTLIE and closing prices from the data loaders.
   **Agents do not run it** (see `no-paid-calls`). Michael (or Grok Bot with his approval) runs it.

## Commands (owner or approved run only)

```bash
python main.py clv --date YYYY-MM-DD   # before tip-off: records closing prices
python main.py clv --date YYYY-MM-DD   # after games finish: writes <date>.graded.json
```

If no games are final, it says so and skips grading; run it again later.

## After a new date is graded (offline, free; agents may do this)

```bash
python -m engine.market_slope
python -m engine.market_slope --by-prop
python -m engine.devig_compare
```

Report results under the `model-change-gate` rules. Commit the new graded file and reports only
if they came from an approved grading run; never hand-edit them.

## In the daily log

State the newest graded date, whether anything new was graded since yesterday, and, if not,
add "grade <dates> when box scores are available" to carry-overs and "Needs owner".
