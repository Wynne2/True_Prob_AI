---
name: daily-improvement-run
description: Use for the scheduled daily improvement run (6 AM America/New_York) on this repo, or whenever asked to "do the daily run". This file is the single source of truth for that routine.
---
# Daily improvement run

Goal: ship 10 small, tested improvements to `main` that make the product more useful,
trustworthy or sellable to a paying customer, and leave an honest log in
`docs/daily/YYYY-MM-DD.md` (date in America/New_York).

## Hard rules (break one and the run has failed)

1. **Honesty.** Report what happened, including failures, skipped items and things not verified.
   No invented sources, numbers or test results.
2. **Model.** Blend weights stay 0 unless walk-forward beats the market by more than 2
   date-clustered SEs and Michael approves. Follow `.cursor/skills/model-change-gate/SKILL.md`.
3. **Graded data.** Never edit past files in `data/predictions/`. Follow `grading-workflow`.
4. **Money.** No paid API calls (`no-paid-calls`). No real payments or live Stripe (`billing-credits-safety`).
5. **Secrets.** Never commit `.env`, keys, Stripe IDs, `data/app.db*`, `data/boards/`,
   `data/_local_backup/`, `data/outbox.log`, `data/.secret_key`.
6. **Compliance.** 21+ and 1-800-GAMBLER stay on every page and email; legal pages stay drafts;
   no edge or profit claims. Follow `compliance-copy`.
7. **Git.** Work on `main`, `git pull --ff-only` first, push straight to `origin main`.
   Never `--no-verify`, never force-push, never rewrite history or amend pushed commits.
8. **Owner decisions.** Prices and plans, legal text and blocked states, provider permissions,
   production settings, turning on a model change: write them under "Needs owner"; do not do them.

## Steps

1. **Start.** `git pull --ff-only origin main`; `git config core.hooksPath .githooks`; set up
   `.venv` (see `release-check`). Run the offline suite and record the "before" count.
2. **Read.** The last two files in `docs/daily/` (carry-overs, needs-owner, what was already tried),
   `docs/LAUNCH.md`, and the newest `data/predictions/*.graded.json`. Do not repeat shipped items;
   retry carry-overs first when they are still unblocked.
3. **Research (read-only).** Look at what customers of similar tools ask for or complain about
   (forums, docs, competitor pages, standards such as OWASP or Google Search docs). Never sign up,
   post, buy or submit anything. Keep a link for every idea you use.
4. **Choose 10 ideas** across **at least 5 areas** (e.g. credits, pricing, conversion, onboarding,
   UI/mobile, security, reliability/tests, AI Advisor, arbitrage, SEO, performance page, model
   experiment, docs). Rank by value to a paying customer divided by risk. At most one model item,
   and it is offline unless the gate passes.
5. **Build each item** as its own small change with tests. UI changes get the browser check
   (1280 px and 375 px, light and dark; see `release-check`). One commit per item:

   ```
   <Area>: <what changed for the customer>

   <why, with source links; what was tested>

   Co-Authored-By: <your model name> <noreply@anthropic.com>
   ```

6. **If an item goes wrong**, revert it with a new commit or drop it, and log it as skipped.
   Fewer honest items beat ten shaky ones.
7. **Finish.** Full offline suite again ("after" count), write the daily log (template below),
   commit it, then `git pull --ff-only origin main && git push origin main` and let the pre-push
   hook run. Confirm the push landed (`git status` shows up to date with `origin/main`).

## Daily log template: `docs/daily/YYYY-MM-DD.md`

```markdown
# Daily improvement run: YYYY-MM-DD (America/New_York)

Started from `main` at `<sha>`. Setup notes (one or two lines).
Read before starting: <files>. Newest graded date: <date>; new grading since yesterday: yes/no.

## Tests
- Before: N passed, M deselected (`pytest -q -p no:cacheprovider -m "not live"`).
- After: N passed, M deselected. Pre-push hook: passed.

## Shipped
| # | Area | Idea | Status | Commit |
|---|---|---|---|---|

### Sources
1. <item>: [title](link), ...

## Model
Blend weights still 0 (or the approved change). Any experiment: pre-registered bar, full result
table, pass/fail. Board and /performance changed: yes/no.

## Verification
What was checked and how (tests, browser pages and widths, themes). What was NOT verified.

## Skipped or cut down
Each idea not shipped, and why.

## Needs owner
Decisions or credentials only Michael can give. Carry unresolved ones forward.

## Carry-overs
Numbered list for tomorrow's run.
```
