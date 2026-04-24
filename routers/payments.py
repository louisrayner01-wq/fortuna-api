"""
payments.py — Stripe subscription management.

Endpoints:
  POST /api/payments/create-checkout  — create a Stripe checkout session (with free trial)
  POST /api/payments/webhook          — receive Stripe webhook events
  GET  /api/payments/portal           — create a billing portal session
  GET  /api/payments/status           — current subscription status for the user
"""

import os
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from database import get_db
from models import User, Subscription
from routers.users import get_current_user

router = APIRouter(prefix="/api/payments", tags=["payments"])

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
PRICE_ID        = os.environ.get("STRIPE_PRICE_ID", "")
WEB_URL         = os.environ.get("WEB_URL", "https://fortuna-web.vercel.app")


# ── Checkout ──────────────────────────────────────────────────────────────────

@router.post("/create-checkout")
def create_checkout(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")
    if not PRICE_ID:
        raise HTTPException(status_code=500, detail="Stripe price not configured")

    # Re-use existing Stripe customer if we have one
    customer_id = current_user.stripe_customer_id
    if not customer_id:
        customer = stripe.Customer.create(email=current_user.email)
        customer_id = customer.id
        current_user.stripe_customer_id = customer_id
        db.commit()

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": PRICE_ID, "quantity": 1}],
        mode="subscription",
        subscription_data={"trial_period_days": 30},
        success_url=f"{WEB_URL}/dashboard?subscribed=1",
        cancel_url=f"{WEB_URL}/subscribe?cancelled=1",
        allow_promotion_codes=True,
    )
    return {"url": session.url}


# ── Billing portal ────────────────────────────────────────────────────────────

@router.get("/portal")
def billing_portal(
    current_user: User = Depends(get_current_user),
):
    if not current_user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No billing account found")
    session = stripe.billing_portal.Session.create(
        customer=current_user.stripe_customer_id,
        return_url=f"{WEB_URL}/settings",
    )
    return {"url": session.url}


# ── Subscription status ───────────────────────────────────────────────────────

@router.get("/status")
def payment_status(
    current_user: User = Depends(get_current_user),
):
    sub = current_user.subscription
    if not sub:
        return {"status": "none", "plan": None, "trial_end": None, "period_end": None}
    return {
        "status":     sub.status,
        "plan":       sub.plan,
        "trial_end":  None,
        "period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
    }


# ── Webhook ───────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
    db: Session = Depends(get_db),
):
    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    etype = event["type"]
    data  = event["data"]["object"]

    # ── Checkout completed (trial or paid) ────────────────────────────────────
    if etype == "checkout.session.completed":
        customer_id = data.get("customer")
        stripe_sub_id = data.get("subscription")
        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user and stripe_sub_id:
            _activate_subscription(db, user, stripe_sub_id, "trialing")

    # ── Subscription updated (trial ends, renews, etc.) ───────────────────────
    elif etype == "customer.subscription.updated":
        _sync_subscription(db, data)

    # ── Trial ends — payment taken, keep active ───────────────────────────────
    elif etype == "customer.subscription.trial_will_end":
        _sync_subscription(db, data)

    # ── Payment succeeded ─────────────────────────────────────────────────────
    elif etype == "invoice.payment_succeeded":
        stripe_sub_id = data.get("subscription")
        customer_id   = data.get("customer")
        if stripe_sub_id:
            user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
            if user:
                _activate_subscription(db, user, stripe_sub_id, "active")

    # ── Payment failed ────────────────────────────────────────────────────────
    elif etype == "invoice.payment_failed":
        stripe_sub_id = data.get("subscription")
        if stripe_sub_id:
            sub = db.query(Subscription).filter(
                Subscription.stripe_sub_id == stripe_sub_id
            ).first()
            if sub:
                sub.status = "past_due"
                db.commit()

    # ── Subscription cancelled ────────────────────────────────────────────────
    elif etype in ("customer.subscription.deleted",):
        _sync_subscription(db, data, force_status="cancelled")

    return {"status": "ok"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _activate_subscription(db: Session, user: User, stripe_sub_id: str, status: str):
    sub = user.subscription
    if not sub:
        sub = Subscription(user_id=user.id)
        db.add(sub)
    sub.stripe_sub_id = stripe_sub_id
    sub.status        = status
    sub.plan          = "pro"
    db.commit()


def _sync_subscription(db: Session, stripe_sub: dict, force_status: str = None):
    stripe_sub_id = stripe_sub.get("id")
    sub = db.query(Subscription).filter(
        Subscription.stripe_sub_id == stripe_sub_id
    ).first()
    if not sub:
        return
    sub.status = force_status or stripe_sub.get("status", sub.status)
    period_end = stripe_sub.get("current_period_end")
    if period_end:
        sub.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
    db.commit()
