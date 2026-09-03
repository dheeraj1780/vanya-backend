"""
The entitlement engine — the one place that can answer "can this user
identify/calculate/diagnose/add a plant right now?", "how much do they
have left?", and "when does it reset?". Every limit-bearing action in the
app (ai_service.identify_plant/diagnose_plant, calculator_service.
compute_care_calculators, plant_service.create_plant) calls into this
module rather than checking settings/counts itself — this is what "Do NOT
hardcode limits throughout the application" means in practice: the only
numbers live in app/core/plans.py, and the only *logic* for comparing
usage against them lives here.

Identification, Care Calculator, and Diagnose share ONE weekly (guest:
lifetime) "AI actions" pool — see plans.py's own AI ACTIONS note for why
this used to be three fully independent counters on three different
clocks, and why that was a real UX problem (six numbers on five reset
schedules) worth collapsing, not just a display simplification. All
three draw DIAGNOSE_ACTION_COST=1 from the shared pool per call now —
even weighting, see plans.py for why diagnose's old 2x weight didn't
actually hold up against real Gemini per-token pricing. Plant slots are
a separate, non-resetting concept entirely (see check_plant_slot_limit /
PLANT COLLECTION RULES in the product spec this was built from).
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, GuestSignInRequiredError, InternalServerError, PlanLimitExceededError
from app.core.plans import DIAGNOSE_ACTION_COST, FeatureAllowance, PlanConfig, next_tier, plan_for
from app.models.user import User
from app.repositories.billing_repository import get_subscription_by_user
from app.repositories.plant_repository import count_growth_memories_by_plant, count_growth_memories_by_user, count_plants_by_user
from app.repositories.usage_repository import count_calls_since, oldest_call_since, utcnow
from app.schemas.entitlement import EntitlementData, FeatureUsage, GardenSetupData, GrowthMemoryData, WishlistData

# The three call_types that draw from the shared ai_actions pool, and what
# each one costs per call — all three cost the same now (see plans.py's
# AI ACTIONS note on why diagnose's old 2x weight got dropped). Order
# doesn't matter; every use below either sums over all three or checks
# membership.
_AI_ACTION_COSTS = {"identify": 1, "calculator": 1, "diagnose": DIAGNOSE_ACTION_COST}


def plan_for_user(user: User) -> PlanConfig:
    return plan_for(user.is_guest, user.subscription_status, user.subscription_product_id)


def _period_since(period: str, now: datetime) -> Optional[datetime]:
    if period == "lifetime":
        return None
    if period == "weekly":
        # A rolling 7-day trailing window, not a fixed Mon-Sun calendar
        # week — "resets every 7 days" per the spec this was built from,
        # deliberately distinct from diagnose's calendar-month reset below.
        return now - timedelta(days=7)
    # monthly — calendar month, resets on the 1st regardless of when in
    # the month usage happened (matches the pre-existing diagnose-limit
    # behavior this replaces).
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month_start(now: datetime) -> datetime:
    if now.month == 12:
        return now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


async def _ai_action_usage(db: AsyncSession, user: User, allowance: FeatureAllowance) -> FeatureUsage:
    """Sums weighted usage across identify + calculator + diagnose (see
    _AI_ACTION_COSTS) against the one shared pool — the replacement for
    what used to be three separate _feature_usage calls against three
    separate allowances. `used` is in "action" units (currently 1:1 with
    raw call counts, since _AI_ACTION_COSTS is evenly weighted — kept as
    a weighted sum rather than a plain count in case a future action type
    ever needs a different weight again)."""
    now = utcnow()
    since = _period_since(allowance.period, now)
    used = 0
    for call_type, cost in _AI_ACTION_COSTS.items():
        used += await count_calls_since(db, user.user_id, call_type, since) * cost

    if allowance.limit < 0:  # UNLIMITED — no current tier uses this, but honored end-to-end regardless
        return FeatureUsage(used=used, limit=-1, period=allowance.period, remaining=-1, resets_at=None)

    remaining = max(0, allowance.limit - used)

    resets_at: Optional[datetime] = None
    if used > 0 and since is not None:
        # Earliest call of ANY of the three types within the window — the
        # pool resets 7 days after whichever action started using it up.
        oldest_candidates = [
            oldest
            for call_type in _AI_ACTION_COSTS
            if (oldest := await oldest_call_since(db, user.user_id, call_type, since)) is not None
        ]
        if oldest_candidates:
            oldest = min(oldest_candidates)
            if allowance.period == "weekly":
                resets_at = oldest + timedelta(days=7)
            elif allowance.period == "monthly":
                resets_at = _next_month_start(now)
        # lifetime never resets — resets_at stays None

    return FeatureUsage(used=used, limit=allowance.limit, period=allowance.period, remaining=remaining, resets_at=resets_at)


async def _garden_setup_status(db: AsyncSession, user: User, plan: PlanConfig) -> GardenSetupData:
    if plan.garden_setup_identifications <= 0:
        return GardenSetupData(total=0, used=0, remaining=0)
    # Lifetime, across ANY tier ever reached — deliberately not reset on
    # tier change. If a user used 6 of Green Thumb's 10 setup slots then
    # upgrades to Photosynthesis PhD, they get 19 more (up to 25 total),
    # not a fresh 25 — see plans.py's docstring on plan_for.
    used = await count_calls_since(db, user.user_id, "garden_setup", None)
    return GardenSetupData(total=plan.garden_setup_identifications, used=used, remaining=max(0, plan.garden_setup_identifications - used))


async def get_entitlement(db: AsyncSession, user: User) -> EntitlementData:
    try:
        plan = plan_for_user(user)
        plant_count = await count_plants_by_user(db, user.user_id, status="active")
        wishlist_count = await count_plants_by_user(db, user.user_id, status="wishlist")
        ai_actions = await _ai_action_usage(db, user, plan.ai_actions)
        garden_setup = await _garden_setup_status(db, user, plan)
        # Account-wide total, for the Plan screen's "saved across your
        # garden" line ONLY — enforcement (check_growth_memory_limit) is
        # per-plant now, via count_growth_memories_by_plant, so this
        # number and plan.growth_memory_limit below are no longer a
        # matched used/limit pair the way every other FeatureUsage-shaped
        # field is.
        growth_memory_count = await count_growth_memories_by_user(db, user.user_id)
        nxt = next_tier(plan.key)
        sub = await get_subscription_by_user(db, user.user_id)
        cancel_scheduled = bool(sub and sub.cancel_scheduled and user.subscription_status == "active")

        return EntitlementData(
            plan=plan.key,
            plan_display_name=plan.display_name,
            subscription_status=user.subscription_status,  # type: ignore[arg-type]
            expires_at=user.subscription_expires_at,
            is_guest=user.is_guest,
            plant_count=plant_count,
            plant_limit=plan.max_plants,
            wishlist=WishlistData(count=wishlist_count, limit=plan.wishlist_limit),
            ai_actions=ai_actions,
            garden_setup=garden_setup,
            growth_memories=GrowthMemoryData(count=growth_memory_count, limit=plan.growth_memory_limit),
            next_plan=nxt.key if nxt else None,
            next_plan_display_name=nxt.display_name if nxt else None,
            cancel_scheduled=cancel_scheduled,
        )
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to compute entitlement: {exc}") from exc


def _plural(n: int, singular: str, plural: Optional[str] = None) -> str:
    return f"{n} {singular}" if n == 1 else f"{n} {plural or singular + 's'}"


def _raise_feature_limit(user: User, plan: PlanConfig, feature_display: str, period_phrase: str, next_period_phrase: str) -> None:
    """Shared by the three feature checks below — a guest sees a sign-in
    prompt (they haven't shown they'll pay for anything yet), a signed-in
    user sees a real upgrade prompt naming the next tier and exactly what
    it unlocks, per the spec's LIMIT REACHED UX section. period_phrase is
    already a complete clause like "this week's plant identifications" or
    "your free diagnosis as a guest" — composed once by the caller so
    nothing gets double-appended here."""
    if user.is_guest:
        raise GuestSignInRequiredError(f"You've used {period_phrase}. Sign in to become a Plantie and keep growing your garden.")
    nxt = next_tier(plan.key)
    if nxt is None:
        raise PlanLimitExceededError(f"You've used {period_phrase}.")
    raise PlanLimitExceededError(
        f"You've used {period_phrase}. Need more {feature_display}? "
        f"{nxt.display_name} gives you {next_period_phrase}."
    )


async def check_ai_action_limit(db: AsyncSession, user: User, action_type: str) -> bool:
    """Unified check for identify/calculator/diagnose — all three now draw
    from ONE shared ai_actions pool instead of three separately-tracked
    allowances on three different clocks (see plans.py's AI ACTIONS note
    and this module's own docstring for why). `action_type` is one of
    "identify" / "calculator" / "diagnose" — used to look up this call's
    cost in _AI_ACTION_COSTS and to phrase the right message if blocked.

    Raises if blocked. Otherwise returns whether an identify call should
    be logged as "garden_setup" (True) instead of drawing from the shared
    pool at all — the garden-setup allowance is always drawn down first
    when any of it remains and only ever applies to identify, so a user
    who just upgraded never has to wait days to digitize plants they
    already own (see plans.py's garden_setup_identifications). Always
    False for calculator/diagnose calls."""
    plan = plan_for_user(user)
    cost = _AI_ACTION_COSTS[action_type]

    if action_type == "identify":
        garden_setup = await _garden_setup_status(db, user, plan)
        if garden_setup.remaining > 0:
            return True

    usage = await _ai_action_usage(db, user, plan.ai_actions)
    if usage.remaining < cost:
        if usage.period == "weekly":
            period_phrase = "this week's AI actions"
        elif usage.period == "monthly":
            period_phrase = "this month's AI actions"
        else:
            period_phrase = f"your {_plural(usage.limit, 'free AI action')} as a guest"
        nxt = next_tier(plan.key)
        next_phrase = f"{_plural(nxt.ai_actions.limit, 'AI action')} every week" if nxt else ""
        # "AI actions" doubles as the feature_display here too — identify,
        # Care Calculator, and diagnose all share the exact same pool now,
        # so there's no single narrower noun ("plants"/"diagnoses") that
        # would still be accurate regardless of which one triggered this.
        _raise_feature_limit(user, plan, "AI actions", period_phrase, next_phrase)
    return False


async def check_plant_slot_limit(db: AsyncSession, user: User) -> None:
    """Plant slots are persistent, not a recurring credit — see PLANT
    COLLECTION RULES: existing plants over a lower tier's limit are never
    deleted or hidden, this only blocks *adding new ones* past the
    current tier's max_plants."""
    plan = plan_for_user(user)
    if plan.max_plants < 0:
        return
    current_count = await count_plants_by_user(db, user.user_id)
    if current_count < plan.max_plants:
        return

    if user.is_guest:
        raise GuestSignInRequiredError(
            f"You've discovered {plan.max_plants} plants with VANYA. Sign in to become a Plantie and keep growing your garden."
        )
    nxt = next_tier(plan.key)
    if nxt is None:
        raise PlanLimitExceededError(f"Your garden is full — {plan.display_name} supports up to {plan.max_plants} plants.")
    raise PlanLimitExceededError(
        f"Your garden is full. You currently have {plan.max_plants} plants in your {plan.display_name} garden. "
        f"Upgrade to {nxt.display_name} to grow your collection up to {nxt.max_plants} plants."
    )


async def check_wishlist_limit(db: AsyncSession, user: User) -> None:
    """Wishlist slots are also persistent (not time-based), but a separate
    pool from max_plants entirely — see plans.py's WISHLIST note. A
    wishlist plant costs nothing ongoing (no reminders/calculators/
    diagnose run against it), so its limit is deliberately more generous
    than the garden's."""
    plan = plan_for_user(user)
    current_count = await count_plants_by_user(db, user.user_id, status="wishlist")
    if current_count < plan.wishlist_limit:
        return

    if user.is_guest:
        raise GuestSignInRequiredError(
            f"Your wishlist is full at {plan.wishlist_limit}. Sign in to become a Plantie and save more plants for later."
        )
    nxt = next_tier(plan.key)
    if nxt is None:
        raise PlanLimitExceededError(f"Your wishlist is full — {plan.display_name} supports up to {plan.wishlist_limit} saved plants.")
    raise PlanLimitExceededError(
        f"Your wishlist is full at {plan.wishlist_limit} plants. "
        f"Upgrade to {nxt.display_name} for up to {nxt.wishlist_limit} wishlist slots."
    )


async def check_growth_memory_limit(db: AsyncSession, user: User, plant_id: str) -> None:
    """Growth Journey slots — PER PLANT, not account-wide. Same PLANT
    COLLECTION RULES semantics as check_plant_slot_limit otherwise:
    persistent, not time-based; cancelling/downgrading never deletes or
    hides existing memories, this only blocks creating new ones past the
    current tier's growth_memory_limit. limit=0 (Guest/Plantie) means the
    feature isn't available at all, not merely "used up" — worded
    differently from the exhausted case below.

    BUG this fixes: this used to count every memory across the user's
    WHOLE garden against one shared limit — so a Green Thumb user's 5th+
    plant could never get a single memory once memories elsewhere in
    their garden had used up the shared pool of 4, even though Growth
    Journey's entire premise is a per-plant timeline. plant_id scopes the
    count to just this plant now, via count_growth_memories_by_plant —
    every plant gets its own N memories, same as every plant already gets
    its own reminders/calculators regardless of how many other plants
    exist."""
    plan = plan_for_user(user)
    if plan.growth_memory_limit < 0:  # UNLIMITED
        return
    if plan.growth_memory_limit == 0:
        if user.is_guest:
            raise GuestSignInRequiredError(
                "Growth Journey is a Green Thumb feature. Sign in, then upgrade, to start tracking a plant's growth over time."
            )
        nxt = next_tier(plan.key)
        next_phrase = f" Upgrade to {nxt.display_name} to unlock it." if nxt else ""
        raise PlanLimitExceededError(f"Growth Journey isn't available on {plan.display_name}.{next_phrase}")

    current_count = await count_growth_memories_by_plant(db, plant_id)
    if current_count < plan.growth_memory_limit:
        return

    nxt = next_tier(plan.key)
    if nxt is None:
        raise PlanLimitExceededError(f"You've used all {plan.growth_memory_limit} of this plant's growth memories on {plan.display_name}.")
    nxt_phrase = "unlimited memories per plant" if nxt.growth_memory_limit < 0 else f"up to {nxt.growth_memory_limit} memories per plant"
    raise PlanLimitExceededError(
        f"You've used all {plan.growth_memory_limit} of this plant's growth memories on {plan.display_name}. "
        f"Upgrade to {nxt.display_name} for {nxt_phrase}."
    )
