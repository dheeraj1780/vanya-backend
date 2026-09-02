from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException, InternalServerError, RateLimitedError
from app.models.user import User
from app.repositories.diagnosis_read_repository import get_latest_diagnosis_for_plant
from app.repositories.diagnosis_repository import count_ai_calls_today, create_diagnosis_log, log_ai_call
from app.repositories.usage_repository import count_calls_since, utcnow
from app.schemas.ai import DiagnoseData, DiagnoseRequest, IdentifyData, LatestDiagnosisData
from app.services.entitlement_service import check_ai_action_limit
from app.services.plant_service import get_plant_for_user
from app.utils.ai_provider import diagnose_plant as call_ai_diagnose
from app.utils.ai_provider import identify_plant as call_ai_identify
from app.utils.ai_provider import identify_plant_by_name as call_ai_identify_by_name

settings = get_settings()

# BUG-C003: a non-real-plant result (artificial plant, random object, ...)
# never costs the user their real "identify" allowance — see the call_type
# branch below — which on its own would make identify a free-to-spam way to
# burn real Gemini API calls against nothing. This caps that specifically,
# separate from (and tighter than) the general daily AI-cost safety net
# below, so a handful of honest mis-scans never penalizes anyone, but
# repeatedly feeding it junk for the rest of the day does.
#
# Was 3 — too low in practice: this is checked BEFORE the new photo is even
# looked at, so once tripped, every subsequent attempt gets the "take a
# break" message regardless of whether that next photo is a real plant, for
# a full rolling 24h. 3 honest mis-scans (bad lighting, a cropped shot, a
# decorative fake plant, testing) shouldn't lock someone out of the app's
# core feature for a day. 10 still caps real spam/abuse while giving normal
# use plenty of room.
_MAX_NON_PLANT_ATTEMPTS_PER_DAY = 10


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


async def _check_non_plant_abuse(db: AsyncSession, user: User) -> None:
    """See _MAX_NON_PLANT_ATTEMPTS_PER_DAY's docstring. Checked up front
    (before spending an AI call) rather than only logged after the fact —
    once someone's racked up enough non-plant results today, this blocks
    the *next* identify attempt outright instead of waiting for it to also
    turn out to be junk."""
    since = utcnow() - timedelta(hours=24)
    non_plant_attempts = await count_calls_since(db, user.user_id, "identify_not_plant", since)
    if non_plant_attempts >= _MAX_NON_PLANT_ATTEMPTS_PER_DAY:
        raise RateLimitedError(
            "That's a lot of non-plant photos today \U0001F33f Take a break and try again tomorrow — or snap an actual plant!"
        )


async def identify_plant(db: AsyncSession, user: User, image_base64: str) -> IdentifyData:
    """Order matters: plan-limit checks happen before the rate-limit checks
    and before the external call, so a blocked user never costs an API call
    or consumes their daily quota.

    check_ai_action_limit tells us whether this call should be charged
    against the one-time Garden Setup allowance (new upgrader populating
    an existing garden) or the regular recurring shared ai_actions pool —
    see entitlement_service for why Garden Setup is tried first. That
    allowance is only actually spent below once the result comes back and
    turns out to be a real plant — see the log_ai_call branch.

    BUG FIX: this used to also call check_plant_limit (garden slot
    capacity) up front, blocking identification outright the moment a
    user's garden was full — even though identification has its own
    completely separate weekly/monthly allowance, and a full garden
    doesn't mean there's "nowhere to put this": wishlist has its own,
    separate capacity (check_wishlist_limit), and plenty of people just
    want to identify something without saving it anywhere at all. Garden
    capacity is still enforced -- correctly, at the moment it actually
    matters -- by create_plant (status='active') and move_to_garden, the
    only two places that actually consume a garden slot. This function
    only ever writes data on a save, never here."""
    try:
        used_garden_setup = await check_ai_action_limit(db, user, "identify")
        await _check_rate_limit(db, user)
        await _check_non_plant_abuse(db, user)

        result = await call_ai_identify(image_base64)
        is_real_plant = result.get("is_real_plant", True)
        if is_real_plant:
            await log_ai_call(db, user.user_id, "garden_setup" if used_garden_setup else "identify")
        else:
            # BUG-C003: deliberately NOT "identify"/"garden_setup" — a photo
            # Gemini itself judged isn't a real plant never costs the user
            # their real identification allowance. Still logged under its
            # own call_type so it counts toward the general daily AI-cost
            # safety net (_check_rate_limit, shared across every call type)
            # and the dedicated abuse check above.
            await log_ai_call(db, user.user_id, "identify_not_plant")
            used_garden_setup = False  # nothing was actually drawn from either allowance
        return IdentifyData(**result, used_garden_setup=used_garden_setup)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to identify plant: {exc}") from exc


async def identify_plant_by_name(db: AsyncSession, user: User, plant_name: str) -> IdentifyData:
    """The "I already know this plant" path — same allowance, same
    garden_setup priority, same call_type bookkeeping as identify_plant
    (see check_ai_action_limit's action_type="identify" and the log_ai_call
    branch below), just a text lookup instead of a vision call: no photo
    is ever sent or required. Kept as a near-duplicate of identify_plant
    rather than a shared helper because the two already diverge in one
    real way (no rate/abuse-check photo to speak of, different provider
    call) and forcing them through one function would need a branch at
    nearly every line anyway."""
    try:
        used_garden_setup = await check_ai_action_limit(db, user, "identify")
        await _check_rate_limit(db, user)
        await _check_non_plant_abuse(db, user)

        result = await call_ai_identify_by_name(plant_name)
        is_real_plant = result.get("is_real_plant", True)
        if is_real_plant:
            await log_ai_call(db, user.user_id, "garden_setup" if used_garden_setup else "identify")
        else:
            await log_ai_call(db, user.user_id, "identify_not_plant")
            used_garden_setup = False
        return IdentifyData(**result, used_garden_setup=used_garden_setup)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to look up plant by name: {exc}") from exc


async def diagnose_plant(db: AsyncSession, user: User, request: DiagnoseRequest) -> DiagnoseData:
    try:
        await check_ai_action_limit(db, user, "diagnose")
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
