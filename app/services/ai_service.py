from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException, InternalServerError, RateLimitedError
from app.models.user import User
from app.repositories.diagnosis_read_repository import get_latest_diagnosis_for_plant
from app.repositories.diagnosis_repository import count_ai_calls_today, create_diagnosis_log, log_ai_call
from app.schemas.ai import DiagnoseData, DiagnoseRequest, IdentifyData, LatestDiagnosisData
from app.services.entitlement_service import check_diagnose_limit, check_identification_limit
from app.services.plant_service import check_plant_limit, get_plant_for_user
from app.utils.ai_provider import diagnose_plant as call_ai_diagnose
from app.utils.ai_provider import identify_plant as call_ai_identify

settings = get_settings()


async def get_latest_diagnosis(db: AsyncSession, user: User, plant_id: str) -> LatestDiagnosisData:
    """Reads the most recent stored diagnosis for a plant — zero AI calls.
    This is what lets a user re-view a past result without spending money
    or their daily rate-limit quota on something they already have."""
    try:
        await get_plant_for_user(db, user, plant_id)  # ownership check, raises 403/404 as needed
        entry = await get_latest_diagnosis_for_plant(db, plant_id)
        if entry is None:
            return LatestDiagnosisData(has_diagnosis=False)
        return LatestDiagnosisData(
            has_diagnosis=True,
            confidence=entry.confidence,
            likely_causes=entry.likely_causes,
            recommended_action=entry.recommended_action,
            urgency=entry.urgency,
            created_at=entry.created_at,
        )
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to fetch latest diagnosis: {exc}") from exc


async def _check_rate_limit(db: AsyncSession, user: User) -> None:
    calls_today = await count_ai_calls_today(db, user.user_id)
    if calls_today >= settings.ai_daily_call_limit:
        raise RateLimitedError("Daily request limit reached — try again tomorrow")


async def identify_plant(db: AsyncSession, user: User, image_base64: str) -> IdentifyData:
    """Order matters: plan-limit checks happen before the rate-limit check
    and before the external call, so a blocked user never costs an API call
    or consumes their daily quota.

    check_identification_limit tells us whether this call should be
    charged against the one-time Garden Setup allowance (new upgrader
    populating an existing garden) or the regular recurring allowance —
    see entitlement_service for why Garden Setup is tried first."""
    try:
        await check_plant_limit(db, user)
        used_garden_setup = await check_identification_limit(db, user)
        await _check_rate_limit(db, user)

        result = await call_ai_identify(image_base64)
        await log_ai_call(db, user.user_id, "garden_setup" if used_garden_setup else "identify")
        return IdentifyData(**result, used_garden_setup=used_garden_setup)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to identify plant: {exc}") from exc


async def diagnose_plant(db: AsyncSession, user: User, request: DiagnoseRequest) -> DiagnoseData:
    try:
        await check_diagnose_limit(db, user)
        await _check_rate_limit(db, user)

        plant = await get_plant_for_user(db, user, request.plant_id)
        result = await call_ai_diagnose(
            species=plant.species or "unknown houseplant",
            full_plant_image_base64=request.full_plant_image_base64,
            closeup_image_base64=request.closeup_image_base64,
        )
        await create_diagnosis_log(
            db,
            plant_id=plant.plant_id,
            user_id=user.user_id,
            confidence=result["confidence"],
            likely_causes=result["likely_causes"],
            recommended_action=result["recommended_action"],
            urgency=result["urgency"],
        )
        await log_ai_call(db, user.user_id, "diagnose")
        return DiagnoseData(**result)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to diagnose plant: {exc}") from exc
