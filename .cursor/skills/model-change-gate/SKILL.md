---
name: model-change-gate
description: Use before changing anything that moves a probability customers see (blend weights, de-vig method, market corrections, calibration, projections that feed the board), and before writing any claim about accuracy, edge or returns.
---
# Model-change gate

The product sells a transparent, margin-free market consensus, not a proven edge. The market
price is the default. It changes only on out-of-sample evidence and with Michael's sign-off.

## Rules

1. Every weight in `data/models/market_blend.json` (`weights` and `default`) stays `0.0` until
   this gate passes. Read its `live_override` note for why.
2. Evidence is walk-forward only: anything fitted is fitted on dates before the date it is scored on.
3. Bar: mean per-date Brier change against the market is better by **more than 2 date-clustered
   standard errors**. Testing several variants at once? Use a multiple-test bar (Bonferroni) or
   pre-register one variant.
4. Pre-register the test (method, data, dates, bar) in today's `docs/daily/YYYY-MM-DD.md`
   before looking at results. No choosing dates, stats or venues after seeing them.
5. Never edit past files in `data/predictions/` (see `grading-workflow`). No paid API calls to
   get more data (see `no-paid-calls`).

## Steps

1. Write the hypothesis and the pre-registered bar in the daily log.
2. Build it as an offline module under `engine/` that writes a report to `data/backtests/`.
   Follow `engine/market_slope.py` and `engine/devig_compare.py`
   (`python -m engine.market_slope`, `python -m engine.market_slope --by-prop`,
   `python -m engine.devig_compare`). Nothing on the board or `/performance` may read the report.
3. Add tests (pattern: `tests/test_market_slope.py`, `tests/test_devig_compare.py`).
4. Report every row: lines, dates, Brier and log loss for each method, mean per-date change ± SE,
   dates better, pass/fail at 2 SE and at the multiple-test bar.
5. Fail or borderline: ship as offline only, say so plainly, and add a pre-registered retest on
   future graded dates to the carry-overs.
6. Pass: still do not switch it on. List it under "Needs owner" with the evidence. Michael decides.
   Once approved, ship it in its own commit with tests, keep the old path, and keep the model weight
   shown on every line.

## Never

- Say "edge", "beats the books", "sharp", or quote a win rate or ROI as a selling point.
- Promote a fitted weight because a backtest looked good; live graded boards are what count.
- Rerun a failed test on the same dates with tweaks until it passes.
