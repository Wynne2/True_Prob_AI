---
name: billing-credits-safety
description: Use when touching plans, prices, credits, charges, refunds, Stripe checkout, the Stripe webhook, subscriptions or account deletion (web/db.py, web/billing.py, web/app.py billing routes).
---
# Billing and credits safety

Billing bugs cost money and trust at once. Every rule here has tests; keep them passing.

## Where things live

- `web/db.py`: `PLANS`, `COSTS`, `spend()`, `refund()`, `grant_period()`.
- `web/billing.py`: Stripe checkout, portal, webhook handling (`invoice.paid` grants plan credits).
- `tests/test_web.py`: Stripe's API is replaced by fakes and webhooks are signed with a test secret.

## Rules

1. **No real Stripe.** Never call Stripe with real keys, never use the live account, never create
   or change real products, prices or customers. Tests use fakes; manual checks use test mode only,
   and only if Michael has supplied test keys.
2. Spending is atomic: the balance check and the deduction happen in one `BEGIN IMMEDIATE`
   transaction (`db.transaction()`). Never split them.
3. Everything is idempotent: the same `ref` charges or grants once (double-clicks, retried webhooks).
4. Charge only after work starts; **refund when nothing is delivered** (failed or empty Advisor run,
   failed scan).
5. Webhooks: verify the signature, ignore unknown price IDs, never trust plan or amount from the client.
6. Price or plan changes (amounts, credits, new tiers) need Michael's approval and matching Stripe
   prices; list them under "Needs owner". Agents may improve how plans are explained, not what they cost.
7. No Stripe IDs, keys or webhook secrets in code, docs, tests or logs. They come from environment
   variables (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_POWER`).

## Every billing change needs tests for

- the happy path, a double submit, a retried webhook, and the refund path;
- concurrent spends cannot overdraw (follow the existing concurrency test in `tests/test_web.py`);
- the receipt and balance text the customer sees.
