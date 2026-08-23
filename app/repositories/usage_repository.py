"""
Generic usage counting against AI_CALL_LOG, used by entitlement_service to
enforce the tiered weekly/monthly/lifetime allowances (see app/core/plans.py).

One row is logged per successful identify/calculate/diagnose call (see
log_ai_call in diagnosis_repository.py, which this module doesn't
duplicate — it only adds counting queries). call_type values in use:
"identify", "garden_setup" (a one-time-allowance identify — see
entitlement_service.check_identification_limit), "diagnose", "calculator",
"identify_not_plant" (an identify call that came back as not a real plant —
see ai_service.identify_plant; counted separately so it never draws down
the real "identify"/"garden_setup" allowances, but still counts toward the
shared daily AI-cost safety net and its own dedicated abuse check).
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InternalServerError
from app.models.plant import AiCallLog


async def count_calls_since(db: AsyncSession, user_id: str, call_type: str, since: Optional[datetime]) -> int:
    """since=None means lifetime (no lower bound) — used for guest limits
    and the garden-setup one-time allowance."""
    try:
        query = select(func.count(AiCallLog.id)).where(AiCallLog.user_id == user_id, AiCallLog.call_type == call_type)
        if since is not None:
            query = query.where(AiCallLog.created_at >= since)
        result = await db.execute(query)
        return result.scalar_one()
    except Exception as exc:
        raise InternalServerError(f"Failed to count usage for {call_type}: {exc}") from exc


async def oldest_call_since(db: AsyncSession, user_id: str, call_type: str, since: datetime) -> Optional[datetime]:
    """The earliest call within the current rolling window — used to tell
    the user exactly when a weekly allowance will next free up a slot
    (oldest_call.created_at + 7 days), rather than just saying "later"."""
    try:
        result = await db.execute(
            select(func.min(AiCallLog.created_at)).where(
                AiCallLog.user_id == user_id, AiCallLog.call_type == call_type, AiCallLog.created_at >= since
            )
        )
        return result.scalar_one_or_none()
    except Exception as exc:
        raise InternalServerError(f"Failed to find oldest usage for {call_type}: {exc}") from exc


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
