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
which is only ever written by the webhook (create_subscription is the one
exception, and only ever writes status="created" — see its own docstring
for why). Same "entitlement must be verified securely, never trust the
client" principle the rest of this app already follows.
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
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
    list_subscriptions_for_reconciliation,
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
from app.utils.time_utils import naive_utc

settings = get_settings()
logger = logging.getLogger("plant_companion")

# How long a just-started ("created", never-activated) subscription blocks
# a second create_subscription call for the same user — see that
# function's own docstring. Generous enough to comfortably cover a real
# Checkout session (open the modal, enter UPI/card details, confirm),
# short enough that someone who genuinely abandoned it isn't locked out of
# ever subscribing again for long.
PENDING_SUBSCRIBE_COOLDOWN = timedelta(minutes=20)


async def create_subscription(db: AsyncSession, user: User, plan_key: str) -> CreateSubscriptionData:
    """Guards against exactly the failure mode found live in testing: five
    simultaneously-"active" Razorpay subscriptions stacked on one account
    from repeated re-subscribing. Nothing on Razorpay's side prevents a
    customer from having multiple subscriptions at once — that's by
    design on their end, since a business might legitimately sell more
    than one product to the same customer — so enforcing "one plan per
    VANYA user" is entirely on us, and this used to not do it at all.

    The tricky part isn't blocking a second subscribe once the first is
    genuinely active (entitlement_service already gates that at the UI/
    plan-card level) — it's the race in between: Checkout succeeds, but
    Razorpay's webhook (the only thing that writes status="active") can
    take a few seconds to arrive. A second click, a second tab, or an
    impatient retry in that exact window used to see no subscription on
    file yet and just create another one. Closing that requires recording
    SOMETHING the instant a subscription is created, not waiting for the
    webhook — hence the immediate upsert_subscription(status="created")
    below, the one deliberate exception to this module's "only the
    webhook writes subscription state" rule (see the module docstring).

    A "created" row blocks a retry only while it's recent
    (PENDING_SUBSCRIBE_COOLDOWN) — an older one is presumably an abandoned
    Checkout (see the "modal dismissed" scenario elsewhere in this
    codebase) and shouldn't permanently lock someone out of ever
    subscribing. An "active" row always blocks, full stop — that's a real
    subscription, not something a retry should ever duplicate.

    Known residual gap: if razorpay_create_subscription succeeds but the
    upsert_subscription commit right after it fails (a genuine DB outage
    at that exact moment), this guard has nothing to catch on the next
    attempt. Accepted as a rare partial-failure window, not solved here —
    would need a full outbox/saga pattern to close completely.

    One deliberate exception to the "active always blocks" rule: a user
    who cancelled and changed their mind before the cycle actually ended
    (existing.cancel_scheduled) is subscribing to the SAME plan they
    already have, not creating a genuine duplicate — see
    _resume_subscription for why that needs its own path rather than
    just letting this fall through to a normal create."""
    try:
        plan = PLANS.get(plan_key)
        if plan is None or plan.razorpay_plan_id is None:
            raise BadRequestError(f"'{plan_key}' is not a purchasable plan")

        existing = await get_subscription_by_user(db, user.user_id)
        if existing is not None:
            if existing.status == "active":
                if existing.cancel_scheduled and existing.product_id == plan.razorpay_plan_id:
                    return await _resume_subscription(db, user, existing)
                raise BadRequestError("You already have an active subscription — manage it from the Account page.")
            # naive_utc(): existing.updated_at round-trips timezone-aware
            # from Postgres (every timestamp column here uses
            # DateTime(timezone=True)) but datetime.utcnow() is naive —
            # comparing them directly raised "can't compare offset-naive
            # and offset-aware datetimes" the first time this path was hit
            # by a real (not just test-mode) pending subscription. See
            # utils/time_utils.py's module docstring for the full story.
            if existing.status == "created" and naive_utc(existing.updated_at) > datetime.utcnow() - PENDING_SUBSCRIBE_COOLDOWN:
                raise BadRequestError("A subscription attempt is already in progress — finish that checkout, or wait a few minutes and try again.")

        result = await razorpay_create_subscription(plan.razorpay_plan_id, user.user_id)
        await upsert_subscription(db, user.user_id, None, "created", None, result["id"], cancel_scheduled=False)
        return CreateSubscriptionData(subscription_id=result["id"], razorpay_key_id=settings.razorpay_key_id)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to create Razorpay subscription: {exc}") from exc


async def _resume_subscription(db: AsyncSession, user: User, existing) -> CreateSubscriptionData:
    """Undoes a cancellation before the cycle actually ends — the one
    thing Razorpay itself has no API for (confirmed directly against
    their docs: the only "undo" endpoint reverses a scheduled *plan
    change*, not a cancellation — see billing_service's earlier
    reconciliation-job work for that same finding). The only way to
    genuinely keep billing going is a fresh subscription; the trick is
    doing it without a gap or a double charge.

    Fetches the still-active old subscription's current_end and creates a
    new one for the SAME plan with start_at set to that same date — so
    the new mandate picks up exactly where the old one would have
    stopped. The old one is left alone (already scheduled to cancel on
    its own; no need to touch it again) rather than force-cancelled now,
    since it's still correctly covering access in the meantime.

    Needs the customer's explicit authorization like any new mandate
    (returns requires_checkout via CreateSubscriptionData's normal
    shape — this reuses the exact same Checkout flow as a first-time
    subscribe on the website, no new UI concept for the client).

    Deliberately does NOT write anything to our own DB here (unlike
    create_subscription's own immediate "created" placeholder) — writing
    cancel_scheduled=False now, before the new mandate is actually
    confirmed, would hide "Resume subscription" the moment this call
    returns even if the customer then abandons Checkout, while the real
    (untouched) old subscription keeps heading for cancellation regardless.
    Leaving this row alone means cancel_scheduled only ever flips to False
    once _apply_subscription_state sees the NEW subscription id for real
    (a genuine webhook/reconciliation confirmation) — abandon the
    Checkout, and the next visit still correctly offers to resume, because
    nothing here claimed it was already done. The accepted trade-off is
    the same class of residual double-click race create_subscription's
    own docstring already accepts (two tabs, not two clicks in one —
    the frontend's busy-button state already covers the common case)."""
    current = await razorpay_fetch_subscription(existing.provider_subscription_id)
    current_end = current.get("current_end")
    new_sub = await razorpay_create_subscription(existing.product_id, user.user_id, start_at=current_end)
    return CreateSubscriptionData(subscription_id=new_sub["id"], razorpay_key_id=settings.razorpay_key_id)


async def change_plan(db: AsyncSession, user: User, new_plan_key: str) -> ChangePlanData:
    """Moves an already-subscribed user directly to a different paid tier —
    branches into two genuinely different flows depending on direction,
    since only one of them can be done without collecting new money:

    DOWNGRADE (e.g. Photosynthesis PhD -> Green Thumb): immediate feature
    change, no refund, existing data untouched. Two independent things
    happen, on purpose:
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

    UPGRADE (e.g. Green Thumb -> Photosynthesis PhD): the opposite
    trade-off, by explicit product decision — charges the new plan's full
    price immediately (no proration for the old plan's unused days, same
    "no refund either direction" spirit as the downgrade path) and needs
    one more Checkout confirmation to actually collect it, so this returns
    requires_checkout=True instead of a done deal. See
    _upgrade_plan's own docstring for the mechanics.
    """
    try:
        new_plan = PLANS.get(new_plan_key)
        if new_plan is None or new_plan.razorpay_plan_id is None:
            raise BadRequestError(f"'{new_plan_key}' is not a purchasable plan")

        current_plan = plan_for_user(user)
        if plan_rank(new_plan_key) == plan_rank(current_plan.key):
            raise BadRequestError(f"You're already on {new_plan.display_name}.")

        sub = await get_subscription_by_user(db, user.user_id)
        if sub is None or not sub.provider_subscription_id:
            raise NotFoundError("No active subscription found for this account")

        if plan_rank(new_plan_key) > plan_rank(current_plan.key):
            return await _upgrade_plan(user, sub.provider_subscription_id, new_plan)

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


async def _upgrade_plan(user: User, old_subscription_id: str, new_plan: PlanConfig) -> ChangePlanData:
    """Explicit product decision: an upgrade charges the new plan's full
    price right away rather than prorating the old plan's unused days —
    the mirror image of the downgrade path's own no-refund policy, just in
    the other direction. Cancels the OLD subscription immediately (not at
    cycle end — it's being replaced right now, not merely scheduled to
    lapse later) before creating the new one, same cancel-then-create
    order _change_plan_upi_fallback already uses for its structurally
    identical "needs a fresh mandate" flow — just with cancel_at_cycle_end
    =False and no deferred start_at, since this starts billing today.

    Unlike a downgrade, feature access does NOT flip until the new
    subscription's payment actually confirms via the webhook — granting a
    HIGHER tier before payment is verified would violate the "never trust
    the client, only the signed webhook" trust boundary this module holds
    everywhere else (see its own module docstring). The website polls for
    that the same way it already does for a first-time subscribe.

    If the customer abandons the new Checkout, they're simply left with no
    active paid subscription (the old one is already gone) — recoverable
    by subscribing again normally, not a money-loss risk either way; the
    same "fails open, never grants anything unpaid" shape as the downgrade
    UPI fallback above."""
    await razorpay_cancel_subscription(old_subscription_id, cancel_at_cycle_end=False)
    new_sub = await razorpay_create_subscription(new_plan.razorpay_plan_id, user.user_id)

    return ChangePlanData(
        plan=new_plan.key,
        message=f"Complete checkout to switch to {new_plan.display_name} for ₹{new_plan.price_inr}/month, starting today.",
        requires_checkout=True,
        subscription_id=new_sub["id"],
        razorpay_key_id=settings.razorpay_key_id,
    )


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

    await upsert_subscription(db, user.user_id, new_plan.razorpay_plan_id, "active", user.subscription_expires_at, new_sub["id"], cancel_scheduled=False)
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
    else in this module) — cancel_scheduled is the one deliberate
    exception, same spirit as create_subscription's immediate "created"
    write: purely informational (lets the website show "Resume
    subscription" — see _resume_subscription), never itself read by the
    entitlement engine, so writing it directly here doesn't touch the
    trust boundary that actually governs access."""
    try:
        sub = await get_subscription_by_user(db, user.user_id)
        if sub is None or not sub.provider_subscription_id:
            raise NotFoundError("No subscription found for this account")
        await razorpay_cancel_subscription(sub.provider_subscription_id, cancel_at_cycle_end=True)
        await upsert_subscription(db, user.user_id, sub.product_id, sub.status, sub.expires_at, cancel_scheduled=True)
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

        sub = await _apply_subscription_state(db, user, subscription_entity)
        await create_webhook_event(db, sub.subscription_id, event_type, external_event_id, event)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to process Razorpay webhook: {exc}") from exc


async def _apply_subscription_state(db: AsyncSession, user: User, subscription_entity: Dict[str, Any]):
    """The one place a raw Razorpay subscription entity turns into our own
    DB's subscription state — shared by process_razorpay_webhook (the
    normal, fast path, fed by Razorpay's push) and reconcile_subscriptions
    (the periodic safety net below, fed by a direct GET instead) so the
    two can never quietly diverge in how they interpret the same data. A
    subscription can never end up in a state the webhook itself wouldn't
    have produced — reconciliation isn't a second, looser trust boundary,
    it's the identical write path on a different trigger.

    Also the one place cancel_scheduled gets reset back to False on a
    normal (non-cancel_subscription-initiated) write: whenever the
    incoming event is for a DIFFERENT provider_subscription_id than what
    was previously stored, some fresh subscription (a resume, an upgrade,
    a downgrade's UPI fallback) has taken over — whatever cancellation
    applied to the PREVIOUS subscription is no longer relevant to this
    new one. The same subscription id recurring (a routine renewal/status
    event) leaves cancel_scheduled exactly as cancel_subscription last
    set it — this function has no way to know a scheduled cancellation
    still stands or not from the entity alone, so it simply never touches
    a flag it didn't just make stale.

    And the one guard against a real race the resume/upgrade/downgrade-
    UPI-fallback flows all introduced: each of those cancels an OLD
    subscription and creates a NEW one, switching this row to the new id
    right away — but the OLD subscription's own final "cancelled" webhook
    doesn't fire until ITS cancellation actually takes effect, which for
    a cycle-end cancel (the downgrade fallback, an ordinary resume) can be
    WEEKS after the switch already happened. If that stale event lands
    after a newer subscription has taken over, blindly applying it would
    stomp a correctly-active user back to "expired". A terminal event for
    an id that no longer matches what's on file is exactly that — a
    superseded subscription's late echo, not real news — so it's recorded
    (the caller's create_webhook_event still runs) but never applied."""
    razorpay_plan_id = subscription_entity.get("plan_id")
    razorpay_subscription_id = subscription_entity.get("id")
    status = status_for(subscription_entity.get("status", ""))
    current_end = subscription_entity.get("current_end")
    expires_at = datetime.fromtimestamp(current_end, tz=timezone.utc) if current_end else None

    # razorpay_plan_id must be one we currently recognize (see plans.py) —
    # otherwise don't touch the user's plan association (an unrecognized/
    # retired plan_id shouldn't silently downgrade them), but still honor
    # an "expired" status regardless (cancellation always applies).
    recognized_plan_id = razorpay_plan_id if razorpay_plan_id in RAZORPAY_PLAN_ID_TO_PLAN else None

    existing = await get_subscription_by_user(db, user.user_id)
    superseded = (
        existing is not None
        and existing.provider_subscription_id
        and existing.provider_subscription_id != razorpay_subscription_id
    )
    if status == "expired" and superseded:
        return existing

    cancel_scheduled = False if (existing is None or superseded) else None

    sub = await upsert_subscription(
        db, user.user_id, recognized_plan_id or razorpay_plan_id, status, expires_at, razorpay_subscription_id,
        cancel_scheduled=cancel_scheduled,
    )
    if recognized_plan_id is not None or status == "expired":
        await update_subscription_status(db, user, status, expires_at, recognized_plan_id)
    return sub


async def reconcile_subscriptions(db: AsyncSession) -> int:
    """Periodic safety net (see main.py's lifespan) — re-fetches every
    subscription we currently believe is still current directly from
    Razorpay and re-applies its real state through _apply_subscription_
    state, the exact same write path process_razorpay_webhook uses.

    Why this exists: the webhook is the fast, normal path (usually
    landing within seconds of a real event), but it depends on Razorpay's
    delivery actually reaching this backend. Razorpay retries a failed
    delivery, but only for a bounded window — and this backend can be
    unreachable for stretches of its own (a free-tier Render dyno asleep
    exactly when a webhook fires, a deploy restart mid-delivery, a
    transient DB outage). A permanently-lost webhook would otherwise
    leave a subscription's status stale indefinitely, with nothing ever
    correcting it — the exact gap flagged as unsolved when this billing
    system was first mapped out end to end.

    Scoped to non-expired rows only (status != "expired") — reconciling
    every subscription ever created, forever, would grow the API call
    volume unboundedly for no real benefit; a subscription already
    believed over is exceedingly unlikely to have silently come back, and
    the actual risk this protects against — still-billing-in-reality
    access we don't know to grant, or the reverse — only lives in rows we
    currently think are still active or pending.

    A failure on one subscription never aborts the batch — logged,
    skipped, picked up again on the next tick, same "don't let one bad
    row take down the whole run" shape as account_service.sweep_expired_
    deletions. Returns how many rows actually changed state, for the
    caller to log."""
    pending = await list_subscriptions_for_reconciliation(db)
    corrected = 0
    for sub in pending:
        if not sub.provider_subscription_id:
            continue
        try:
            user = await get_user_by_id(db, sub.user_id)
            if user is None:
                continue
            entity = await razorpay_fetch_subscription(sub.provider_subscription_id)
            before = (user.subscription_status, user.subscription_product_id)
            await _apply_subscription_state(db, user, entity)
            if (user.subscription_status, user.subscription_product_id) != before:
                corrected += 1
        except Exception as exc:
            logger.error(f"Reconciliation failed for subscription {sub.subscription_id} (user {sub.user_id}): {exc}")
    return corrected
