"""
App entrypoint. Run with: uvicorn app.main:app --reload
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.database import init_db
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

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev-friendly: creates tables if they don't exist. Swap for Alembic
    # migrations before a real production launch — see database.py's note.
    await init_db()
    yield


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
    the same uniform envelope instead of leaking a stack trace."""
    trace_id = request.headers.get("request-id", "unknown")
    return error_response(f"An unexpected error occurred: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@app.get("/health")
async def health_check() -> dict:
    """Not part of the versioned API — just for uptime checks/load balancers."""
    return {"status": "ok"}
