from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InternalServerError
from app.models.analytics import AnalyticsEvent


async def log_event(
    db: AsyncSession, user_id: Optional[str], event_name: str, plan: Optional[str], properties: Optional[Dict[str, Any]]
) -> None:
    try:
        db.add(AnalyticsEvent(user_id=user_id, event_name=event_name, plan=plan, properties=properties))
        await db.commit()
    except Exception as exc:
        await db.rollback()
        # Analytics must never break the feature it's measuring — logged
        # and swallowed by the router, not raised further.
        raise InternalServerError(f"Failed to log analytics event: {exc}") from exc
