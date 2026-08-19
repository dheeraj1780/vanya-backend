"""
Free, keyless weather lookup (Open-Meteo) — grounds the watering calculator
in the plant's actual local season/temperature instead of a hardcoded
Northern-hemisphere calendar guess.
"""
from datetime import datetime, timezone
from typing import Optional, TypedDict

import httpx

from app.core.exceptions import ExternalProviderError

_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

_SEASON_BY_MONTH_NORTHERN = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn",
}
_OPPOSITE_SEASON = {"winter": "summer", "summer": "winter", "spring": "autumn", "autumn": "spring"}


class WeatherResult(TypedDict):
    temperature_c: float
    season: str


def season_for_latitude(latitude: Optional[float], now: Optional[datetime] = None) -> str:
    """Calendar season derived from month + hemisphere (latitude sign) —
    Open-Meteo has no "season" field, there isn't one universal value.
    No latitude (location unknown/denied) defaults to Northern hemisphere."""
    month = (now or datetime.now(timezone.utc)).month
    season = _SEASON_BY_MONTH_NORTHERN[month]
    is_southern = latitude is not None and latitude < 0
    return _OPPOSITE_SEASON[season] if is_southern else season


async def get_current_weather(latitude: float, longitude: float) -> WeatherResult:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                _OPEN_METEO_URL,
                params={"latitude": latitude, "longitude": longitude, "current": "temperature_2m"},
            )
            resp.raise_for_status()
            data = resp.json()
        return {"temperature_c": data["current"]["temperature_2m"], "season": season_for_latitude(latitude)}
    except Exception as exc:
        raise ExternalProviderError(f"Could not fetch weather for this location: {exc}") from exc
