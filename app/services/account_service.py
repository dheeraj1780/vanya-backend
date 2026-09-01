import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, InternalServerError, NotFoundError
from app.models.user import User
from app.repositories.user_repository import get_preference, soft_delete_user, upsert_preference
from app.schemas.entitlement import DeleteAccountData, PreferencesData
from app.services.auth_service import ACCOUNT_RESTORE_WINDOW
from app.services.billing_service import cancel_subscription

logger = logging.getLogger("plant_companion")


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
    """Soft-deletes the account and, if there's an active Razorpay
    subscription, schedules it to cancel at the end of the current billing
    cycle — same policy as a manual cancel from the website's Account page
    (see billing_service.cancel_subscription). This used to not touch
    billing at all (a stale comment here referenced the old Play Billing/
    RevenueCat era, which never applied to Razorpay), which meant a paying
    user who deleted their account kept getting charged indefinitely —
    the subscription had no connection to an account that no longer
    existed to manage or even see it.

    Cancelling immediately (rather than waiting for the 24h restore window
    to close) is deliberately safe for a user who restores: cancel_at_
    cycle_end never revokes access early, so a restored account still has
    the subscription fully intact through whatever's already been paid
    for — it just won't auto-renew unless they resubscribe, the same
    state they'd be in had they cancelled manually and then restored.
    finalize_deletion (true permanent deletion) isn't a reliable place to
    do this instead: it only ever runs lazily, if the same identity tries
    to sign in again after the window closes — an account that's deleted
    and never revisited would never trigger it.

    A billing-side failure here (Razorpay down, no active subscription,
    etc.) never blocks the deletion itself — the account being deleted is
    the primary thing the user asked for; the subscription cancel is a
    best-effort side effect, logged but not fatal.

    Reports restorable_until so the client can tell the user exactly how
    long they have to change their mind — see auth_service.sign_in/
    restore_account for what actually happens if they sign back in within
    that window (or explicitly abandon it early via POST /auth/restart)."""
    try:
        try:
            await cancel_subscription(db, user)
        except NotFoundError:
            pass  # no active subscription to cancel — the common case
        except AppException as exc:
            logger.error(f"Failed to cancel subscription during account deletion for {user.user_id}: {exc.message}")

        await soft_delete_user(db, user)
        return DeleteAccountData(restorable_until=user.deleted_at + ACCOUNT_RESTORE_WINDOW)
    except Exception as exc:
        raise InternalServerError(f"Failed to delete account: {exc}") from exc
