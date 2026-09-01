"""
ORM models for SUBSCRIPTIONS and BILLING_WEBHOOK_EVENTS. One Subscription
row per user (upserted, not per-transaction) — product_id holds the
Razorpay plan_id (see app/core/plans.py's razorpay_plan_id), written only
by billing_service.process_razorpay_webhook, the sole trusted source for
subscription state (see that module's docstring on the trust boundary).
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Subscription(Base):
    __tablename__ = "subscriptions"

    subscription_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"), unique=True, index=True, nullable=False)
    # Razorpay's own subscription id ("sub_xxx") — distinct from
    # subscription_id above (our internal row UUID). Needed to call
    # Razorpay's cancel/fetch APIs; populated as soon as it's known, either
    # from create_subscription's response or the first webhook event.
    provider_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    product_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # free | active | expired
    # True from the moment cancel_subscription schedules a cancel_at_
    # cycle_end on this row's provider_subscription_id, until either it
    # actually expires or a fresh subscription (a resume, upgrade, or
    # downgrade fallback) takes its place — see billing_service.
    # cancel_subscription and _apply_subscription_state for the two ends
    # of that lifecycle. Purely informational (surfaced to the website so
    # "Resume subscription" can show up), never itself read by the
    # entitlement engine — access is still governed entirely by status/
    # expires_at/product_id exactly as before.
    cancel_scheduled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    webhook_events: Mapped[list["BillingWebhookEvent"]] = relationship(back_populates="subscription")


class BillingWebhookEvent(Base):
    __tablename__ = "billing_webhook_events"

    webhook_event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subscription_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("subscriptions.subscription_id"), nullable=True
    )
    event_source: Mapped[str] = mapped_column(String(20), default="razorpay", nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)  # RENEWAL, CANCELLATION, EXPIRATION, etc.
    external_event_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    processing_status: Mapped[str] = mapped_column(String(20), default="processed", nullable=False)
    processing_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    subscription: Mapped[Optional["Subscription"]] = relationship(back_populates="webhook_events")
