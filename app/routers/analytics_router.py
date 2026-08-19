from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.response import success_response
from app.dependencies import get_current_user, get_request_id
from app.models.user import User
from app.repositories.analytics_repository import log_event
from app.schemas.analytics import AnalyticsEventRequest
from app.services.entitlement_service import plan_for_user

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.post("/event")
async def log_event_endpoint(
    request: AnalyticsEventRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    """Fire-and-forget from the client's point of view (see
    analytics_service.dart) — always returns success even if logging
    itself fails internally, since a broken analytics pipeline should
    never surface as a user-facing error in the feature it's measuring."""
    try:
        plan = plan_for_user(current_user).key
        await log_event(db, current_user.user_id, request.event_name, plan, request.properties)
    except Exception:
        pass
    return success_response({"logged": True}, "Event recorded", 200, trace_id)
