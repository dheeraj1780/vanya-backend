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
from app.core.plans import PLANS, RAZORPAY_PLAN_ID_TO_PLAN, PlanConfig, plan_rank
from app.models.user import User
from app.repositories.billing_repository import (
    create_webhook_event,
    get_subscription_by_user,
    get_webhook_event_by_external_id,
    upsert_subscription,
)
from app.repositories.user_repository import get_user_by_id, update_subscription_status
from app.schemas.billing import ChangePlanData, CreateSubscriptionData, SubscriptionStatusData
from app.services.entitlement_service import plan_for_user
from app.utils.razorpay_client import cancel_subscription as razorpay_cancel_subscription
from app.utils.razorpay_client import create_subscription as razorpay_create_subscription
from app.utils.razorpay_client import fetch_subscription as razorpay_fetch_subscription
from app.utils.razorpay_client import (
    UpiPlanChangeUnsupportedError,
    status_for,
    update_subscription_plan as razorpay_update_subscription_plan,
    verify_webhook_signature,
)

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


async def change_plan(db: AsyncSession, user: User, new_plan_key: str) -> ChangePlanData:
    """Downgrade from one active paid tier to a cheaper one (e.g.
    Photosynthesis PhD -> Green Thumb) — immediate feature change, no
    refund, existing data untouched. Deliberately NOT a general
    "change plan" (upgrades still go through create_subscription's normal
    Checkout flow, since collecting more money needs a real payment step;
    this only ever moves a user to something cheaper).

    Two independent things happen, on purpose:
    1. Razorpay's own subscription is told to change plan at the END of
       the current cycle (razorpay_update_subscription_plan, schedule_
       change_at="cycle_end") — the cycle already paid for at the higher
       price keeps running unchanged, so there's nothing to refund; only
       the NEXT renewal bills at the new lower price.
    2. This backend's own DB flips subscription_product_id to the new
       plan RIGHT NOW, independent of Razorpay's schedule — so
       plan_for_user (and every entitlement check) reflects the lower
       tier's limits immediately, per the product decision that a
       downgrade's *feature* change shouldn't wait for the next billing
       cycle even though the *price* change does. This is the one place
       outside the webhook allowed to touch subscription_product_id — see
       the module docstring's trust boundary — because it can only ever
       move a user to something they already verified-paid for at least
       as much as (a cheaper plan), never grant anything new.

    Existing plants/growth memories over the new lower limit are never
    touched — same PLANT COLLECTION RULES principle as everywhere else
    (see plans.py): limits only ever block *creating new* things past the
    cap, never delete/hide what's already there.
    """
    try:
        new_plan = PLANS.get(new_plan_key)
        if new_plan is None or new_plan.razorpay_plan_id is None:
            raise BadRequestError(f"'{new_plan_key}' is not a purchasable plan")

        current_plan = plan_for_user(user)
        if plan_rank(new_plan_key) >= plan_rank(current_plan.key):
            raise BadRequestError(
                f"{new_plan.display_name} isn't a downgrade from {current_plan.display_name} — "
                "to upgrade, subscribe to it from the plans page instead."
            )

        sub = await get_subscription_by_user(db, user.user_id)
        if sub is None or not sub.provider_subscription_id:
            raise NotFoundError("No active subscription found for this account")

        try:
            await razorpay_update_subscription_plan(sub.provider_subscription_id, new_plan.razorpay_plan_id, schedule_change_at="cycle_end")
        except UpiPlanChangeUnsupportedError:
            return await _change_plan_upi_fallback(db, user, sub.provider_subscription_id, new_plan)

        # Mirrors the webhook's own write pattern (status/expires_at stay
        # exactly as they were — this isn't a status change) but flips the
        # plan association immediately, both on SUBSCRIPTIONS and the
        # denormalized USERS columns.
        await upsert_subscription(db, user.user_id, new_plan.razorpay_plan_id, sub.status, sub.expires_at, sub.provider_subscription_id)
        await update_subscription_status(db, user, user.subscription_status, user.subscription_expires_at, new_plan.razorpay_plan_id)

        return ChangePlanData(
            plan=new_plan.key,
            message=(
                f"You're on {new_plan.display_name} now, limits and all. You already paid for this cycle at the "
                f"old price, so nothing changes there — your next bill is what reflects the new ₹{new_plan.price_inr}/month."
            ),
        )
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to change plan: {exc}") from exc


async def _change_plan_upi_fallback(db: AsyncSession, user: User, old_subscription_id: str, new_plan: PlanConfig) -> ChangePlanData:
    """Razorpay's in-place plan-change endpoint only works for card-based
    subscriptions — UPI Autopay mandates (the overwhelming majority of
    VANYA's India subscribers) can't be updated in place at all (see
    UpiPlanChangeUnsupportedError). The only Razorpay-supported way to move
    a UPI subscriber to a cheaper plan is: cancel the current mandate at
    cycle end (so the cycle already paid for keeps running unchanged, same
    "no refund" policy as the direct path), and create a brand new
    subscription for the lower plan with its first charge deferred to that
    same cycle-end date — so nothing bills twice and nothing bills early.

    Unlike the direct path, a NEW mandate needs the customer's explicit
    authorization (Razorpay requires this for any new UPI Autopay setup,
    same as subscribing the first time), so this can't be fully silent —
    the website opens Checkout for the new subscription_id this returns.
    Feature access still flips immediately regardless of whether/when that
    Checkout step completes, per the same "downgrade never waits on
    payment machinery" decision the direct path already makes; if the
    customer never completes it, the old mandate simply runs out at cycle
    end with nothing behind it — never grants anything unpaid, only fails
    open into "no active subscription", same as an ordinary cancellation.
    """
    current = await razorpay_fetch_subscription(old_subscription_id)
    current_end = current.get("current_end")

    await razorpay_cancel_subscription(old_subscription_id, cancel_at_cycle_end=True)
    new_sub = await razorpay_create_subscription(new_plan.razorpay_plan_id, user.user_id, start_at=current_end)

    await upsert_subscription(db, user.user_id, new_plan.razorpay_plan_id, "active", user.subscription_expires_at, new_sub["id"])
    await update_subscription_status(db, user, user.subscription_status, user.subscription_expires_at, new_plan.razorpay_plan_id)

    return ChangePlanData(
        plan=new_plan.key,
        message=(
            f"You're on {new_plan.display_name} now, limits and all. Since your current plan is billed via UPI, "
            "Razorpay needs one quick confirmation for the new plan so it keeps billing after your current cycle ends."
        ),
        requires_checkout=True,
        subscription_id=new_sub["id"],
        razorpay_key_id=settings.razorpay_key_id,
    )


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
