from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InternalServerError
from app.models.user import User
from app.repositories.user_repository import get_preference, soft_delete_user, upsert_preference
from app.schemas.entitlement import PreferencesData


async def get_preferences(db: AsyncSession, user: User) -> PreferencesData:
    try:
        pref = await get_preference(db, user.user_id)
        # Every user gets a default row on creation, but fall back safely
        # if one is somehow missing rather than raising a 404 for a setting.
        return PreferencesData(reminders_enabled=pref.reminders_enabled if pref else True)
    except Exception as exc:
        raise InternalServerError(f"Failed to fetch preferences: {exc}") from exc


async def update_preferences(db: AsyncSession, user: User, reminders_enabled: bool) -> PreferencesData:
    try:
        pref = await upsert_preference(db, user.user_id, reminders_enabled)
        return PreferencesData(reminders_enabled=pref.reminders_enabled)
    except Exception as exc:
        raise InternalServerError(f"Failed to update preferences: {exc}") from exc


async def delete_account(db: AsyncSession, user: User) -> None:
    """Soft-deletes the account. Does NOT cancel an active App Store/Play
    subscription — that must happen through the platform's own subscription
    management, per the OpenAPI spec's note on this endpoint."""
    try:
        await soft_delete_user(db, user)
    except Exception as exc:
        raise InternalServerError(f"Failed to delete account: {exc}") from exc
