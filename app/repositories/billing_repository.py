from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InternalServerError
from app.models.billing import BillingWebhookEvent, Subscription


async def get_subscription_by_user(db: AsyncSession, user_id: str) -> Optional[Subscription]:
    try:
        result = await db.execute(select(Subscription).where(Subscription.user_id == user_id))
        return result.scalar_one_or_none()
    except Exception as exc:
        raise InternalServerError(f"Failed to fetch subscription: {exc}") from exc


async def list_subscriptions_for_reconciliation(db: AsyncSession) -> List[Subscription]:
    """Every subscription row currently believed non-expired — used only by
    billing_service.reconcile_subscriptions, the periodic safety-net re-poll
    against Razorpay's own API in case a webhook for it was ever missed."""
    try:
        result = await db.execute(select(Subscription).where(Subscription.status != "expired"))
        return list(result.scalars().all())
    except Exception as exc:
        raise InternalServerError(f"Failed to list subscriptions for reconciliation: {exc}") from exc


async def upsert_subscription(
    db: AsyncSession,
    user_id: str,
    product_id: Optional[str],
    status: str,
    expires_at: Optional[datetime],
    provider_subscription_id: Optional[str] = None,
    cancel_scheduled: Optional[bool] = None,
) -> Subscription:
    """cancel_scheduled follows the same "None = leave untouched" pattern
    as provider_subscription_id's falsy-skip above, just spelled out with
    an explicit sentinel since False is itself a meaningful value here
    (unlike provider_subscription_id, where empty/falsy never is). See
    billing_service.cancel_subscription (sets True) and
    _apply_subscription_state (resets to False when a different
    subscription id takes over) for the two ends of its lifecycle."""
    try:
        existing = await get_subscription_by_user(db, user_id)
        if existing:
            existing.product_id = product_id
            existing.status = status
            existing.expires_at = expires_at
            if provider_subscription_id:
                existing.provider_subscription_id = provider_subscription_id
            if cancel_scheduled is not None:
                existing.cancel_scheduled = cancel_scheduled
            await db.commit()
            await db.refresh(existing)
            return existing

        sub = Subscription(
            user_id=user_id,
            product_id=product_id,
            status=status,
            expires_at=expires_at,
            provider_subscription_id=provider_subscription_id,
            cancel_scheduled=cancel_scheduled if cancel_scheduled is not None else False,
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
        return sub
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to save subscription: {exc}") from exc


async def get_webhook_event_by_external_id(db: AsyncSession, external_event_id: str) -> Optional[BillingWebhookEvent]:
    try:
        result = await db.execute(
            select(BillingWebhookEvent).where(BillingWebhookEvent.external_event_id == external_event_id)
        )
        return result.scalar_one_or_none()
    except Exception as exc:
        raise InternalServerError(f"Failed to look up webhook event: {exc}") from exc


async def create_webhook_event(
    db: AsyncSession,
    subscription_id: Optional[str],
    event_type: str,
    external_event_id: str,
    payload: dict,
) -> BillingWebhookEvent:
    try:
        event = BillingWebhookEvent(
            subscription_id=subscription_id,
            event_type=event_type,
            external_event_id=external_event_id,
            payload=payload,
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)
        return event
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to record webhook event: {exc}") from exc
