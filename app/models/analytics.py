"""
ORM model for ANALYTICS_EVENTS — a minimal, self-hosted event log for the
pricing-model events the subscription spec asks to measure (see
analytics_router.py). Deliberately not a third-party SDK (Firebase
Analytics, Amplitude, Mixpanel, ...): this keeps the beta launch free of
an extra native plugin/build-risk surface, keeps every field under this
codebase's own control (so "don't collect unnecessary personal
information" is trivially true — there's no PII column to misuse), and
can be swapped for a real analytics vendor later purely inside
analytics_service.dart on the client without touching this table's
consumers.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Nullable: not currently used (every screen that fires an event has a
    # signed-in-or-guest session already, see dependencies.get_current_user),
    # but kept optional so a future pre-auth event never has to be dropped.
    user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.user_id"), index=True, nullable=True)
    event_name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    # The plan the user was on when the event fired (see product spec:
    # "Also track which plan the user was on") — denormalized onto the
    # event row itself rather than joined from USERS at query time, since
    # a user's plan at query time may differ from their plan when the
    # event actually happened.
    plan: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    properties: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
