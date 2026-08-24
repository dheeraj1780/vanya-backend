from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import error_response, success_response, unexpected_error_response
from app.dependencies import get_current_user, get_request_id
from app.models.user import User
from app.services.entitlement_service import get_entitlement

router = APIRouter(prefix="/entitlement", tags=["Entitlement"])


@router.get("")
async def get_entitlement_endpoint(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        data = await get_entitlement(db, current_user)
        return success_response(data.model_dump(mode="json"), "Entitlement retrieved successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return unexpected_error_response("fetching entitlement", exc, trace_id)
