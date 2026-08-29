from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InternalServerError
from app.models.user import User
from app.repositories.user_repository import get_preference, soft_delete_user, upsert_preference
from app.schemas.entitlement import DeleteAccountData, PreferencesData
from app.services.auth_service import ACCOUNT_RESTORE_WINDOW


async def get_preferences(db: AsyncSession, user: User) -> PreferencesData:
    try:
        pref = await get_preference(db, user.user_id)
        # Every user gets a default row on creation, but fall back safely
        # if one is somehow missing rather than raising a 404 for a setting.
        return PreferencesData(reminders_enabled=pref.reminders_enabled if pref else True, name=user.name)
    except Exception as exc:
        raise InternalServerError(f"Failed to fetch preferences: {exc}") from exc


async def update_preferences(db: AsyncSession, user: User, reminders_enabled: bool, name: Optional[str] = None) -> PreferencesData:
    try:
        # None = leave the stored name untouched; "" (after stripping) =
        # clear it back to None; anything else replaces it. Set on the
        # ORM object here so upsert_preference's own commit picks up both
        # changes in the same transaction — no separate commit needed.
        if name is not None:
            # Cap matches the client's TextField maxLength (settings_screen.dart's
            # _editName) — was 100, trimmed to 50 as a more realistic real-name
            # length; kept in sync here as a server-side backstop regardless of
            # what any client sends.
            user.name = name.strip()[:50] or None
        pref = await upsert_preference(db, user.user_id, reminders_enabled)
        return PreferencesData(reminders_enabled=pref.reminders_enabled, name=user.name)
    except Exception as exc:
        raise InternalServerError(f"Failed to update preferences: {exc}") from exc


async def delete_account(db: AsyncSession, user: User) -> DeleteAccountData:
    """Soft-deletes the account. Does NOT cancel an active App Store/Play
    subscription — that must happen through the platform's own subscription
    management, per the OpenAPI spec's note on this endpoint.

    Reports restorable_until so the client can tell the user exactly how
    long they have to change their mind — see auth_service.sign_in/
    restore_account for what actually happens if they sign back in within
    that window (or explicitly abandon it early via POST /auth/restart)."""
    try:
        await soft_delete_user(db, user)
        return DeleteAccountData(restorable_until=user.deleted_at + ACCOUNT_RESTORE_WINDOW)
    except Exception as exc:
        raise InternalServerError(f"Failed to delete account: {exc}") from exc
