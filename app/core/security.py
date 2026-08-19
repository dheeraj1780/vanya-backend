"""
Our own session JWT — distinct from the Firebase ID token, which is
verified separately in utils/firebase_auth.py and never stored or reused
after sign-in.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, TypedDict

from jose import JWTError, jwt

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError

settings = get_settings()


class SessionTokenPayload(TypedDict):
    sub: str  # user_id
    token_version: int
    exp: datetime


def create_session_token(user_id: str, token_version: int) -> str:
    """Mints a session JWT scoped to this user_id and their current
    token_version. Incrementing token_version server-side (on signout)
    invalidates every previously issued token instantly, with no session
    table required."""
    try:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
        payload = {"sub": user_id, "token_version": token_version, "exp": expire}
        return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    except Exception as exc:
        # Token creation failing is always a server bug, not a client error.
        raise RuntimeError(f"Failed to create session token: {exc}") from exc


def decode_session_token(token: str) -> SessionTokenPayload:
    """Raises UnauthorizedError (never a raw JWTError) on any failure, so
    callers never need to know about python-jose's exception types."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload  # type: ignore[return-value]
    except JWTError as exc:
        raise UnauthorizedError("Invalid or expired session token") from exc
    except Exception as exc:
        raise UnauthorizedError("Invalid or expired session token") from exc


def extract_bearer_token(authorization_header: Optional[str]) -> str:
    """Pulls the raw JWT out of an `Authorization: Bearer <token>` header."""
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise UnauthorizedError("Missing or malformed Authorization header")
    return authorization_header.removeprefix("Bearer ").strip()
