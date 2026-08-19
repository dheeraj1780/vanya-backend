"""
Single source of truth for the response envelope used by every endpoint in
this API:
    { "success": bool, "status_code": int, "message": str,
      "data": <endpoint-specific | null>, "trace_id": str, "error": str }

Routers must always return via success_response()/error_response() below —
never build a raw dict — so the envelope can never silently drift between
endpoints.
"""
from typing import Any, Optional

from fastapi.responses import JSONResponse


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


def get_trace_id(request_id_header: Optional[str]) -> str:
    """Every request is required to send a `request-id` header (see
    dependencies.py). Falls back to a placeholder only so a missing header
    never crashes response construction — validation still rejects the
    request separately."""
    return request_id_header or "unknown"
