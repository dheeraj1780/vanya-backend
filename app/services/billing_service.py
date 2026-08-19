"""
Billing service — Razorpay Subscriptions, sold ONLY on the VANYA website,
never inside the Flutter app.

Why: Google Play requires Google Play Billing (15-30% commission) for any
purchase flow that exists *inside* the app. Google Play's long-standing
"reader app" / consumption-only exemption lets an app be commission-free
if it has ZERO purchase UI at all — the user signs up/pays somewhere else
and the app just checks their account status. VANYA's paywall screen is
therefore informational only (see paywall_screen.dart) and the actual
subscription purchase happens on a separate website, which calls the two
entry points below. This is also why RevenueCat was removed entirely —
its whole purpose was mediating Apple/Google's own billing SDKs, which
this architecture deliberately doesn't use.

Trust boundary: the website NEVER tells this backend "the user paid" —
only Razorpay's own signed webhook (process_razorpay_webhook) is allowed
to flip subscription_status. create_subscription only ever creates a
Razorpay subscription in "created" (unpaid) status; get_subscription_status
is a read-only poll of whatever this backend's own DB currently believes,
which is only ever written by the webhook. Same "entitlement must be
verified securely, never trust the client" principle the rest of this
app already follows.
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException, BadRequestError, InternalServerError, InvalidSignatureError, NotFoundError
from app.core.plans import PLANS, RAZORPAY_PLAN_ID_TO_PLAN
from app.models.user import User
from app.repositories.billing_repository import (
    create_webhook_event,
    get_subscription_by_user,
    get_webhook_event_by_external_id,
    upsert_subscription,
)
from app.repositories.user_repository import get_user_by_id, update_subscription_status
from app.schemas.billing import CreateSubscriptionData, SubscriptionStatusData
from app.services.entitlement_service import plan_for_user
from app.utils.razorpay_client import cancel_subscription as razorpay_cancel_subscription
from app.utils.razorpay_client import create_subscription as razorpay_create_subscription
from app.utils.razorpay_client import status_for, verify_webhook_signature

settings = get_settings()


async def create_subscription(db: AsyncSession, user: User, plan_key: str) -> CreateSubscriptionData:
    try:
        plan = PLANS.get(plan_key)
        if plan is None or plan.razorpay_plan_id is None:
            raise BadRequestError(f"'{plan_key}' is not a purchasable plan")

        result = await razorpay_create_subscription(plan.razorpay_plan_id, user.user_id)
        return CreateSubscriptionData(subscription_id=result["id"], razorpay_key_id=settings.razorpay_key_id)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to create Razorpay subscription: {exc}") from exc


async def cancel_subscription(db: AsyncSession, user: User) -> None:
    """Cancels at the end of the current billing cycle — the user keeps
    what they already paid for. Doesn't write subscription_status itself;
    that only ever happens via the webhook once Razorpay actually fires
    subscription.cancelled at cycle end (same trust boundary as everywhere
    else in this module)."""
    try:
        sub = await get_subscription_by_user(db, user.user_id)
        if sub is None or not sub.provider_subscription_id:
            raise NotFoundError("No subscription found for this account")
        await razorpay_cancel_subscription(sub.provider_subscription_id, cancel_at_cycle_end=True)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to cancel subscription: {exc}") from exc


async def get_subscription_status(user: User) -> SubscriptionStatusData:
    """Read-only — reflects whatever the webhook has already written to
    this user's row. The website polls this after checkout since the
    webhook can lag the Checkout.js success callback by a few seconds."""
    plan = plan_for_user(user)
    return SubscriptionStatusData(
        subscription_status=user.subscription_status,  # type: ignore[arg-type]
        plan=plan.key if plan.price_inr > 0 else None,
        expires_at=user.subscription_expires_at,
    )


def _event_dedup_key(header_event_id: Optional[str], raw_body: bytes) -> str:
    """Prefers Razorpay's X-Razorpay-Event-Id header when present; falls
    back to a hash of the raw body (Razorpay retries a failed delivery
    with the identical body, so this still dedupes correctly even without
    the header)."""
    if header_event_id:
        return header_event_id
    return hashlib.sha256(raw_body).hexdigest()


async def process_razorpay_webhook(
    db: AsyncSession, raw_body: bytes, signature_header: Optional[str], header_event_id: Optional[str]
) -> None:
    try:
        if not verify_webhook_signature(raw_body, signature_header):
            raise InvalidSignatureError("Razorpay webhook signature did not match")

        event: Dict[str, Any] = json.loads(raw_body)
        event_type: str = event.get("event", "unknown")
        payload: Dict[str, Any] = event.get("payload", {})
        subscription_entity = payload.get("subscription", {}).get("entity")
        if not subscription_entity:
            # Events without a subscription entity (e.g. standalone payment
            # events) aren't relevant to subscription status — acknowledge
            # and move on rather than failing the whole webhook.
            return

        external_event_id = _event_dedup_key(header_event_id, raw_body)
        if await get_webhook_event_by_external_id(db, external_event_id):
            return  # already processed — Razorpay retries on any non-2xx response

        vanya_user_id = subscription_entity.get("notes", {}).get("vanya_user_id")
        if not vanya_user_id:
            await create_webhook_event(db, None, event_type, external_event_id, event)
            return  # a subscription we didn't create (e.g. test data) — log and move on

        user = await get_user_by_id(db, vanya_user_id)
        if user is None:
            await create_webhook_event(db, None, event_type, external_event_id, event)
            return

        razorpay_plan_id = subscription_entity.get("plan_id")
        razorpay_subscription_id = subscription_entity.get("id")
        status = status_for(subscription_entity.get("status", ""))
        current_end = subscription_entity.get("current_end")
        expires_at = datetime.fromtimestamp(current_end, tz=timezone.utc) if current_end else None

        # razorpay_plan_id must be one we currently recognize (see
        # plans.py) — otherwise don't touch the user's plan association
        # (an unrecognized/retired plan_id shouldn't silently downgrade
        # them), but still record the webhook itself for audit, and still
        # honor an "expired" status regardless (cancellation always applies).
        recognized_plan_id = razorpay_plan_id if razorpay_plan_id in RAZORPAY_PLAN_ID_TO_PLAN else None

        sub = await upsert_subscription(
            db, user.user_id, recognized_plan_id or razorpay_plan_id, status, expires_at, razorpay_subscription_id
        )
        if recognized_plan_id is not None or status == "expired":
            await update_subscription_status(db, user, status, expires_at, recognized_plan_id)
        await create_webhook_event(db, sub.subscription_id, event_type, external_event_id, event)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to process Razorpay webhook: {exc}") from exc
