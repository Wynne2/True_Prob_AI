---
name: release-check
description: Use before every push to main, and after any change to web/ pages, templates, CSS or JavaScript, to run the tests, the browser check and the commit hygiene checks.
---
# Release check

## 1. Setup (once per clone)

```bash
git config core.hooksPath .githooks          # pre-push hook runs the offline suite
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # .venv is gitignored
```

If `venv` fails with "ensurepip is not available", install `python3.12-venv` first
(`sudo apt-get update && sudo apt-get install -y python3.12-venv`; on a fresh VM the install fails without the update).

## 2. Tests

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider -m "not live"
```

Record the pass count before and after your work. Every change gets a test. Never skip, delete
or loosen a test to get green; fix the code. Live tests (`-m live`) call real APIs and are not run.

## 3. Browser check (any UI change)

```bash
FEATURE_SCAN=0 TRUEPROB_DB=/tmp/trueprob-check.db .venv/bin/uvicorn web.app:app --port 8000
```

- `FEATURE_SCAN=0` stops data-provider calls; `TRUEPROB_DB` keeps the real `data/app.db` untouched.
- Use Playwright with Chromium (install it into `.venv`; it is not in `requirements.txt`).
- Every changed page at **1280 px and 375 px**, with the OS colour scheme set to **light and dark**.
- Pass: 0 px horizontal overflow, the new text is present, no console errors, server log has no 5xx.
  A 4xx you trigger on purpose (a refused sign-up, a 429) always logs Chrome's "Failed to load resource";
  that one is expected, so list it in the log instead of counting it as a failure.
- Delete any synthetic boards or files you created afterwards. Running the app without `SECRET_KEY` writes
  `data/.secret_key`, and a sign-up without SMTP writes `data/outbox.log`; both are gitignored, delete them too.
  (The test suite sets its own `SECRET_KEY` and redirects the outbox, so it writes neither.)

## 4. Before committing

```bash
git status --short
```

Never stage: `.env`, `data/app.db*`, `data/boards/`, `data/_local_backup/`, `data/outbox.log`,
`data/.secret_key`, `.venv/`, or any changed past file in `data/predictions/`. Stage files by
name; do not use `git add -A` blindly. No secrets, keys or Stripe IDs in code, docs or tests.

## 5. Push

```bash
git pull --ff-only origin main && git push origin main
```

- The pre-push hook must run and pass. **Never `--no-verify`, never force-push, never rewrite history.**
- If the hook fails, fix it and make a new commit.
- CI (`.github/workflows/tests.yml`) runs the same suite on `main`.
