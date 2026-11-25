# True Prob AI: launch readiness

Status as of 2026-11-25 (first written 2026-11-15; daily changes are in `docs/daily/`). "Done" means built and covered by automated tests or a recorded browser run.
"Blocked" means it needs something only the owner can provide (an approval, a credential, a lawyer).

## What a customer can do today (local or once deployed)

Visit the landing page (signed-in visitors get "Open the app" instead of sign-up), read pricing,
performance and legal pages, register (21+ and region check, terms acceptance), confirm their email,
get 20 free credits, open single lines (1 credit) or the whole day's board (5), run AI Advisor
analyses (3, refunded if they fail), open the arbitrage terminal (5 per day), keep a watchlist
(downloadable as a CSV of player names), see their full credit history (with the balance after each
row, and as a CSV that Excel opens as UTF-8), change their password (typed twice, the account's email
is told), sign out other devices, see recent sign-ins and security changes, download all their data
as JSON and delete their account. Sessions end after a week without activity (cookie max-age is still
14 days). Anyone can download every graded line on `/performance` as CSV (one file per date, or every
date in one file; UTF-8 with BOM; an unchanged file answers a repeat download with an empty 304), see
the newest graded date at the top of the page, and see which saved boards are not graded yet. Empty
performance cells name "no data" for screen readers. Missing pages offer links to Performance,
Pricing and Sign in. With Stripe keys set they can subscribe, receive plan credits automatically from
the `invoice.paid` webhook, manage billing and cancel in Stripe's portal, and drop back to Free when
the subscription ends.

## Audit register (what was found and what was done)

| # | Area | Finding | Status |
|---|---|---|---|
| 1 | Product | No customer-facing app: Streamlit dashboard with no accounts, billing or legal pages | Done: `web/` app |
| 2 | Billing | No payments, entitlements or usage limits | Done: Stripe subscriptions + credit ledger; live keys blocked (see below) |
| 3 | Security | No auth, no CSRF, no rate limits, no audit log | Done: scrypt passwords, signed sessions, CSRF on every POST, rate limits, audit log, CSP and security headers |
| 4 | Credits | Needed atomic, idempotent spending | Done: one `BEGIN IMMEDIATE` transaction per spend; tested with 12 concurrent requests |
| 5 | Model | Candidate model at full weight despite zero fitted weight; 7% floor creating fake edges | Fixed earlier (9076e67), kept, regression-tested |
| 6 | Model | Market-relative gaps shown as if they were predictions | Done: every line shows offered price, consensus, model alone, probability used and model weight separately, with a plain warning when weight is 0 |
| 7 | Model | Favourite/long-shot bias in the market price untested | Done: walk-forward test (`engine/market_slope.py`); not significant, so not applied |
| 8 | Arbitrage | Fillable, small and conditional pairs need to be kept apart | Done in the terminal; no orders are ever placed |
| 9 | Advisor | Must not run on stale or missing data and must be paid for fairly | Done: runs only on today's board, charged after it starts, refunded on failure |
| 10 | Legal | No terms, privacy, risk, subscription, refund or acceptable-use pages | Drafts done; **lawyer review blocked** |
| 11 | Ops | No deployment config, backups or health check | Done: `render.yaml`, `/healthz`, `docs/DEPLOY.md` |
| 12 | Scale | SQLite and in-process jobs | Accepted for one instance (documented shortcut); Postgres + queue before scaling out |

## Model evidence (no cherry-picking)

- **Walk-forward market correction**: 11,052 graded lines on 11 dates; 9,878 lines on 8 dates scored
  with a correction fitted only on earlier dates. Brier 0.22282 (market) vs 0.22254 (corrected);
  mean per-date change +0.000003 ± 0.000124; better on 3 of 8 dates. Not distinguishable from noise,
  so the market price is used as is. The market *is* slightly overconfident on long shots (0–30%
  buckets hit less often than priced; 70–100% more often), which the Performance page shows.
- **Live boards graded** (Nov 4–6; earlier model versions): our Brier 0.2303 / 0.2196 / 0.2144 vs
  market 0.2230 / 0.2202 / 0.2142. Flat-stake return on flagged positive gaps −19.2%, −1.2%, −16.2%.
  Also graded: Oct 31 (1,168 decided lines, none with a market price recorded, so no market Brier). Nov 2's grading
  found no final games, and Nov 15 is saved but not graded (as of 2026-11-25). `/performance` is the
  current, complete table.
- **Candidate model**: fitted blend weight 0 on every stat; its own gaps have lost money on live boards.
- **Conclusion**: the product sells transparency and speed (margin-free consensus across venues,
  projections, arbitrage screening), not a proven edge. All marketing copy says so.

## Pricing and unit economics (estimates; verify each input)

| Plan | Price | Credits | Worst-case variable cost* | Stripe fees** | Margin at worst case |
|---|---|---|---|---|---|
| Free | $0 | 20 / 30 days | $0.40 | – | –$0.40 |
| Pro | $29 | 200 | $4.00 | $1.34 | ~$23.66 (82%) |
| Power | $79 | 1,000 | $20.00 | $3.14 | ~$55.86 (71%) |

\* Every credit spent on AI Advisor runs (3 credits each) at an assumed ~$0.06 per run
(~18k input / ~1.1k output tokens; check your OpenAI model's current price). Boards and arbitrage
scans are shared across all customers, so they cost the same with 1 or 1,000 users.

\*\* 2.9% + $0.30 card fee plus 0.7% Stripe Billing.

Fixed monthly costs (estimates): hosting $7–25, BALLDONTLIE plan $10–40, The Odds API $0–59 (optional;
lean mode uses ~8 credits per game per scan), email $0–15, domain ~$1. Total roughly **$20–140/month**,
so break-even is about **1–6 Pro subscribers**.

Cost controls in code: shared scans with a 30-minute (board) and 15-minute (arbitrage) cache; one scan
of each kind at a time; scans start only for confirmed customers; Advisor limited to 6 runs per 10
minutes per network; Odds API optional.

## Legal and provider research (summary, not legal advice)

- **Stripe**: lists "sports forecasting or odds making with a monetary or material prize" as
  prohibited and treats gambling-adjacent businesses case by case
  ([Stripe restricted businesses](https://stripe.com/legal/restricted-businesses),
  [FAQ](https://support.stripe.com/questions/prohibited-and-restricted-business-list-faqs)).
  This product awards no prizes and takes no wagers, but **ask Stripe for written confirmation before
  going live**.
- **Selling picks/analysis**: no single federal rule; secondary sources treat paid analysis as lawful
  speech if not deceptive, but some states (e.g. Rhode Island) restrict paid betting services without
  a licence. **Needs a gaming-law attorney** to decide which states to block (`BLOCKED_REGIONS`).
- **Kalshi data**: API use is governed by the Kalshi Developer Agreement; a secondary source says it
  limits use to members' own trading. Redistributing Kalshi prices to paying customers **needs
  Kalshi's permission or a data licence**
  ([Developer Agreement](https://kalshi-public-docs.s3.amazonaws.com/Kalshi-Developer-Agreement.pdf)).
- **Polymarket**: the international platform is not for US persons; Polymarket US has its own
  participant agreement and API usage policy. **Confirm data-use rights** before showing its prices.
- **Investment-advice rules**: event contracts are CFTC-regulated; giving personalised trading advice
  for compensation can raise commodity-trading-adviser questions. The product gives general,
  non-personalised analysis and no execution; **have counsel confirm**.
- **Auto-renewal laws** (e.g. California): terms are shown before checkout, cancellation is online,
  cancellation and renewal emails are sent. Counsel should confirm the exact disclosures.

## Launch checklist

**Done and tested**
- [x] Landing, pricing, performance, legal pages; SEO meta, Open Graph, JSON-LD, sitemap, robots
- [x] Registration with 21+ and region checks, email confirmation, login, logout, password reset
- [x] Credits: free grant, monthly reset, atomic spend, no double charge, refunds, ledger, admin adjustments
- [x] Stripe checkout, signed + idempotent webhooks, renewals, failed payments, cancellation, plan end
- [x] Explorer, line detail, watchlist, arbitrage terminal, AI Advisor, account, admin
- [x] Security: CSRF, rate limits, session invalidation, CSP, admin hidden (404), no secrets in pages
- [x] Account recovery per OWASP: per-account reset limits, password-change emails, old reset links voided,
      spent links deleted, no-store and no-referrer on sign-in and reset pages, common-password blocklist
- [x] `/.well-known/security.txt` (served only once `SUPPORT_EMAIL` is set)
- [x] Account deletion (cancels subscription, erases personal data) and a JSON export of the customer's data

**Needs production credentials (owner)**
- [ ] Stripe **test-mode** keys and prices to run the paid journey end to end (the only Stripe account
      connected in this session was live mode, which was not used)
- [ ] SMTP provider credentials
- [ ] Hosting account, domain, `SECRET_KEY`, persistent disk
- [ ] `SUPPORT_EMAIL` set to an inbox someone reads (until then `security.txt`, the pricing page's Teams
      card and the "write to us" line in password-change emails stay hidden; the legal Contact page
      still shows the placeholder)

**Needs external approval**
- [ ] Stripe written confirmation that the business model is allowed, then live keys
- [ ] Kalshi (and Polymarket US) permission to show their prices to paying customers

**Needs legal review**
- [ ] All seven legal pages (fill in `[LEGAL ENTITY NAME]`, `[BUSINESS ADDRESS]`, `[GOVERNING STATE]`)
- [ ] Which states to block (`BLOCKED_REGIONS`), tax registration for digital subscriptions
- [ ] Then set the `legal_reviewed` switch in Admin to remove the "draft" notices

**Not yet tested**
- [ ] Paid journey against real Stripe test mode (tested with signed fake events and faked Stripe API reads)
- [ ] Real email delivery
- [ ] Load beyond a single instance
