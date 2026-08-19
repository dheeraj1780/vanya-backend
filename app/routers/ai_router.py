from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import error_response, success_response
from app.dependencies import get_current_user, get_request_id
from app.models.user import User
from app.schemas.ai import DiagnoseRequest, IdentifyRequest
from app.services.ai_service import diagnose_plant, identify_plant

router = APIRouter(prefix="/ai", tags=["AI Vision"])


@router.post("/identify")
async def identify_endpoint(
    request: IdentifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        data = await identify_plant(db, current_user, request.image_base64)
        return success_response(data.model_dump(mode="json"), "Plant identified successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error identifying plant: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@router.post("/diagnose")
async def diagnose_endpoint(
    request: DiagnoseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        data = await diagnose_plant(db, current_user, request)
        return success_response(data.model_dump(mode="json"), "Diagnosis completed successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error diagnosing plant: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)
