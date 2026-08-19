from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, BadRequestError, IdentityAlreadyLinkedError, InternalServerError
from app.core.security import create_session_token
from app.models.user import User
from app.repositories.user_repository import (
    create_user,
    get_user_by_id,
    get_user_by_provider_id,
    increment_token_version,
    link_identity as link_identity_repo,
)
from app.schemas.auth import LinkIdentityData, SignInData, SignInRequest
from app.utils.firebase_auth import verify_firebase_id_token


async def sign_in(db: AsyncSession, request: SignInRequest) -> SignInData:
    """Verifies the Firebase ID token (which covers both Google and Apple
    sign-in, and any other provider enabled in the Firebase console), or
    accepts a guest device_uuid with no external call at all. Firebase's
    UID is stable per project regardless of which underlying provider was
    used, so it's stored directly as provider_id."""
    try:
        provider_id: str
        email: Optional[str] = None

        if request.provider == "firebase":
            if not request.identity_token:
                raise BadRequestError("identity_token is required for provider=firebase")
            verified = await verify_firebase_id_token(request.identity_token)
            provider_id, email = verified["provider_id"], verified["email"]
        else:  # guest
            if not request.device_uuid:
                raise BadRequestError("device_uuid is required for provider=guest")
            provider_id = request.device_uuid

        existing = await get_user_by_provider_id(db, request.provider, provider_id)
        if existing:
            return SignInData(
                user_id=existing.user_id,
                session_token=create_session_token(existing.user_id, existing.token_version),
                is_new_user=False,
                is_guest=existing.is_guest,
            )

        new_user = await create_user(db, request.provider, provider_id, email, request.provider == "guest")
        return SignInData(
            user_id=new_user.user_id,
            session_token=create_session_token(new_user.user_id, new_user.token_version),
            is_new_user=True,
            is_guest=new_user.is_guest,
        )
    except (BadRequestError,):
        raise
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Sign-in failed: {exc}") from exc


async def sign_out(db: AsyncSession, user: User) -> None:
    try:
        await increment_token_version(db, user)
    except Exception as exc:
        raise InternalServerError(f"Sign-out failed: {exc}") from exc


async def link_identity(db: AsyncSession, current_user: User, identity_token: str) -> LinkIdentityData:
    """The guest-to-account upgrade path — attaches a real Firebase-verified
    identity to the *currently authenticated* user_id instead of creating a
    second account, which is exactly what plain /auth/signin would
    otherwise do."""
    try:
        verified = await verify_firebase_id_token(identity_token)
        provider_id, email = verified["provider_id"], verified["email"]

        conflicting = await get_user_by_provider_id(db, "firebase", provider_id)
        if conflicting and conflicting.user_id != current_user.user_id:
            raise IdentityAlreadyLinkedError("This account is already linked to a different user")

        updated = await link_identity_repo(db, current_user, "firebase", provider_id, email)
        return LinkIdentityData(user_id=updated.user_id, is_guest=updated.is_guest)
    except (IdentityAlreadyLinkedError,):
        raise
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Account linking failed: {exc}") from exc
