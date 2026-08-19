"""
Repository layer for USERS / USER_PREFERENCES. Repositories only talk to the
database — no business rules, no HTTP concerns. Every function is async,
typed, and wrapped in try/except so a raw DB driver exception never leaks
past this layer.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InternalServerError
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
    try:
        user.deleted_at = datetime.now(timezone.utc)
        user.is_active = False
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
