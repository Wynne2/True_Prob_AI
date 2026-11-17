---
name: no-paid-calls
description: Use before running scans, the AI Advisor, grading, live tests, or any code that may reach an external data or AI provider, and when writing tests for provider code.
---
# No paid calls

Agents work offline. Paid providers cost Michael money per call and can burn a monthly quota.

## Paid (never call)

| Provider | Key | Reached by |
|---|---|---|
| BALLDONTLIE | `BALLDONTLIE_API_KEY` | scans, `python main.py clv`, `python main.py backtest`, `data/builders/` |
| The Odds API | `THE_ODDS_API_KEY` | scans, closing prices |
| OpenAI | `OPENAI_API_KEY` | AI Advisor (`python main.py advisor`, web Advisor), injury-news features |
| Stripe | `STRIPE_SECRET_KEY` | checkout, portal, subscription reads (see `billing-credits-safety`) |
| SMTP | `SMTP_HOST` | outgoing email (unset = written to `data/outbox.log`) |

Kalshi and Polymarket public market data need no key, but they rate-limit and their data terms
are unresolved; only touch them for a specific, small, read-only check.

## Rules

1. Do not run `python main.py scan`, `backtest`, `advisor` or `clv`, and do not run `pytest -m live`.
2. Run the web app with `FEATURE_SCAN=0` (see `release-check`); never set real keys locally.
3. Tests must not touch the network: inject fakes or monkeypatch, as `tests/conftest.py` does
   (`AI_NEWS_FEATURES=0`) and `tests/test_web.py` does for Stripe.
4. Use data already in the repo: graded files in `data/predictions/`, `data/backtests/`,
   `data/cache/balldontlie_history/`, `data/cache/odds_history/`.
5. If a task truly needs fresh paid data (grading, a live scan), stop and put it under "Needs owner".
6. Never print, log or commit a key. `.env` is gitignored; keep it that way.
