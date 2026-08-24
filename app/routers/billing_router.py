from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import error_response, success_response, unexpected_error_response
from app.dependencies import get_current_user, get_request_id
from app.models.user import User
from app.schemas.billing import CreateSubscriptionRequest
from app.services.billing_service import cancel_subscription, create_subscription, get_subscription_status, process_razorpay_webhook

router = APIRouter(tags=["Billing"])


@router.post("/billing/razorpay/create-subscription")
async def create_subscription_endpoint(
    request: CreateSubscriptionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    """Called from the VANYA website (see billing_service.py's module
    docstring — never from the Flutter app, which has no purchase UI at
    all). current_user comes from the same Bearer session token the app
    uses, since the website signs in through the same POST /auth/signin
    with the same Firebase project — same user_id either way."""
    try:
        data = await create_subscription(db, current_user, request.plan)
        return success_response(data.model_dump(mode="json"), "Subscription created", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return unexpected_error_response("creating subscription", exc, trace_id)


@router.post("/billing/razorpay/cancel-subscription")
async def cancel_subscription_endpoint(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    """Called from the website's Account page. Cancels at the end of the
    current billing cycle — see billing_service.cancel_subscription."""
    try:
        await cancel_subscription(db, current_user)
        return success_response({"message": "Subscription will end at the current cycle's close."}, "Subscription cancelled", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return unexpected_error_response("cancelling subscription", exc, trace_id)


@router.get("/billing/subscription-status")
async def subscription_status_endpoint(
    current_user: User = Depends(get_current_user),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    """Read-only status check the website polls right after Razorpay
    Checkout reports success, since the webhook (the only thing that
    actually updates this) can lag by a few seconds. The Flutter app
    doesn't need this — it already gets the same info, richer, from
    GET /entitlement."""
    try:
        data = await get_subscription_status(current_user)
        return success_response(data.model_dump(mode="json"), "Subscription status retrieved", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return unexpected_error_response("fetching subscription status", exc, trace_id)


@router.post("/webhooks/razorpay")
async def razorpay_webhook_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
    x_razorpay_signature: Optional[str] = Header(default=None),
    x_razorpay_event_id: Optional[str] = Header(default=None),
) -> JSONResponse:
    """The ONLY thing allowed to change a user's subscription_status — see
    billing_service.py's trust-boundary note. Signature verified against
    the raw request body (must be read before any JSON parsing happens,
    since re-serializing would change the byte-for-byte signature input)."""
    try:
        raw_body = await request.body()
        await process_razorpay_webhook(db, raw_body, x_razorpay_signature, x_razorpay_event_id)
        return success_response({"message": "Subscription status updated."}, "Webhook processed successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return unexpected_error_response("processing Razorpay webhook", exc, trace_id)
