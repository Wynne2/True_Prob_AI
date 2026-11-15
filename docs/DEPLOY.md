# Deploying the True Prob AI web app

The customer app is `web/` (Starlette, server-rendered, SQLite). The research tools (`main.py`,
the Streamlit dashboard in `app/`) are unchanged and are not exposed to customers.

## Run locally

```bash
pip install -r requirements.txt
uvicorn web.app:app --port 8000
```

Without SMTP settings, emails are written to `data/outbox.log`, and the app shows a "Development
mode: confirm now" link so you can verify accounts. Without Stripe settings, pricing says
"Payments open soon" and checkout is disabled.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `APP_ENV=production` | yes | Secure cookies, HSTS, refuses to start without `SECRET_KEY`, hides dev links |
| `SECRET_KEY` | yes | Signs session cookies. 48+ random characters; never reuse or commit |
| `BASE_URL` | yes | Public origin, e.g. `https://trueprob.ai`, used in email links and Stripe redirects |
| `TRUEPROB_DB` | yes | Path to the SQLite file on a persistent disk, e.g. `/var/data/app.db` |
| `ADMIN_EMAILS` | yes | Comma-separated emails that become admins when they sign in |
| `SUPPORT_EMAIL` | yes | Shown on the contact page and in emails |
| `STRIPE_SECRET_KEY` | for payments | `sk_test_...` for testing, `sk_live_...` only after Stripe approves the business |
| `STRIPE_WEBHOOK_SECRET` | for payments | Signing secret of the webhook endpoint below |
| `STRIPE_PRICE_PRO`, `STRIPE_PRICE_POWER` | for payments | Monthly recurring price ids ($29 and $79) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` | for email | Any SMTP provider (Postmark, SES, Resend) |
| `OPENAI_API_KEY` | for AI Advisor | Advisor is hidden without it |
| `BALLDONTLIE_API_KEY` | for boards | Player statistics used by the model |
| `THE_ODDS_API_KEY` | optional | Sportsbook prices; costs credits per scan. Leave empty to use only Kalshi and Polymarket |
| `BLOCKED_REGIONS` | after legal review | Comma-separated regions refused at sign-up, e.g. `Washington,Outside the United States` |
| `FEATURE_SCAN`, `FEATURE_ARBITRAGE`, `FEATURE_ADVISOR` | optional | `0` switches a feature off; admins can also switch them in the admin page |

## Stripe setup (test mode first)

1. In the Stripe dashboard (test mode) create a product "True Prob AI" with two monthly prices: $29 (Pro) and $79 (Power).
2. Add a webhook endpoint `https://YOUR_DOMAIN/stripe/webhook` with these events:
   `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`,
   `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`.
3. Turn on the customer portal (Settings > Billing > Customer portal): allow cancelling at period end,
   switching between the two prices, updating payment methods and viewing invoices.
4. Set the five Stripe variables, restart, and run the journey in "Before launch" below with card `4242 4242 4242 4242`.
5. Local webhook testing: `stripe listen --forward-to localhost:8000/stripe/webhook` and use the printed `whsec_...`.

Stripe lists "sports forecasting or odds making with a monetary or material prize" as prohibited and
handles gambling-adjacent businesses case by case. Describe the product to Stripe exactly as it is
(a subscription research tool that takes no wagers, holds no funds and awards no prizes) and get
written confirmation before switching to live keys.

## Hosting (one web instance)

Any host with a persistent disk works. Example (Render):

- Web service, start command `uvicorn web.app:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`
- Persistent disk mounted at `/var/data`, `TRUEPROB_DB=/var/data/app.db`
- Health check path `/healthz`
- `render.yaml` in the repo root has these settings.

Run exactly **one** instance: credits, rate limits and scan jobs are coordinated inside one process
and one SQLite file. Moving to Postgres and a job queue is the step before scaling out.

## Backups and recovery

SQLite in WAL mode can be copied safely with its online backup API:

```bash
python -c "import sqlite3; s=sqlite3.connect('/var/data/app.db'); d=sqlite3.connect('/var/data/backup.db'); s.backup(d)"
```

Run it daily (cron) and copy the file off the host. To restore, stop the app, replace `app.db` with
the backup, start the app. Billing state re-syncs from Stripe on the next webhook for each customer.

## Monitoring

- `/healthz` returns database status and running/failed jobs.
- Errors are logged with stack traces (uvicorn's log). Point the host's log alerts at `ERROR` lines.
- The admin page shows failed scans, failed payments, revenue and the audit log.
