from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import error_response, success_response
from app.dependencies import get_current_user, get_request_id
from app.models.user import User
from app.schemas.auth import LinkIdentityRequest, SignInRequest
from app.services.auth_service import link_identity, sign_in, sign_out

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/signin")
async def signin_endpoint(
    request: SignInRequest,
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        data = await sign_in(db, request)
        return success_response(data.model_dump(mode="json"), "Signed in successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error during sign-in: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@router.post("/signout")
async def signout_endpoint(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        await sign_out(db, current_user)
        return success_response({"message": "Session invalidated."}, "Signed out successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error during sign-out: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@router.post("/link")
async def link_endpoint(
    request: LinkIdentityRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        data = await link_identity(db, current_user, request.identity_token)
        return success_response(data.model_dump(mode="json"), "Account linked successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error while linking account: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)
