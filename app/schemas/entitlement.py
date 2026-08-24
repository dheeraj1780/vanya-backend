from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class FeatureUsage(BaseModel):
    """One feature's (identification / care calculator / diagnose) usage
    against its current allowance. `limit`/`remaining` of -1 means
    unlimited (see plans.UNLIMITED) — no current tier uses this, but the
    engine and this schema both already support it for a future tier."""
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
    identification: FeatureUsage
    care_calculator: FeatureUsage
    diagnose: FeatureUsage
    garden_setup: GardenSetupData
    growth_memories: GrowthMemoryData
    # The next tier up, for "upgrade to X for Y" prompts — null at the top
    # tier (Photosynthesis PhD has nowhere further to go at launch).
    next_plan: Optional[str] = None
    next_plan_display_name: Optional[str] = None


class PreferencesData(BaseModel):
    reminders_enabled: bool


class PreferencesUpdateRequest(BaseModel):
    reminders_enabled: bool
