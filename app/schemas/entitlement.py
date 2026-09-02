from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class FeatureUsage(BaseModel):
    """Usage against the shared "AI actions" allowance — identify, Care
    Calculator, and diagnose all draw from this ONE pool now (see
    entitlement_service's module docstring and plans.py's AI ACTIONS note
    for why three separate allowances on three different clocks got
    collapsed into one). `used`/`remaining` are in action units, not raw
    call counts — diagnose costs more than 1 per call (plans.
    DIAGNOSE_ACTION_COST). `limit`/`remaining` of -1 means unlimited (see
    plans.UNLIMITED) — no current tier uses this, but the engine and this
    schema both already support it for a future tier."""
    used: int
    limit: int
    period: Literal["lifetime", "weekly", "monthly"]
    remaining: int
    resets_at: Optional[datetime] = None  # None if never resets (lifetime) or nothing used yet


class GardenSetupData(BaseModel):
    """The one-time onboarding allowance granted on first upgrade to a
    paid tier, for digitizing an already-owned garden — separate from the
    recurring weekly identification allowance (see plans.py's
    garden_setup_identifications)."""
    total: int
    used: int
    remaining: int


class WishlistData(BaseModel):
    """Plants identified but not yet given a garden slot — see
    entitlement_service.check_wishlist_limit and plans.py's WISHLIST note.
    Persistent like plant_count/plant_limit, not a recurring allowance."""
    count: int
    limit: int


class GrowthMemoryData(BaseModel):
    """Growth Journey usage — see entitlement_service.check_growth_memory_limit
    and plans.py's GROWTH JOURNEY note. Persistent like plant_count/plant_limit
    (limit=0 means the current tier doesn't have the feature at all;
    limit=-1 means unlimited, see plans.UNLIMITED)."""
    count: int
    limit: int


class EntitlementData(BaseModel):
    plan: Literal["guest", "plantie", "green_thumb", "photosynthesis_phd"]
    plan_display_name: str
    subscription_status: Literal["free", "active", "expired"]
    expires_at: Optional[datetime] = None
    is_guest: bool
    plant_count: int
    plant_limit: int
    wishlist: WishlistData
    # Identify + Care Calculator + diagnose, unified — see FeatureUsage's
    # own docstring.
    ai_actions: FeatureUsage
    garden_setup: GardenSetupData
    growth_memories: GrowthMemoryData
    # The next tier up, for "upgrade to X for Y" prompts — null at the top
    # tier (Photosynthesis PhD has nowhere further to go at launch).
    next_plan: Optional[str] = None
    next_plan_display_name: Optional[str] = None
    # True while a paid subscription is active but scheduled to end at
    # cycle close (see billing_service.cancel_subscription) — the website
    # shows "Resume subscription" whenever this is true, since Razorpay
    # itself has no way to reverse the cancellation directly (see
    # billing_service._resume_subscription's docstring).
    cancel_scheduled: bool = False


class PreferencesData(BaseModel):
    reminders_enabled: bool
    # Display name — see User.name's docstring on how/when it's captured.
    # None means nothing captured yet (common for Apple, always for a
    # never-linked guest) — the client falls back to a name-less greeting.
    name: Optional[str] = None


class PreferencesUpdateRequest(BaseModel):
    reminders_enabled: bool
    # null/omitted leaves the stored name untouched; "" clears it back to
    # None; anything else replaces it (trimmed, capped at 100 chars
    # server-side) — see account_service.update_preferences.
    name: Optional[str] = None


class DeleteAccountData(BaseModel):
    """See account_service.ACCOUNT_RESTORE_WINDOW — how long the deleted
    account can still be restored by signing back in with the same
    identity, via POST /auth/restore (or explicitly abandoned early via
    POST /auth/restart)."""
    restorable_until: datetime
