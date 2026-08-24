from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, BadRequestError, IdentityAlreadyLinkedError, InternalServerError, NotFoundError
from app.core.security import create_session_token
from app.models.user import User
from app.repositories.user_repository import (
    create_user,
    finalize_deletion,
    get_user_by_id,
    get_user_by_provider_id,
    get_user_by_provider_id_including_deleted,
    increment_token_version,
    link_identity as link_identity_repo,
    restore_user,
)
from app.schemas.auth import LinkIdentityData, SignInData, SignInRequest
from app.utils.firebase_auth import verify_firebase_id_token

# How long a deleted account can still be restored by signing back in with
# the same identity — see account_service.delete_account (which reports
# this to the user at deletion time) and sign_in/restore_account/
# restart_account below. Deliberately not env-configurable: this is a
# product policy, not a deployment knob.
ACCOUNT_RESTORE_WINDOW = timedelta(hours=24)


def _utcnow() -> datetime:
    # Naive UTC — this codebase writes every timestamp as naive UTC (see
    # soft_delete_user), so comparisons here need to match that.
    return datetime.utcnow()


def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalizes a datetime that may or may not carry tzinfo to naive UTC
    before any arithmetic/comparison against _utcnow(). This is required
    because a DB-read value's awareness depends on the *dialect*, not on
    anything this app controls: SQLite never round-trips tzinfo (a value
    written as naive UTC reads back naive), while Postgres's
    DateTime(timezone=True) columns (every timestamp column in this schema
    uses that) round-trip as timezone-aware UTC. Comparing a fresh
    datetime.utcnow() against a DB-read value that's sometimes aware and
    sometimes not is exactly what raised "can't compare offset-naive and
    offset-aware datetimes" against production Postgres — this makes every
    such comparison safe regardless of which DB is behind it."""
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


async def _resolve_identity(request: SignInRequest) -> Tuple[str, Optional[str]]:
    """Shared by sign_in/restore_account/restart_account — verifies the
    Firebase ID token (covers Google, Apple, and any other provider
    enabled in the Firebase console) or accepts a guest device_uuid with
    no external call at all. Firebase's UID is stable per project
    regardless of which underlying provider was used, so it's stored
    directly as provider_id."""
    if request.provider == "firebase":
        if not request.identity_token:
            raise BadRequestError("identity_token is required for provider=firebase")
        verified = await verify_firebase_id_token(request.identity_token)
        return verified["provider_id"], verified["email"]
    else:  # guest
        if not request.device_uuid:
            raise BadRequestError("device_uuid is required for provider=guest")
        return request.device_uuid, None


def _session_for(user: User, is_new_user: bool) -> SignInData:
    return SignInData(
        status="signed_in",
        user_id=user.user_id,
        session_token=create_session_token(user.user_id, user.token_version),
        is_new_user=is_new_user,
        is_guest=user.is_guest,
    )


async def sign_in(db: AsyncSession, request: SignInRequest) -> SignInData:
    """Signs in an existing (active) account, or creates a new one — with
    one detour: an identity whose account was deleted less than
    ACCOUNT_RESTORE_WINDOW ago gets neither. Instead this returns
    status="restorable" with no session at all, and the client is expected
    to show a restore-or-start-fresh choice and call POST /auth/restore or
    POST /auth/restart with the same request — see those functions below."""
    try:
        provider_id, email = await _resolve_identity(request)

        existing = await get_user_by_provider_id(db, request.provider, provider_id)
        if existing:
            return _session_for(existing, is_new_user=False)

        # No active account under this identity — check for one recently
        # deleted before creating a brand-new one.
        deleted = await get_user_by_provider_id_including_deleted(db, request.provider, provider_id)
        deleted_at = _naive_utc(deleted.deleted_at) if deleted is not None else None
        if deleted_at is not None:
            restorable_until = deleted_at + ACCOUNT_RESTORE_WINDOW
            if _utcnow() < restorable_until:
                return SignInData(status="restorable", restorable_until=restorable_until)
            # Window's closed — free the identity now so create_user below
            # doesn't collide with this now-permanently-gone row.
            await finalize_deletion(db, deleted)

        new_user = await create_user(db, request.provider, provider_id, email, request.provider == "guest")
        return _session_for(new_user, is_new_user=True)
    except (BadRequestError,):
        raise
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Sign-in failed: {exc}") from exc


async def restore_account(db: AsyncSession, request: SignInRequest) -> SignInData:
    """The "restore" half of the choice sign_in's status="restorable"
    response asks the client to present — re-verifies the same identity,
    then undoes the deletion on the row it still points at. Raises if
    there's nothing restorable (already restored, window closed, or this
    identity was never deleted) rather than silently no-op'ing."""
    try:
        provider_id, _ = await _resolve_identity(request)
        deleted = await get_user_by_provider_id_including_deleted(db, request.provider, provider_id)
        deleted_at = _naive_utc(deleted.deleted_at) if deleted is not None else None
        if deleted_at is None:
            raise NotFoundError("No recently-deleted account found for this identity")
        if _utcnow() >= deleted_at + ACCOUNT_RESTORE_WINDOW:
            raise BadRequestError("The 24-hour restore window for this account has passed")
        restored = await restore_user(db, deleted)
        return _session_for(restored, is_new_user=False)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Account restore failed: {exc}") from exc


async def restart_account(db: AsyncSession, request: SignInRequest) -> SignInData:
    """The other half of the choice — explicitly gives up the restore
    window early (rather than waiting for it to lapse on its own) and
    creates a brand-new account under the same identity. Safe to call even
    after the window's already closed on its own; finalize_deletion is
    idempotent."""
    try:
        provider_id, email = await _resolve_identity(request)
        deleted = await get_user_by_provider_id_including_deleted(db, request.provider, provider_id)
        if deleted is not None and deleted.deleted_at is not None:
            await finalize_deletion(db, deleted)
        new_user = await create_user(db, request.provider, provider_id, email, request.provider == "guest")
        return _session_for(new_user, is_new_user=True)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Starting a new account failed: {exc}") from exc


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
