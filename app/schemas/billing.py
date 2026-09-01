from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class CreateSubscriptionRequest(BaseModel):
    """Called from the VANYA website (never the Flutter app — see
    billing_service.py's module docstring on why there's no in-app
    purchase flow at all)."""
    plan: Literal["green_thumb", "photosynthesis_phd"]


class CreateSubscriptionData(BaseModel):
    """Everything the website's Razorpay Checkout.js widget needs to open
    — razorpay_key_id is Razorpay's PUBLIC key (safe to send to the
    client, same as any store's publishable key), subscription_id is what
    Checkout.js uses to attach the payment to the subscription that was
    just created server-side."""
    subscription_id: str
    razorpay_key_id: str


class ChangePlanRequest(BaseModel):
    """Downgrade only — from one active paid tier to a cheaper one, e.g.
    Photosynthesis PhD -> Green Thumb. See billing_service.change_plan for
    why upgrades don't use this (they need to actually collect more money,
    so they go through the normal create-subscription + Checkout flow)."""
    plan: Literal["green_thumb", "photosynthesis_phd"]


class ChangePlanData(BaseModel):
    plan: str
    message: str
    # Razorpay's in-place plan-change endpoint (the instant, no-Checkout
    # path) only works for card-based subscriptions — it hard-rejects UPI
    # Autopay mandates ("subscriptions cannot be updated when payment mode
    # is upi"), which is most of VANYA's India-first subscriber base. When
    # that happens, change_plan falls back to cancel-at-cycle-end + a new
    # subscription for the lower plan (deferred to start when the old one
    # ends) — but a NEW UPI mandate needs the customer's explicit
    # authorization, same as subscribing the first time. requires_checkout
    # tells the website to open Razorpay Checkout for subscription_id
    # instead of treating the downgrade as already fully done.
    requires_checkout: bool = False
    subscription_id: Optional[str] = None
    razorpay_key_id: Optional[str] = None


class SubscriptionStatusData(BaseModel):
    """Read-only status check — the website polls this right after
    Checkout.js reports success, since the webhook (the actual source of
    truth) can arrive a few seconds after the checkout UI closes."""
    subscription_status: Literal["free", "active", "expired"]
    plan: Optional[str] = None
    expires_at: Optional[datetime] = None
