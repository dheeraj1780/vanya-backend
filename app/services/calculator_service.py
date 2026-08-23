"""
Care Calculators — watering schedule, fertilizer dosing, and light fit for
one specific plant.

Primary path: ask Gemini (same provider as identify/diagnose) for numbers
grounded in the plant's actual species — genuinely plant-scoped, not a
generic tier lookup. If the AI provider isn't configured, the day's AI call
quota is used up, or its response fails to parse, this falls back to a
transparent rule-of-thumb formula so the feature still works; every result
carries a `reasoning` string and a `source` flag ("ai" | "formula") so
nothing is presented as more precise than it is.

Season is inferred from device location (latitude sign → hemisphere, plus
today's date) via Open-Meteo, which also supplies outdoor temperature to
tune the watering interval. Falls back to a Northern-hemisphere calendar
guess if no location was given or the weather lookup fails.
"""
import math
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException, InternalServerError
from app.models.plant import Plant
from app.models.user import User
from app.repositories.diagnosis_repository import count_ai_calls_today, log_ai_call
from app.schemas.calculator import CalculatorData, FertilizerDoseResult, LightFitResult, LocationWeatherPreview, WateringScheduleResult
from app.services.entitlement_service import check_calculator_limit
from app.services.plant_service import get_plant_for_user
from app.utils.ai_provider import calculate_care as call_ai_calculate
from app.utils.weather_client import get_current_weather, season_for_latitude

settings = get_settings()

_SEASON_WATERING_MULTIPLIER = {"spring": 1.0, "summer": 0.85, "autumn": 1.1, "winter": 1.4}
_POT_WATERING_MULTIPLIER = {"small": 0.85, "medium": 1.0, "large": 1.15}
_DEFAULT_POT_DIAMETER_CM = {"small": 12, "medium": 18, "large": 28}

_FERTILIZER_BY_DIFFICULTY = {
    # Easier / faster-growing plants tolerate (and benefit from) more
    # frequent, stronger feeding; fussier "hard" plants get a lighter,
    # less frequent dose to avoid fertilizer burn/stress.
    "easy": {"dilution": "1:10 (half strength)", "every_n_waterings": 2},
    "moderate": {"dilution": "1:15 (third strength)", "every_n_waterings": 3},
    "hard": {"dilution": "1:20 (quarter strength)", "every_n_waterings": 4},
}

_LIGHT_LEVELS = {"low": 0, "medium": 1, "bright": 2, "direct": 3}


def _pot_bucket(pot_diameter_cm: Optional[int]) -> str:
    if pot_diameter_cm is None:
        return "medium"
    if pot_diameter_cm < 15:
        return "small"
    if pot_diameter_cm <= 25:
        return "medium"
    return "large"


def _temperature_multiplier(temperature_c: Optional[float]) -> float:
    """Hotter air dries potting mix faster (water more often); colder air,
    the opposite. No-op (1.0) if temperature wasn't available."""
    if temperature_c is None:
        return 1.0
    if temperature_c >= 30:
        return 0.85
    if temperature_c >= 22:
        return 1.0
    if temperature_c >= 12:
        return 1.15
    return 1.35


def _compute_watering(plant: Plant, season: str, pot_diameter_cm: Optional[int], temperature_c: Optional[float]) -> WateringScheduleResult:
    base = plant.water_frequency_days
    bucket = _pot_bucket(pot_diameter_cm)
    multiplier = _SEASON_WATERING_MULTIPLIER[season] * _POT_WATERING_MULTIPLIER[bucket] * _temperature_multiplier(temperature_c)
    adjusted = max(1, round(base * multiplier))

    next_watering_date = plant.last_watered_at + timedelta(days=adjusted) if plant.last_watered_at else None

    diameter = pot_diameter_cm or _DEFAULT_POT_DIAMETER_CM[bucket]
    # Rule of thumb: water to roughly 15% of the pot's approximate soil
    # volume (treated as a cylinder, height ~= diameter, 80% filled with
    # mix) — a common home-gardening guideline, not a moisture-sensor
    # reading.
    volume_cm3 = math.pi * (diameter / 2) ** 2 * diameter * 0.8
    amount_ml = round(volume_cm3 * 0.15)

    reasoning = (
        f"{plant.nickname}'s base interval is every {base} day(s) (learned when it was identified). "
        f"Adjusted ×{multiplier:.2f} for {season}"
        f"{f', {temperature_c}°C' if temperature_c is not None else ''}, and a {bucket} pot"
        f"{f' ({pot_diameter_cm}cm)' if pot_diameter_cm else ''} → every {adjusted} day(s)."
    )

    return WateringScheduleResult(
        base_interval_days=base,
        adjusted_interval_days=adjusted,
        next_watering_date=next_watering_date,
        recommended_amount_ml=amount_ml,
        reasoning=reasoning,
    )


def _compute_fertilizer(plant: Plant, season: str, watering: WateringScheduleResult) -> FertilizerDoseResult:
    if season == "winter":
        return FertilizerDoseResult(
            needed=False,
            reasoning=f"{plant.nickname} is likely dormant in winter — skip fertilizing until spring growth resumes.",
        )

    difficulty = plant.care_difficulty or "moderate"
    profile = _FERTILIZER_BY_DIFFICULTY.get(difficulty, _FERTILIZER_BY_DIFFICULTY["moderate"])
    frequency_days = watering.adjusted_interval_days * profile["every_n_waterings"]

    reasoning = (
        f"Based on {plant.nickname}'s '{difficulty}' care difficulty: a {profile['dilution']} feed every "
        f"{profile['every_n_waterings']} waterings (about {frequency_days} days), mixed into a normal watering "
        f"instead of plain water."
    )

    return FertilizerDoseResult(
        needed=True,
        dilution_ratio=profile["dilution"],
        amount_ml=watering.recommended_amount_ml,
        frequency_days=frequency_days,
        reasoning=reasoning,
    )


def _plant_target_light_level(needs_lower: str) -> int:
    # \bdirect\b, not a plain substring check — "direct" is itself a
    # substring of "indirect", which would otherwise misclassify "bright
    # indirect light" as full-sun/direct instead of bright.
    if re.search(r"\bdirect\b", needs_lower) or "full sun" in needs_lower:
        return 3
    if "bright" in needs_lower:
        return 2
    if "low" in needs_lower:
        return 0
    return 1  # indirect / medium / anything unrecognized defaults to medium


def _compute_light_fit(plant: Plant, room_light: Optional[str]) -> LightFitResult:
    needs_raw = plant.light_needs
    needs_lower = (needs_raw or "").lower()

    if room_light is None or not needs_lower:
        return LightFitResult(
            plant_light_needs=needs_raw,
            room_light=room_light,
            fit="unknown",
            reasoning="Not enough information to check — this plant has no recorded light needs, or no room light level was given.",
        )

    target = _plant_target_light_level(needs_lower)
    actual = _LIGHT_LEVELS[room_light]
    diff = abs(target - actual)

    if diff == 0:
        fit = "ideal"
        reasoning = f"{plant.nickname} needs '{needs_raw}' light — a {room_light}-light room matches that well."
    elif diff == 1:
        fit = "acceptable"
        reasoning = (
            f"{plant.nickname} needs '{needs_raw}' light; a {room_light}-light room is close but not exact — "
            f"watch for stretching (too little light) or scorched leaves (too much)."
        )
    else:
        fit = "poor"
        reasoning = f"{plant.nickname} needs '{needs_raw}' light, which is a poor match for a {room_light}-light room — consider relocating it."

    return LightFitResult(plant_light_needs=needs_raw, room_light=room_light, fit=fit, reasoning=reasoning)


async def _under_daily_ai_limit(db: AsyncSession, user: User) -> bool:
    """Soft check, not a raised error — calculators degrade to the formula
    fallback instead of failing outright when the day's AI quota is used up."""
    calls_today = await count_ai_calls_today(db, user.user_id)
    return calls_today < settings.ai_daily_call_limit


async def preview_location_weather(latitude: float, longitude: float) -> LocationWeatherPreview:
    """Lightweight season+temperature lookup for a raw lat/long — lets the
    Care Calculator screen show what "auto-detect from your location" will
    actually resolve to right after location permission is granted, instead
    of only after the user presses Calculate. Deliberately separate from
    compute_care_calculators: no plant, no calculator-usage limit consumed
    (check_calculator_limit) and no AI call — this is just showing weather,
    not "using" the calculator feature."""
    try:
        weather = await get_current_weather(latitude, longitude)
        return LocationWeatherPreview(season=weather["season"], temperature_c=weather["temperature_c"])
    except AppException:
        # Open-Meteo unreachable/failed — same calendar-only fallback the
        # full calculator uses in this case, just without a temperature.
        return LocationWeatherPreview(season=season_for_latitude(latitude), temperature_c=None)


async def compute_care_calculators(
    db: AsyncSession,
    user: User,
    plant_id: str,
    pot_diameter_cm: Optional[int],
    season: Optional[str],
    room_light: Optional[str],
    latitude: Optional[float],
    longitude: Optional[float],
) -> CalculatorData:
    try:
        # Tier-limit check, distinct from the daily AI-cost safety net
        # below: this is a hard stop with an upgrade prompt (per-tier
        # weekly/lifetime allowance from plans.py), not a silent
        # formula-fallback — a Care Calculator run always "uses" the
        # feature whether or not AI was available for it.
        await check_calculator_limit(db, user)
        plant = await get_plant_for_user(db, user, plant_id)

        temperature_c: Optional[float] = None
        location_aware = False
        resolved_season = season
        if resolved_season is None and latitude is not None and longitude is not None:
            try:
                weather = await get_current_weather(latitude, longitude)
                resolved_season = weather["season"]
                temperature_c = weather["temperature_c"]
                location_aware = True
            except AppException:
                pass  # weather lookup failed — fall through to the calendar guess below
        if resolved_season is None:
            resolved_season = season_for_latitude(latitude)

        source = "formula"
        watering: Optional[WateringScheduleResult] = None
        fertilizer: Optional[FertilizerDoseResult] = None
        light_fit: Optional[LightFitResult] = None

        if settings.gemini_api_key and await _under_daily_ai_limit(db, user):
            try:
                raw = await call_ai_calculate(
                    species=plant.species,
                    light_needs=plant.light_needs,
                    care_difficulty=plant.care_difficulty,
                    water_frequency_days=plant.water_frequency_days,
                    season=resolved_season,
                    temperature_c=temperature_c,
                    pot_diameter_cm=pot_diameter_cm,
                    room_light=room_light,
                )
                adjusted = max(1, int(round(raw["adjusted_interval_days"])))
                watering = WateringScheduleResult(
                    base_interval_days=plant.water_frequency_days,
                    adjusted_interval_days=adjusted,
                    next_watering_date=(plant.last_watered_at + timedelta(days=adjusted)) if plant.last_watered_at else None,
                    recommended_amount_ml=int(round(raw["recommended_amount_ml"])),
                    reasoning=raw["watering_reasoning"],
                )
                fertilizer = FertilizerDoseResult(
                    needed=bool(raw["fertilizer_needed"]),
                    dilution_ratio=raw.get("fertilizer_dilution_ratio"),
                    amount_ml=raw.get("fertilizer_amount_ml"),
                    frequency_days=raw.get("fertilizer_frequency_days"),
                    reasoning=raw["fertilizer_reasoning"],
                )
                light_fit = LightFitResult(
                    plant_light_needs=plant.light_needs,
                    room_light=room_light,
                    fit=raw["light_fit"],
                    reasoning=raw["light_fit_reasoning"],
                )
                source = "ai"
            except Exception:
                # Any AI/parse failure silently falls back to the formula
                # path below — a calculator that sometimes errors out is
                # worse than one that's occasionally a rule-of-thumb.
                watering = fertilizer = light_fit = None

        if watering is None:
            watering = _compute_watering(plant, resolved_season, pot_diameter_cm, temperature_c)
            fertilizer = _compute_fertilizer(plant, resolved_season, watering)
            light_fit = _compute_light_fit(plant, room_light)
            source = "formula"

        # Logged once here, after either path resolves — a formula-fallback
        # result still counts as one Care Calculator use against the tier's
        # weekly allowance, same as an AI-sourced one. (Previously this only
        # logged on the AI-success branch, so formula-fallback runs — which
        # happen whenever the AI provider isn't configured or the day's AI
        # quota is used up — silently didn't count at all.)
        await log_ai_call(db, user.user_id, "calculator")

        return CalculatorData(
            plant_id=plant.plant_id,
            nickname=plant.nickname,
            species=plant.species,
            season=resolved_season,
            location_aware=location_aware,
            temperature_c=temperature_c,
            source=source,
            watering=watering,
            fertilizer=fertilizer,
            light_fit=light_fit,
        )
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to compute care calculators: {exc}") from exc
