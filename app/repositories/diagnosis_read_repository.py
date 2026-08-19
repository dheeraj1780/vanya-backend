from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InternalServerError
from app.models.plant import DiagnosisLog


async def get_latest_diagnosis_for_plant(db: AsyncSession, plant_id: str) -> Optional[DiagnosisLog]:
    try:
        result = await db.execute(
            select(DiagnosisLog)
            .where(DiagnosisLog.plant_id == plant_id)
            .order_by(DiagnosisLog.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
    except Exception as exc:
        raise InternalServerError(f"Failed to fetch latest diagnosis: {exc}") from exc
