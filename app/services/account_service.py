import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, InternalServerError, NotFoundError
from app.models.user import User
from app.repositories.user_repository import (
    finalize_deletion,
    get_preference,
    list_users_pending_finalization,
    soft_delete_user,
    upsert_preference,
)
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
    """Soft-deletes the account — does NOT touch the Razorpay subscription
    yet. That's deliberate: see sweep_expired_deletions below for why
    cancelling billing only happens once the 24h restore window has
    genuinely closed, never at delete time itself.

    Reports restorable_until so the client can tell the user exactly how
    long they have to change their mind — see auth_service.sign_in/
    restore_account for what actually happens if they sign back in within
    that window (or explicitly abandon it early via POST /auth/restart)."""
    try:
        await soft_delete_user(db, user)
        return DeleteAccountData(restorable_until=user.deleted_at + ACCOUNT_RESTORE_WINDOW)
    except Exception as exc:
        raise InternalServerError(f"Failed to delete account: {exc}") from exc


async def sweep_expired_deletions(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """Run periodically in the background (see main.py's lifespan) — finds
    every account whose 24h restore window has genuinely closed and whose
    deletion hasn't been finalized yet, cancels any Razorpay subscription
    still on it, and finalizes the deletion.

    Why a sweep instead of acting at delete time: cancelling a subscription
    the moment someone hits "delete account" is unsafe for the business —
    Razorpay has no "un-cancel" (confirmed against their own API docs: the
    only reversal endpoint undoes a scheduled *plan change*, not a
    cancellation), so a user who deletes by mistake or on impulse and
    restores minutes later would permanently lose their subscription's
    auto-renewal even though they never actually wanted to leave. Waiting
    until the window has DEFINITELY closed means a restore within the
    window is never touched at all — not "unlikely to be affected", simply
    never in scope, since cancellation hasn't happened yet.

    Why not finalize_deletion's existing lazy trigger instead: that only
    fires if the same identity happens to sign in again after the window
    closes — an account that's deleted and never revisited would never
    trigger it, which is exactly how this went unhandled before. This
    sweep is the reliable path; the lazy trigger stays as a harmless,
    idempotent no-op if this already got there first (mangling an
    already-mangled provider_id, or cancelling an already-cancelled
    subscription, are both safe — see finalize_deletion and
    billing_service.cancel_subscription).

    Unbounded on how far back it looks (no upper age limit on the query)
    so a long gap in this service actually running — a free-tier Render
    dyno can sleep for hours between requests — never permanently misses
    an account; whatever accumulated gets caught on the very next tick.
    Each account drops out of future queries the moment it's finalized
    here, so a long-idle deleted account is only ever processed once, not
    repeatedly re-attempted forever."""
    cutoff = (now or datetime.utcnow()) - ACCOUNT_RESTORE_WINDOW
    pending = await list_users_pending_finalization(db, cutoff)
    for user in pending:
        try:
            await cancel_subscription(db, user)
        except NotFoundError:
            pass  # no active subscription — the common case
        except AppException as exc:
            logger.error(f"Sweep: failed to cancel subscription for {user.user_id}: {exc.message}")
        try:
            await finalize_deletion(db, user)
        except AppException as exc:
            logger.error(f"Sweep: failed to finalize deletion for {user.user_id}: {exc.message}")
    return len(pending)
