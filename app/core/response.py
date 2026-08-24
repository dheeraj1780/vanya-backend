"""
Single source of truth for the response envelope used by every endpoint in
this API:
    { "success": bool, "status_code": int, "message": str,
      "data": <endpoint-specific | null>, "trace_id": str, "error": str }

Routers must always return via success_response()/error_response() below —
never build a raw dict — so the envelope can never silently drift between
endpoints.
"""
import logging
from typing import Any, Optional

from fastapi.responses import JSONResponse

logger = logging.getLogger("plant_companion")


def success_response(
    data: Any,
    message: str,
    status_code: int = 200,
    trace_id: str = "",
) -> JSONResponse:
    """Builds a uniform success envelope. `data` should already be a
    JSON-serializable dict (e.g. from a Pydantic model's .model_dump())."""
    body = {
        "success": True,
        "status_code": status_code,
        "message": message,
        "data": data,
        "trace_id": trace_id,
        "error": "",
    }
    return JSONResponse(status_code=status_code, content=body)


def error_response(
    message: str,
    error_code: str,
    status_code: int,
    trace_id: str = "",
) -> JSONResponse:
    """Builds a uniform error envelope. `data` is always null on error."""
    body = {
        "success": False,
        "status_code": status_code,
        "message": message,
        "data": None,
        "trace_id": trace_id,
        "error": error_code,
    }
    return JSONResponse(status_code=status_code, content=body)


def unexpected_error_response(context: str, exc: Exception, trace_id: str = "") -> JSONResponse:
    """Every router's outer `except Exception` should call this instead of
    building an error_response(f"...: {exc}", ...) by hand — that pattern
    puts the raw exception text (SQL, stack detail, internal paths) straight
    into the client-visible response, exactly what InternalServerError
    (core/exceptions.py) and main.py's last-resort handler already fix for
    every *other* failure path. This closes the same gap here: the real
    detail is logged server-side only, the client gets the same generic,
    safe message every genuinely-unexpected 500 already returns.
    `context` is a short phrase for the *server-side* log line only, e.g.
    "sign-in" — never shown to the client."""
    logger.error(f"Unexpected error during {context}: {exc}", exc_info=exc)
    return error_response("Something went wrong on our end. Please try again.", "INTERNAL_SERVER_ERROR", 500, trace_id)


def get_trace_id(request_id_header: Optional[str]) -> str:
    """Every request is required to send a `request-id` header (see
    dependencies.py). Falls back to a placeholder only so a missing header
    never crashes response construction — validation still rejects the
    request separately."""
    return request_id_header or "unknown"
