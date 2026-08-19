from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import error_response, success_response
from app.dependencies import get_current_user, get_request_id
from app.models.user import User
from app.schemas.entitlement import PreferencesUpdateRequest
from app.services.account_service import delete_account, get_preferences, update_preferences

router = APIRouter(tags=["Account"])


@router.get("/users/preferences")
async def get_preferences_endpoint(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        data = await get_preferences(db, current_user)
        return success_response(data.model_dump(mode="json"), "Preferences retrieved successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error fetching preferences: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@router.put("/users/preferences")
async def update_preferences_endpoint(
    request: PreferencesUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        data = await update_preferences(db, current_user, request.reminders_enabled)
        return success_response(data.model_dump(mode="json"), "Preferences updated successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error updating preferences: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@router.delete("/account")
async def delete_account_endpoint(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        await delete_account(db, current_user)
        return success_response(
            {"message": "Account and all associated data have been permanently removed."},
            "Account deleted successfully",
            200,
            trace_id,
        )
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error deleting account: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)
