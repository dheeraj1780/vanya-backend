from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class WateringScheduleResult(BaseModel):
    base_interval_days: int
    adjusted_interval_days: int
    next_watering_date: Optional[datetime] = None
    recommended_amount_ml: int
    reasoning: str


class FertilizerDoseResult(BaseModel):
    needed: bool
    dilution_ratio: Optional[str] = None
    amount_ml: Optional[int] = None
    frequency_days: Optional[int] = None
    reasoning: str


class LightFitResult(BaseModel):
    plant_light_needs: Optional[str] = None
    room_light: Optional[Literal["low", "medium", "bright", "direct"]] = None
    fit: Literal["ideal", "acceptable", "poor", "unknown"]
    reasoning: str


class LocationWeatherPreview(BaseModel):
    """Result of GET /plants/weather-preview — a plant-less, limit-free peek
    at what a raw lat/long resolves to, so the Care Calculator screen can
    show it the moment location is granted instead of only after a full
    Calculate run."""
    season: Literal["spring", "summer", "autumn", "winter"]
    temperature_c: Optional[float] = None


class CalculatorData(BaseModel):
    plant_id: str
    nickname: str
    species: Optional[str] = None
    season: Literal["spring", "summer", "autumn", "winter"]
    # True if latitude/longitude were given and Open-Meteo answered — season
    # then reflects the plant's actual hemisphere/date instead of the
    # Northern-hemisphere default, and temperature_c further tunes watering.
    location_aware: bool = False
    temperature_c: Optional[float] = None
    # "ai" = Gemini computed this specific to the species; "formula" = the
    # rule-of-thumb fallback (AI provider not configured, or its call/parse
    # failed) — surfaced so the UI never claims more precision than it has.
    source: Literal["ai", "formula"]
    watering: WateringScheduleResult
    fertilizer: FertilizerDoseResult
    light_fit: LightFitResult
