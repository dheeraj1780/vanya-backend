"""
Single source of truth for VANYA's subscription tiers — every number in
this file (prices, plant slots, weekly/monthly AI allowances, one-time
garden-setup allowances) is the ONLY place those values are defined.
Nothing else in the backend should hardcode a limit; import PLANS or
plan_for() instead. This is what lets pricing/limits change later (after
market analysis, per the product ask this was built for) without touching
entitlement_service.py, any router, or the frontend's enforcement logic —
only this file (and its Dart mirror, lib/config/plans.dart, which is
display-only; the frontend never enforces limits itself) need to change.

Four tiers, in upgrade order:
  GUEST                -> signs in with Google ->
  PLANTIE (free)        -> upgrades ->
  GREEN_THUMB (paid)    -> upgrades ->
  PHOTOSYNTHESIS_PHD (paid)

Prices are in INR (India-first launch). Billing is Razorpay Subscriptions,
NOT Google Play Billing / RevenueCat — see billing_service.py's module
docstring for why: the app is deliberately "consumption-only" (Google
Play's reader-app exemption) so subscriptions are sold on the VANYA
website, not inside the app, which is what keeps Google's commission at
0% instead of 15-30%. razorpay_plan_id below must match a real Plan
created in the Razorpay dashboard (Subscriptions > Plans) — see that
file's TODO comment for the exact values still needed.

TIER LADDER: every allowance strictly increases up the ladder — guest <
plantie < green_thumb < photosynthesis_phd — including in the very first
period after signing up, not just cumulatively over time. This used to be
violated (Plantie's identification was 1/week against Guest's 3-lifetime,
and max_plants was 3 on both), which meant a brand-new signed-in Plantie
account looked *worse* than staying anonymous — exactly backwards for a
tier whose entire purpose is to reward creating an account. Guest's
allowances are lifetime (one anonymous trial, not an ongoing relationship);
every signed-in tier's are recurring (weekly/monthly), so even where a
signed-in tier's per-period number equals Guest's one-time number, it's
still a strictly better deal by the second period — but per-period numbers
are now also individually >= the tier below wherever that comparison makes
sense, so it never LOOKS like a downgrade to sign up.

WISHLIST: max_plants is deliberately small on every tier — it's a
*garden*, not a database, and reminders/calculators/diagnose all cost
something per active plant. But a hard cap on "plants I'm tracking" was
directly punishing curiosity: a user who wants to identify a plant they
saw at a nursery just to remember what it was shouldn't have to evict an
existing plant to do it. Every Plant row now carries a `status`
("active" | "wishlist" — see models/plant.py); wishlist rows are cheap
(no reminders scheduled, no calculators run against them) so their own
limit (wishlist_limit) is generous relative to max_plants. Identifying a
plant always costs one identification-allowance use regardless of which
list it lands in; moving a wishlist plant into the active garden later
costs a plant slot but NOT a second identification (see
plant_service.move_to_garden).

GROWTH JOURNEY: dated, named photo memories on a plant's growth timeline
(models.GrowthMemory) — a Green Thumb-and-up feature, gated by
growth_memory_limit, not available to Guest/Plantie at all (0). Green
Thumb's limit is deliberately 1 — a one-time taste of the feature (same
one-time spirit as garden_setup_identifications) rather than a real
ongoing timeline, which needs Photosynthesis PhD's unlimited allowance to
actually be useful. Same PLANT COLLECTION RULES semantics as max_plants:
cancelling a subscription never deletes or hides existing memories, it
only blocks creating new ones past whatever the current tier allows.
"""
from dataclasses import dataclass
from typing import Dict, Literal, Optional

Period = Literal["lifetime", "weekly", "monthly"]

# -1 on a FeatureAllowance.limit or PlanConfig.max_plants means "unlimited".
# Not used by any current tier, but the entitlement engine already honors
# it everywhere a limit is compared, so a future "unlimited" tier needs no
# engine changes — just a new PlanConfig entry.
UNLIMITED = -1


@dataclass(frozen=True)
class FeatureAllowance:
    limit: int
    period: Period


@dataclass(frozen=True)
class PlanConfig:
    key: str
    display_name: str
    tagline: str
    emoji: str
    price_inr: int  # 0 for the two free tiers
    billing: Literal["none", "monthly"]
    max_plants: int  # persistent slots — never reset by time, see PLANT COLLECTION RULES
    wishlist_limit: int  # persistent slots for NOT-yet-owned plants, doesn't compete with max_plants
    garden_setup_identifications: int  # one-time allowance, 0 = tier grants none
    identification: FeatureAllowance
    care_calculator: FeatureAllowance
    diagnose: FeatureAllowance
    # Growth Journey — persistent slots for dated photo memories on a
    # plant's growth timeline, same PLANT COLLECTION RULES semantics as
    # max_plants (a downgrade never deletes/hides existing memories, only
    # blocks creating new ones past this count — see
    # entitlement_service.check_growth_memory_limit). 0 = tier doesn't get
    # the feature at all. Green Thumb's 1 is deliberately a one-time taste
    # of the feature, not a real timeline — see plans.py's TIER LADDER note.
    growth_memory_limit: int
    # Razorpay Plan ID (Subscriptions > Plans in the Razorpay dashboard).
    # None for the two free tiers, which are never purchased — Plantie is
    # granted automatically on sign-in, Guest requires no purchase at all.
    razorpay_plan_id: Optional[str] = None


PLANS: Dict[str, PlanConfig] = {
    "guest": PlanConfig(
        key="guest",
        display_name="Guest",
        tagline="Try VANYA before you sign in.",
        emoji="🌾",
        price_inr=0,
        billing="none",
        max_plants=3,
        wishlist_limit=3,
        garden_setup_identifications=0,
        identification=FeatureAllowance(3, "lifetime"),
        care_calculator=FeatureAllowance(3, "lifetime"),
        diagnose=FeatureAllowance(1, "lifetime"),
        growth_memory_limit=0,
    ),
    "plantie": PlanConfig(
        key="plantie",
        display_name="Plantie",
        tagline="Start your plant journey.",
        emoji="🌱",
        price_inr=0,
        billing="none",
        max_plants=5,
        wishlist_limit=5,
        garden_setup_identifications=0,
        identification=FeatureAllowance(3, "weekly"),
        care_calculator=FeatureAllowance(3, "weekly"),
        diagnose=FeatureAllowance(1, "monthly"),
        growth_memory_limit=0,
    ),
    "green_thumb": PlanConfig(
        key="green_thumb",
        display_name="Green Thumb",
        tagline="Grow your garden with confidence.",
        emoji="🌿",
        price_inr=99,
        billing="monthly",
        max_plants=10,
        wishlist_limit=20,
        garden_setup_identifications=10,
        identification=FeatureAllowance(7, "weekly"),
        care_calculator=FeatureAllowance(7, "weekly"),
        diagnose=FeatureAllowance(2, "monthly"),
        growth_memory_limit=1,
        razorpay_plan_id="plan_TRk4u6iPqBbxmL",
    ),
    "photosynthesis_phd": PlanConfig(
        key="photosynthesis_phd",
        display_name="Photosynthesis PhD",
        tagline="For those who take plants seriously.",
        emoji="🌳",
        price_inr=199,
        billing="monthly",
        max_plants=25,
        wishlist_limit=50,
        garden_setup_identifications=25,
        identification=FeatureAllowance(10, "weekly"),
        care_calculator=FeatureAllowance(20, "weekly"),
        diagnose=FeatureAllowance(5, "monthly"),
        growth_memory_limit=UNLIMITED,
        razorpay_plan_id="plan_TRk5QYflkJTWAp",
    ),
}

# Reverse lookup used to turn a Razorpay subscription's plan_id (from the
# webhook payload) back into one of our plan keys.
RAZORPAY_PLAN_ID_TO_PLAN: Dict[str, str] = {plan.razorpay_plan_id: plan.key for plan in PLANS.values() if plan.razorpay_plan_id}

# The tier order upgrade prompts point to — "next tier up" from any given
# plan. Photosynthesis PhD has no next step (highest tier at launch, see
# product principle #13: no "Pro" tier yet).
_UPGRADE_PATH = ["guest", "plantie", "green_thumb", "photosynthesis_phd"]


def plan_rank(plan_key: str) -> int:
    """Where a plan sits on _UPGRADE_PATH — higher is better. Used by
    billing_service.change_plan to confirm a requested plan switch is
    actually a downgrade (strictly lower rank than the user's current
    plan) before allowing it to skip the normal paid-checkout flow.
    Returns -1 for an unrecognized key, which never ranks above anything
    real."""
    try:
        return _UPGRADE_PATH.index(plan_key)
    except ValueError:
        return -1


def next_tier(plan_key: str) -> Optional[PlanConfig]:
    try:
        idx = _UPGRADE_PATH.index(plan_key)
    except ValueError:
        return None
    if idx + 1 >= len(_UPGRADE_PATH):
        return None
    return PLANS[_UPGRADE_PATH[idx + 1]]


def plan_for(is_guest: bool, subscription_status: str, razorpay_plan_id: Optional[str]) -> PlanConfig:
    """The one place that decides which tier a user is actually on right
    now. Deliberately NOT cached on the user row as a standing "plan"
    value — always re-derived from (is_guest, subscription_status,
    razorpay_plan_id), both of which are themselves kept in sync with
    Razorpay's webhook events (see billing_service.py). This is what
    "entitlement state must be verified securely" means in practice:
    nothing here trusts a client-supplied plan, and there's no local field
    that could drift out of sync with Razorpay's own record."""
    if is_guest:
        return PLANS["guest"]
    if subscription_status == "active" and razorpay_plan_id in RAZORPAY_PLAN_ID_TO_PLAN:
        return PLANS[RAZORPAY_PLAN_ID_TO_PLAN[razorpay_plan_id]]
    # Signed in, but no active paid subscription (never purchased, or it
    # expired/was cancelled) — Plantie, same as immediately after sign-in.
    # Per product principle #10, this NEVER deletes plants over the new
    # lower limit; it only blocks *new* additions past it (see
    # entitlement_service.check_plant_slot_limit).
    return PLANS["plantie"]
