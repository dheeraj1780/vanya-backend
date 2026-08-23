"""
Repository layer for USERS / USER_PREFERENCES. Repositories only talk to the
database — no business rules, no HTTP concerns. Every function is async,
typed, and wrapped in try/except so a raw DB driver exception never leaks
past this layer.
"""
from datetime import datetime, timezone
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


async def get_user_by_id(db: AsyncSession, user_id: str) -> Optional[User]:
    try:
        result = await db.execute(select(User).where(User.user_id == user_id, User.deleted_at.is_(None)))
        return result.scalar_one_or_none()
    except Exception as exc:
        raise InternalServerError(f"Failed to fetch user: {exc}") from exc


async def create_user(db: AsyncSession, provider: str, provider_id: str, email: Optional[str], is_guest: bool) -> User:
    try:
        user = User(provider=provider, provider_id=provider_id, email=email, is_guest=is_guest)
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
        # 2. This identity previously deleted its VANYA account. Soft
        #    delete only sets deleted_at/is_active=False — the row (and
        #    its provider_id) stays, which is exactly why
        #    get_user_by_provider_id's deleted_at filter correctly didn't
        #    find it a moment ago. Silently reviving that old row here
        #    would resurrect data the user was explicitly told was gone
        #    for good (see the delete-account confirmation copy) — so
        #    instead this is a clear, honest error, not a crash or a
        #    silent undelete.
        await db.rollback()
        result = await db.execute(select(User).where(User.provider == provider, User.provider_id == provider_id))
        conflicting = result.scalar_one_or_none()
        if conflicting is not None and conflicting.deleted_at is not None:
            raise BadRequestError(
                "This account was previously used with a VANYA account that was deleted. Please use a different account."
            )
        if conflicting is not None:
            return conflicting
        raise InternalServerError("Failed to create user: provider_id already exists but could not be re-fetched")
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to create user: {exc}") from exc


async def link_identity(db: AsyncSession, user: User, provider: str, provider_id: str, email: Optional[str]) -> User:
    try:
        user.provider = provider
        user.provider_id = provider_id
        if email:
            user.email = email
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

    provider_id is deliberately mangled here too: it's UNIQUE at the DB
    level, and leaving it alone would permanently block that same Google/
    Apple identity from ever signing in again — every future attempt would
    hit this row's still-live provider_id and either crash (see the
    IntegrityError handling in create_user, kept as a fallback for rows
    already in this state) or, worse, silently resurrect this "permanently
    deleted" account's old data. Mangling frees the real provider_id up
    for a genuinely fresh signup, while this row (and its history) stays
    exactly as it was for whatever audit/billing purposes need it."""
    try:
        user.deleted_at = datetime.now(timezone.utc)
        user.is_active = False
        user.provider_id = f"deleted:{user.user_id}"  # user_id is a UUID PK, so this is guaranteed unique on its own
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to delete account: {exc}") from exc


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
