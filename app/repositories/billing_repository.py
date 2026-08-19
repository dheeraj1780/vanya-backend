from datetime import datetime
from typing import Optional

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


async def upsert_subscription(
    db: AsyncSession,
    user_id: str,
    product_id: Optional[str],
    status: str,
    expires_at: Optional[datetime],
    provider_subscription_id: Optional[str] = None,
) -> Subscription:
    try:
        existing = await get_subscription_by_user(db, user_id)
        if existing:
            existing.product_id = product_id
            existing.status = status
            existing.expires_at = expires_at
            if provider_subscription_id:
                existing.provider_subscription_id = provider_subscription_id
            await db.commit()
            await db.refresh(existing)
            return existing

        sub = Subscription(
            user_id=user_id,
            product_id=product_id,
            status=status,
            expires_at=expires_at,
            provider_subscription_id=provider_subscription_id,
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
