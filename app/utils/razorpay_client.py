"""
Client for Razorpay's REST API — Razorpay Subscriptions is VANYA's billing
engine, used ONLY from the website checkout flow (never from the Flutter
app itself). See billing_service.py's module docstring for why: the app is
deliberately "consumption-only" under Google Play's reader-app policy, so
it has zero purchase UI and Google takes 0% commission — the actual
subscription purchase happens on the VANYA website, which calls the two
functions below server-side (create_subscription at checkout time,
verify_webhook_signature to trust Razorpay's async status updates).

TODO before this can create real subscriptions:
1. Razorpay Dashboard > Account & Settings > API Keys > generate a Key
   ID + Key Secret, put them in .env as RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET.
2. Razorpay Dashboard > Subscriptions > Plans > create two plans (monthly,
   INR 99 and INR 199) — copy their plan_id (looks like "plan_Qxxxxxxxxxx")
   into app/core/plans.py's razorpay_plan_id fields, replacing the
   "plan_REPLACE_..." placeholders.
3. Razorpay Dashboard > Account & Settings > Webhooks > add a webhook
   pointing at {PUBLIC_BASE_URL}/v1/webhooks/razorpay, subscribed to the
   subscription.* events, and copy its Secret into .env as
   RAZORPAY_WEBHOOK_SECRET.
4. Razorpay requires their own business KYC/activation before *live* mode
   works — test mode (using test API keys) works immediately with no KYC,
   good enough for building/testing the whole flow before that completes.
"""
import hashlib
import hmac
from typing import Any, Dict, Optional

import httpx

from app.core.config import get_settings
from app.core.exceptions import ExternalProviderError

settings = get_settings()

_API_BASE = "https://api.razorpay.com/v1"


def _auth() -> tuple:
    return (settings.razorpay_key_id, settings.razorpay_key_secret)


async def create_subscription(plan_id: str, vanya_user_id: str, total_count: int = 120) -> Dict[str, Any]:
    """Creates a Razorpay subscription in "created" status — the website
    then opens Razorpay Checkout with the returned id, and the user
    completes payment/e-mandate registration there. total_count=120
    (10 years of monthly cycles) is Razorpay's convention for an
    "until cancelled" subscription; it auto-renews each cycle regardless.

    notes.vanya_user_id is what let the webhook (see billing_service.py)
    resolve which of our users this subscription belongs to — Razorpay
    echoes `notes` back on every webhook payload unchanged."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{_API_BASE}/subscriptions",
                auth=_auth(),
                json={
                    "plan_id": plan_id,
                    "total_count": total_count,
                    "customer_notify": 1,
                    "notes": {"vanya_user_id": vanya_user_id},
                },
            )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise ExternalProviderError(f"Razorpay rejected subscription creation: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise ExternalProviderError(f"Razorpay subscription creation request failed: {exc}") from exc


async def cancel_subscription(subscription_id: str, cancel_at_cycle_end: bool = True) -> Dict[str, Any]:
    """cancel_at_cycle_end=True (Razorpay's `cancel_at_cycle_end: 1`) lets
    the user keep access through what they already paid for instead of
    cutting them off mid-cycle — the webhook (subscription.cancelled)
    still won't fire until the cycle actually ends, at which point
    process_razorpay_webhook flips them to "expired" the normal way."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{_API_BASE}/subscriptions/{subscription_id}/cancel",
                auth=_auth(),
                json={"cancel_at_cycle_end": 1 if cancel_at_cycle_end else 0},
            )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise ExternalProviderError(f"Razorpay rejected subscription cancellation: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise ExternalProviderError(f"Razorpay subscription cancellation request failed: {exc}") from exc


async def fetch_subscription(subscription_id: str) -> Dict[str, Any]:
    """Used as a fallback when the website wants to confirm status right
    after checkout, before any webhook has necessarily arrived yet."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{_API_BASE}/subscriptions/{subscription_id}", auth=_auth())
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise ExternalProviderError(f"Failed to fetch Razorpay subscription: {exc}") from exc


def verify_webhook_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Razorpay signs every webhook POST body with HMAC-SHA256 using the
    webhook secret configured in their dashboard, sent as the
    X-Razorpay-Signature header — timing-safe compared here, same
    principle as the old RevenueCat shared-secret check this replaced."""
    if not signature_header:
        return False
    expected = hmac.new(settings.razorpay_webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# Razorpay subscription.entity.status values -> our trichotomy.
# "created" = awaiting the customer's first authorization.
# "authenticated" = the e-mandate/card is registered, but the first charge
#   hasn't succeeded yet — not entitled until it does.
# "pending" = a renewal charge failed and is in Razorpay's retry window —
#   still entitled for now (grace period), same spirit as how App Store/
#   Play grace periods work, so a single missed auto-debit doesn't
#   instantly lock the user out.
_ACTIVE_STATUSES = {"active", "pending"}
_EXPIRED_STATUSES = {"cancelled", "completed", "expired", "halted"}


def status_for(razorpay_status: str) -> str:
    if razorpay_status in _ACTIVE_STATUSES:
        return "active"
    if razorpay_status in _EXPIRED_STATUSES:
        return "expired"
    return "free"
