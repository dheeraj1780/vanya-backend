"""
Dependencies shared across every router. get_current_user is the one place
that turns a Bearer token into a validated User row — every protected
endpoint depends on it rather than re-implementing auth checks.
"""
from typing import Optional

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import UnauthorizedError
from app.core.security import decode_session_token, extract_bearer_token
from app.models.user import User
from app.repositories.user_repository import get_user_by_id


async def get_request_id(request_id: Optional[str] = Header(default=None, alias="request-id")) -> str:
    """The `request-id` header is required on every request per the API
    spec; kept optional here so a missing header degrades to a placeholder
    rather than blocking debugging, while still being documented as
    required in the OpenAPI spec."""
    return request_id or "unknown"


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolves the Bearer session JWT into a live User row. Raises
    UnauthorizedError if the token is missing, invalid, expired, or its
    token_version no longer matches (i.e. the user has since signed out)."""
    try:
        token = extract_bearer_token(authorization)
        payload = decode_session_token(token)
        user = await get_user_by_id(db, payload["sub"])
        if user is None:
            raise UnauthorizedError("User for this session no longer exists")
        if user.token_version != payload["token_version"]:
            raise UnauthorizedError("Session has been invalidated — please sign in again")
        return user
    except UnauthorizedError:
        raise
    except Exception as exc:
        raise UnauthorizedError(f"Could not validate session: {exc}") from exc
