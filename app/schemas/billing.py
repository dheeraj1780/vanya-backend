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


class SubscriptionStatusData(BaseModel):
    """Read-only status check — the website polls this right after
    Checkout.js reports success, since the webhook (the actual source of
    truth) can arrive a few seconds after the checkout UI closes."""
    subscription_status: Literal["free", "active", "expired"]
    plan: Optional[str] = None
    expires_at: Optional[datetime] = None
