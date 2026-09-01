"""
App entrypoint. Run with: uvicorn app.main:app --reload
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, init_db
from app.core.exceptions import AppException
from app.core.response import error_response
from app.routers import (
    account_router,
    ai_router,
    analytics_router,
    auth_router,
    billing_router,
    entitlement_router,
    plants_router,
)
from app.services.account_service import sweep_expired_deletions
from app.services.billing_service import reconcile_subscriptions

settings = get_settings()
logger = logging.getLogger("plant_companion")

# How often the background sweep checks for accounts whose 24h restore
# window has closed (see account_service.sweep_expired_deletions for why
# this exists — cancelling a subscription only once deletion is genuinely
# permanent, never at delete time, so a restore is never at risk of losing
# it). Deliberately much shorter than the window itself: this only affects
# how soon a *permanently* abandoned account's billing actually stops, not
# anything restore-related, so an hour of slack here is fine.
DELETION_SWEEP_INTERVAL_SECONDS = 60 * 60

# How often every non-expired subscription gets re-fetched directly from
# Razorpay and re-reconciled (see billing_service.reconcile_subscriptions)
# — this is the safety net for a webhook that never arrived at all, so it
# runs much tighter than the deletion sweep: billing correctness staying
# wrong for up to an hour is a real problem in a way an abandoned deletion
# finalizing an hour late never is.
SUBSCRIPTION_RECONCILE_INTERVAL_SECONDS = 30 * 60


async def _run_deletion_sweep_loop() -> None:
    """Runs for the lifetime of the process. A free-tier Render dyno can
    sleep for hours between requests, silently pausing this loop along
    with everything else — harmless: sweep_expired_deletions's query has
    no upper bound on how old a pending account can be, so whatever
    accumulated while asleep is simply caught on the next tick after
    waking, not missed."""
    while True:
        await asyncio.sleep(DELETION_SWEEP_INTERVAL_SECONDS)
        try:
            async with AsyncSessionLocal() as db:
                count = await sweep_expired_deletions(db)
                if count:
                    logger.info(f"Deletion sweep: finalized {count} account(s) past their restore window.")
        except Exception as exc:
            # A single bad tick should never kill the loop -- there's
            # always another one an hour from now.
            logger.error(f"Deletion sweep tick failed: {exc}")


async def _run_subscription_reconcile_loop() -> None:
    """Runs for the lifetime of the process, same "asleep dyno just delays
    the next tick, never loses work" reasoning as the deletion sweep — see
    billing_service.reconcile_subscriptions for what each tick actually
    does and why it exists. Ticks once immediately on startup (unlike the
    deletion sweep, which only needs hourly resolution and can wait for
    its first interval) — a deploy restart is itself one of the exact
    moments a webhook could have been missed, so the first correctness
    check shouldn't wait 30 more minutes to happen."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                count = await reconcile_subscriptions(db)
                if count:
                    logger.info(f"Subscription reconciliation: corrected {count} row(s) that had drifted from Razorpay's actual state.")
        except Exception as exc:
            logger.error(f"Subscription reconciliation tick failed: {exc}")
        await asyncio.sleep(SUBSCRIPTION_RECONCILE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev-friendly: creates tables if they don't exist. Swap for Alembic
    # migrations before a real production launch — see database.py's note.
    await init_db()
    background_tasks = [
        asyncio.create_task(_run_deletion_sweep_loop()),
        asyncio.create_task(_run_subscription_reconcile_loop()),
    ]
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()


app = FastAPI(title="Plant Companion API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serves locally stored plant photos when STORAGE_BACKEND=local.
app.mount("/uploads", StaticFiles(directory=settings.local_storage_dir), name="uploads")

app.include_router(auth_router.router, prefix="/v1")
app.include_router(plants_router.router, prefix="/v1")
app.include_router(ai_router.router, prefix="/v1")
app.include_router(billing_router.router, prefix="/v1")
app.include_router(entitlement_router.router, prefix="/v1")
app.include_router(account_router.router, prefix="/v1")
app.include_router(analytics_router.router, prefix="/v1")


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """Safety net: any AppException a router forgot to catch explicitly
    still returns the uniform envelope with the correct status code,
    rather than FastAPI's default error shape."""
    trace_id = request.headers.get("request-id", "unknown")
    return error_response(exc.message, exc.error_code, exc.status_code, trace_id)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Last-resort safety net for anything truly unexpected — still returns
    the same uniform envelope instead of leaking a stack trace. The real
    exception is logged server-side only; the client only ever sees a
    generic message (this docstring used to claim that while actually
    doing the opposite — see InternalServerError in core/exceptions.py,
    which now owns this same fix for every *handled* internal error too)."""
    trace_id = request.headers.get("request-id", "unknown")
    logging.getLogger("plant_companion").error(f"Unhandled exception: {exc}", exc_info=exc)
    return error_response("Something went wrong on our end. Please try again.", "INTERNAL_SERVER_ERROR", 500, trace_id)


@app.get("/health")
async def health_check() -> dict:
    """Not part of the versioned API — just for uptime checks/load balancers."""
    return {"status": "ok"}
