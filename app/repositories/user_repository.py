"""
Repository layer for USERS / USER_PREFERENCES. Repositories only talk to the
database — no business rules, no HTTP concerns. Every function is async,
typed, and wrapped in try/except so a raw DB driver exception never leaks
past this layer.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, InternalServerError
from app.models.user import User, UserPreference


async def get_user_by_provider_id(db: AsyncSession, provider: str, provider_id: str) -> Optional[User]:
    try:
        result = await db.execute(
            select(User).where(User.provider == provider, User.provider_id == provider_id, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()
    except Exception as exc:
        raise InternalServerError(f"Failed to look up user: {exc}") from exc


async def get_user_by_provider_id_including_deleted(db: AsyncSession, provider: str, provider_id: str) -> Optional[User]:
    """Same lookup as get_user_by_provider_id but without the deleted_at
    filter — used by the 24h restore-window flow (auth_service.sign_in /
    restore_account / restart_account), which specifically needs to find a
    just-deleted row that get_user_by_provider_id correctly can't see."""
    try:
        result = await db.execute(select(User).where(User.provider == provider, User.provider_id == provider_id))
        return result.scalar_one_or_none()
    except Exception as exc:
        raise InternalServerError(f"Failed to look up user: {exc}") from exc


async def get_user_by_id(db: AsyncSession, user_id: str) -> Optional[User]:
    try:
        result = await db.execute(select(User).where(User.user_id == user_id, User.deleted_at.is_(None)))
        return result.scalar_one_or_none()
    except Exception as exc:
        raise InternalServerError(f"Failed to fetch user: {exc}") from exc


async def create_user(
    db: AsyncSession, provider: str, provider_id: str, email: Optional[str], is_guest: bool, name: Optional[str] = None
) -> User:
    try:
        user = User(provider=provider, provider_id=provider_id, email=email, is_guest=is_guest, name=name)
        db.add(user)
        await db.flush()
        # A brand-new user should always start with default preferences.
        db.add(UserPreference(user_id=user.user_id))
        await db.commit()
        await db.refresh(user)
        return user
    except IntegrityError:
        # The unique constraint on provider_id fired — two different real
        # situations land here, both needing to NOT be a raw 500:
        #
        # 1. A genuine timing collision: sign_in's check-then-insert
        #    (get_user_by_provider_id, then this) has a race window — two
        #    concurrent sign-ins for the same not-yet-existing identity (a
        #    double-tap, or a slow first request that got retried) can both
        #    pass that check before either commits, so the second INSERT
        #    here hits the constraint. Harmless — recover by returning
        #    whichever row just won, so this login still just succeeds.
        #
        # 2. This identity has a deleted-but-still-within-its-24h-restore-
        #    window account (see auth_service.ACCOUNT_RESTORE_WINDOW) —
        #    soft_delete_user deliberately leaves provider_id intact
        #    during that window so a restore has something to restore.
        #    auth_service.sign_in/restart_account both check for and
        #    handle this case BEFORE ever calling create_user, so landing
        #    here means that check raced with something else — a rare
        #    fallback, not the normal path. Still a clear, honest error
        #    rather than a crash or a silent undelete either way.
        await db.rollback()
        result = await db.execute(select(User).where(User.provider == provider, User.provider_id == provider_id))
        conflicting = result.scalar_one_or_none()
        if conflicting is not None and conflicting.deleted_at is not None:
            raise BadRequestError(
                "This account was recently deleted. Please sign in again to see restore options, or use a different account."
            )
        if conflicting is not None:
            return conflicting
        raise InternalServerError("Failed to create user: provider_id already exists but could not be re-fetched")
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to create user: {exc}") from exc


async def link_identity(
    db: AsyncSession, user: User, provider: str, provider_id: str, email: Optional[str], name: Optional[str] = None
) -> User:
    try:
        user.provider = provider
        user.provider_id = provider_id
        if email:
            user.email = email
        if name:
            user.name = name
        user.is_guest = False
        await db.commit()
        await db.refresh(user)
        return user
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to link identity: {exc}") from exc


async def increment_token_version(db: AsyncSession, user: User) -> None:
    """Called on signout — invalidates every JWT issued before this point."""
    try:
        user.token_version += 1
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to invalidate session: {exc}") from exc


async def update_subscription_status(
    db: AsyncSession, user: User, status: str, expires_at: Optional[datetime], product_id: Optional[str] = None
) -> None:
    """Writes the denormalized subscription fields on USERS, kept in sync
    whenever the SUBSCRIPTIONS table changes (purchase verify or webhook).
    product_id is what plans.plan_for() uses to tell Green Thumb apart from
    Photosynthesis PhD — both report status="active", only the product
    differs."""
    try:
        user.subscription_status = status
        user.subscription_expires_at = expires_at
        user.subscription_product_id = product_id
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to update subscription status: {exc}") from exc


async def soft_delete_user(db: AsyncSession, user: User) -> None:
    """Soft delete, not a hard one — subscription/payment history
    (subscription_status, subscription_product_id, ...) needs to survive
    for billing records even after the account itself is gone.

    provider_id is deliberately left INTACT here (unlike this function's
    old behavior) — the 24h restore window (see
    auth_service.ACCOUNT_RESTORE_WINDOW) needs it to still recognize this
    identity if the same person signs back in within that window.
    finalize_deletion below does the actual mangling, once that window
    has genuinely closed (or the user explicitly abandons it early via
    POST /auth/restart) — only then is the real provider_id actually
    freed up for a genuinely fresh signup."""
    try:
        # Naive UTC, matching every other timestamp in this codebase
        # (User.created_at/updated_at, etc. all use datetime.utcnow) —
        # not datetime.now(timezone.utc). SQLite doesn't actually preserve
        # tzinfo on read-back regardless of the column's timezone=True, so
        # a value stored aware comes back naive later; comparing that
        # against an aware "now" elsewhere (see auth_service._utcnow)
        # raises "can't compare offset-naive and offset-aware datetimes" —
        # staying naive throughout sidesteps the mismatch entirely.
        user.deleted_at = datetime.utcnow()
        user.is_active = False
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to delete account: {exc}") from exc


async def restore_user(db: AsyncSession, user: User) -> User:
    """Undoes soft_delete_user within the restore window — see
    auth_service.restore_account, the only caller."""
    try:
        user.deleted_at = None
        user.is_active = True
        await db.commit()
        await db.refresh(user)
        return user
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to restore account: {exc}") from exc


async def finalize_deletion(db: AsyncSession, user: User) -> None:
    """The provider_id-mangling step soft_delete_user used to do
    immediately — now deferred until the 24h restore window has actually
    closed, or the user explicitly gives it up early (see
    auth_service.sign_in and restart_account, the only callers).
    Idempotent: mangling an already-mangled provider_id is harmless."""
    try:
        user.provider_id = f"deleted:{user.user_id}"  # user_id is a UUID PK, so this is guaranteed unique on its own
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to finalize account deletion: {exc}") from exc


async def get_preference(db: AsyncSession, user_id: str) -> Optional[UserPreference]:
    try:
        result = await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
        return result.scalar_one_or_none()
    except Exception as exc:
        raise InternalServerError(f"Failed to fetch preferences: {exc}") from exc


async def upsert_preference(db: AsyncSession, user_id: str, reminders_enabled: bool) -> UserPreference:
    try:
        pref = await get_preference(db, user_id)
        if pref is None:
            pref = UserPreference(user_id=user_id, reminders_enabled=reminders_enabled)
            db.add(pref)
        else:
            pref.reminders_enabled = reminders_enabled
        await db.commit()
        await db.refresh(pref)
        return pref
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to update preferences: {exc}") from exc
