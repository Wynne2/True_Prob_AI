# Agent guide: True Prob AI

NBA player-prop research SaaS: a Starlette web app (`web/`) selling credits (Free, Pro, Power)
on top of a market-anchored probability engine (`engine/`). It sells transparency, not a proven
edge (`docs/LAUNCH.md`).

## Hard rules

1. Be honest in code, copy and reports. Say what failed or was not verified.
2. Blend weights in `data/models/market_blend.json` stay 0 unless walk-forward beats the market by
   more than 2 date-clustered SEs and Michael approves.
3. Never edit past files in `data/predictions/`.
4. No paid API calls (BALLDONTLIE, The Odds API, OpenAI), no live Stripe, no real payments.
5. Never commit secrets, `.env`, `data/app.db*`, `data/boards/`, `data/_local_backup/`.
6. 21+ and 1-800-GAMBLER on every page and email; legal pages stay drafts until a lawyer reviews them.
7. Run the offline tests before pushing; never `--no-verify`, never force-push, never rewrite history.
8. Prices, legal text, blocked states, provider permissions and production settings are Michael's call.

## Skills (read the one that matches your task)

| Skill | Use when |
|---|---|
| `.cursor/skills/daily-improvement-run/SKILL.md` | The scheduled daily run (single source of truth) |
| `.cursor/skills/model-change-gate/SKILL.md` | Any change to shown probabilities or accuracy claims |
| `.cursor/skills/grading-workflow/SKILL.md` | Closing prices, grading, `data/predictions/` |
| `.cursor/skills/compliance-copy/SKILL.md` | Any customer-facing words or legal pages |
| `.cursor/skills/release-check/SKILL.md` | Before every push; any UI change |
| `.cursor/skills/billing-credits-safety/SKILL.md` | Plans, credits, refunds, Stripe |
| `.cursor/skills/no-paid-calls/SKILL.md` | Anything that might reach an external provider |

Coding style: `.agents/skills/ponytail/SKILL.md` (smallest complete change).

## Quick commands

```bash
git config core.hooksPath .githooks
.venv/bin/python -m pytest -q -p no:cacheprovider -m "not live"
FEATURE_SCAN=0 TRUEPROB_DB=/tmp/trueprob-check.db .venv/bin/uvicorn web.app:app --port 8000
```
