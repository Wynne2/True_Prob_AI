"""
Stripe subscriptions. Stripe is the source of truth: every relevant webhook re-reads the
subscription from Stripe and copies its state onto the user; the checkout redirect grants nothing.

Environment: STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_PRO, STRIPE_PRICE_POWER.
Without them billing is off and the pricing page says so. Plan changes, payment methods,
invoices and cancellation happen in Stripe's hosted customer portal.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Optional

import stripe

from web import db, mailer

logger = logging.getLogger(__name__)

HANDLED = {
    "checkout.session.completed", "customer.subscription.created", "customer.subscription.updated",
    "customer.subscription.deleted", "invoice.paid", "invoice.payment_failed",
}


def enabled() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY") and os.environ.get("STRIPE_WEBHOOK_SECRET") and price_ids())


def price_ids() -> dict[str, str]:
    """plan -> Stripe price id, for the plans that are configured."""
    return {plan: pid for plan in ("pro", "power") if (pid := os.environ.get(f"STRIPE_PRICE_{plan.upper()}"))}


def _client():
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    return stripe


def create_checkout(user, plan: str, base_url: str) -> str:
    """Hosted checkout for a new subscription; returns the URL to send the customer to."""
    s = _client()
    customer = user["stripe_customer_id"]
    if not customer:
        customer = s.Customer.create(email=user["email"], metadata={"user_id": str(user["id"])})["id"]
        db.update_user(user["id"], stripe_customer_id=customer)
    session = s.checkout.Session.create(
        mode="subscription",
        customer=customer,
        client_reference_id=str(user["id"]),
        line_items=[{"price": price_ids()[plan], "quantity": 1}],
        success_url=f"{base_url}/app/account?checkout=done",
        cancel_url=f"{base_url}/pricing",
        allow_promotion_codes=True,
        # Auto-renewal terms are shown before payment (state auto-renewal laws).
        custom_text={"submit": {"message": "Renews monthly until you cancel. Cancel any time in Account, effective at the end of the paid period."}},
    )
    return session["url"]


def create_portal(user, base_url: str) -> str:
    return _client().billing_portal.Session.create(customer=user["stripe_customer_id"], return_url=f"{base_url}/app/account")["url"]


def cancel_now(user) -> None:
    """Used by account deletion: end the subscription immediately so billing stops."""
    if user["stripe_subscription_id"] and os.environ.get("STRIPE_SECRET_KEY"):
        try:
            _client().Subscription.cancel(user["stripe_subscription_id"])
        except stripe.StripeError as exc:
            logger.error("Could not cancel subscription for user %s: %s", user["id"], type(exc).__name__)
            raise


def fetch_subscription(sub_id: str) -> dict:
    return json.loads(str(_client().Subscription.retrieve(sub_id)))


def _plan_for(sub: dict) -> Optional[str]:
    by_price = {pid: plan for plan, pid in price_ids().items()}
    for item in (sub.get("items") or {}).get("data") or []:
        plan = by_price.get((item.get("price") or {}).get("id"))
        if plan:
            return plan
    return None


def _period_end(sub: dict) -> Optional[float]:
    items = (sub.get("items") or {}).get("data") or [{}]
    return sub.get("current_period_end") or items[0].get("current_period_end")


def sync_subscription(sub: dict):
    """Copy a Stripe subscription onto its user. Returns the refreshed user row, or None when unknown."""
    user = db.get_user(customer=sub.get("customer"))
    if user is None:
        logger.warning("Stripe subscription %s has no matching user", sub.get("id"))
        return None
    # An older subscription's event must not overwrite a newer one's state.
    if user["stripe_subscription_id"] and user["stripe_subscription_id"] != sub.get("id") \
            and sub.get("status") not in db.ACTIVE_STATUSES:
        return user
    plan = _plan_for(sub)
    if plan is None:
        logger.error("Stripe subscription %s has no known price; access not granted", sub.get("id"))
        return user
    db.update_user(
        user["id"], plan=plan, sub_status=str(sub.get("status") or ""), stripe_subscription_id=sub.get("id"),
        period_end=_period_end(sub), cancel_at_period_end=int(bool(sub.get("cancel_at_period_end") or sub.get("cancel_at"))),
    )
    return db.get_user(user["id"])


def verify(payload: bytes, signature: str) -> dict:
    """Check Stripe's signature and timestamp. Raises ValueError on anything that does not verify."""
    try:
        stripe.Webhook.construct_event(payload, signature, os.environ["STRIPE_WEBHOOK_SECRET"])
    except (stripe.SignatureVerificationError, ValueError, KeyError) as exc:
        raise ValueError("invalid webhook signature") from exc
    return json.loads(payload)


def _invoice_subscription(invoice: dict) -> Optional[str]:
    parent = (invoice.get("parent") or {}).get("subscription_details") or {}
    return invoice.get("subscription") or parent.get("subscription")


def handle_event(event: dict) -> str:
    """Apply one verified event. Each event id is processed once; a repeat delivery is a no-op."""
    etype, obj = event.get("type"), (event.get("data") or {}).get("object") or {}
    if etype not in HANDLED:
        return "ignored"
    try:
        db.execute("INSERT INTO stripe_events VALUES (?,?,?)", (event["id"], etype, time.time()))
    except sqlite3.IntegrityError:
        return "duplicate"
    try:
        return _apply(etype, obj)
    except Exception:
        # Let Stripe retry: forget the event so the retry is processed.
        db.execute("DELETE FROM stripe_events WHERE id=?", (event["id"],))
        raise


def _apply(etype: str, obj: dict) -> str:
    if etype == "checkout.session.completed":
        uid = obj.get("client_reference_id")
        user = db.get_user(int(uid)) if uid and str(uid).isdigit() else None
        if user and obj.get("customer") and not user["stripe_customer_id"]:
            db.update_user(user["id"], stripe_customer_id=obj["customer"])
        if obj.get("subscription"):
            sync_subscription(fetch_subscription(obj["subscription"]))
        db.track("checkout_completed", user["id"] if user else None)
        return "ok"

    if etype.startswith("customer.subscription."):
        before = db.get_user(customer=obj.get("customer"))
        # Re-read from Stripe so an out-of-order delivery cannot leave stale state.
        sub = obj if etype.endswith("deleted") else fetch_subscription(obj["id"])
        user = sync_subscription(sub)
        if user is None:
            return "no_user"
        if etype.endswith("deleted") and user["stripe_subscription_id"] == sub.get("id"):
            db.grant_period(user["id"], "free", f"end:{sub.get('id')}")
            mailer.send(user["email"], "Your True Prob AI subscription has ended",
                        f"Your paid plan has ended and your account is back on the Free plan ({db.PLANS['free']['credits']} "
                        "credits every 30 days). "
                        "You can subscribe again any time from the pricing page.")
            db.track("subscription_ended", user["id"])
        elif before and not before["cancel_at_period_end"] and user["cancel_at_period_end"]:
            mailer.send(user["email"], "Your True Prob AI cancellation is confirmed",
                        "Your subscription will not renew. You keep your plan and credits until the end of the paid period.")
            db.track("subscription_cancel_scheduled", user["id"])
        return "ok"

    sub_id = _invoice_subscription(obj)
    if not sub_id:
        return "not_subscription"
    user = sync_subscription(fetch_subscription(sub_id))
    if user is None:
        return "no_user"
    if etype == "invoice.paid":
        plan = db.effective_plan(user)
        if plan != "free" and db.grant_period(user["id"], plan, f"invoice:{obj.get('id')}"):
            mailer.send(user["email"], "Payment received: True Prob AI",
                        f"Thanks. Your {db.PLANS[plan]['name']} plan is active and {db.PLANS[plan]['credits']} credits are "
                        "available for this period. Receipts and invoices are in Account > Manage billing.")
            db.track("invoice_paid", user["id"])
    else:
        mailer.send(user["email"], "Payment failed: True Prob AI",
                    "We could not charge your payment method. Stripe will retry; update your card in "
                    "Account > Manage billing to keep your plan.")
        db.track("payment_failed", user["id"])
    return "ok"
