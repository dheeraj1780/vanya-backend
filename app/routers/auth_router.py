from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import error_response, success_response, unexpected_error_response
from app.dependencies import get_current_user, get_request_id
from app.models.user import User
from app.schemas.auth import LinkIdentityRequest, SignInRequest
from app.services.auth_service import create_web_handoff_token, link_identity, restart_account, restore_account, sign_in, sign_out

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
        return unexpected_error_response("sign-in", exc, trace_id)


@router.post("/restore")
async def restore_endpoint(
    request: SignInRequest,
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    """Called after /auth/signin returns status="restorable" — same
    request body, same identity, undoes the deletion instead of creating
    anything new."""
    try:
        data = await restore_account(db, request)
        return success_response(data.model_dump(mode="json"), "Account restored successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return unexpected_error_response("restoring account", exc, trace_id)


@router.post("/restart")
async def restart_endpoint(
    request: SignInRequest,
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    """The other half of the restorable choice — explicitly gives up the
    restore window early and starts a brand-new account under the same
    identity."""
    try:
        data = await restart_account(db, request)
        return success_response(data.model_dump(mode="json"), "New account started successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return unexpected_error_response("starting new account", exc, trace_id)


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
        return unexpected_error_response("sign-out", exc, trace_id)


@router.post("/web-handoff-token")
async def web_handoff_token_endpoint(
    current_user: User = Depends(get_current_user),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    """PaywallScreen's "Continue on vanya.app" calls this right before
    opening the website, so the browser signs in as the SAME account
    automatically instead of showing a bare picker a user could easily
    tap the wrong Google account in — see auth_service.create_web_handoff_token."""
    try:
        data = await create_web_handoff_token(current_user)
        return success_response(data.model_dump(mode="json"), "Web handoff token created successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return unexpected_error_response("creating web handoff token", exc, trace_id)


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
        return unexpected_error_response("linking account", exc, trace_id)
