from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InternalServerError
from app.models.plant import AiCallLog, DiagnosisLog


async def create_diagnosis_log(
    db: AsyncSession,
    plant_id: str,
    user_id: str,
    confidence: str,
    likely_causes: List[str],
    recommended_action: str,
    urgency: str,
) -> DiagnosisLog:
    try:
        entry = DiagnosisLog(
            plant_id=plant_id,
            user_id=user_id,
            confidence=confidence,
            likely_causes=likely_causes,
            recommended_action=recommended_action,
            urgency=urgency,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return entry
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to log diagnosis: {exc}") from exc


async def count_diagnoses_this_month(db: AsyncSession, user_id: str) -> int:
    try:
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(func.count(DiagnosisLog.diagnosis_id)).where(
                DiagnosisLog.user_id == user_id, DiagnosisLog.created_at >= month_start
            )
        )
        return result.scalar_one()
    except Exception as exc:
        raise InternalServerError(f"Failed to count monthly diagnoses: {exc}") from exc


async def count_diagnoses_lifetime(db: AsyncSession, user_id: str) -> int:
    """Total ever, no time window — used only for the guest gate, since a
    guest session is a short trial, not a sustainable free tier."""
    try:
        result = await db.execute(
            select(func.count(DiagnosisLog.diagnosis_id)).where(DiagnosisLog.user_id == user_id)
        )
        return result.scalar_one()
    except Exception as exc:
        raise InternalServerError(f"Failed to count lifetime diagnoses: {exc}") from exc


async def log_ai_call(db: AsyncSession, user_id: str, call_type: str) -> None:
    try:
        db.add(AiCallLog(user_id=user_id, call_type=call_type))
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to log AI call: {exc}") from exc


async def count_ai_calls_today(db: AsyncSession, user_id: str) -> int:
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        result = await db.execute(
            select(func.count(AiCallLog.id)).where(AiCallLog.user_id == user_id, AiCallLog.created_at >= since)
        )
        return result.scalar_one()
    except Exception as exc:
        raise InternalServerError(f"Failed to count daily AI calls: {exc}") from exc
