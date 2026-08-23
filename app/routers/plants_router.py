from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import error_response, success_response
from app.dependencies import get_current_user, get_request_id
from app.models.user import User
from app.schemas.plant import PhotoUploadRequest, PlantInput, PlantUpdateInput
from app.services.ai_service import get_latest_diagnosis
from app.services.calculator_service import compute_care_calculators, preview_location_weather
from app.services.plant_service import create_plant, delete_plant, list_plants, move_to_garden, update_plant, upload_plant_photo

router = APIRouter(prefix="/plants", tags=["Plants"])


@router.get("")
async def list_plants_endpoint(
    is_indoor: Optional[bool] = Query(default=None),
    is_pet_safe: Optional[bool] = Query(default=None),
    is_air_purifying: Optional[bool] = Query(default=None),
    care_difficulty: Optional[str] = Query(default=None),
    light_needs: Optional[str] = Query(default=None),
    status: Literal["active", "wishlist"] = Query(default="active"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        plants = await list_plants(db, current_user, is_indoor, is_pet_safe, is_air_purifying, care_difficulty, light_needs, status)
        return success_response(
            {"plants": [p.model_dump(mode="json") for p in plants]}, "Plants retrieved successfully", 200, trace_id
        )
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error listing plants: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@router.post("")
async def create_plant_endpoint(
    request: PlantInput,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        plant = await create_plant(db, current_user, request)
        return success_response(
            {"id": plant.id, "created_at": plant.created_at.isoformat()},
            "Plant created successfully",
            201,
            trace_id,
        )
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error creating plant: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@router.put("/{plant_id}")
async def update_plant_endpoint(
    plant_id: str,
    request: PlantUpdateInput,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        plant = await update_plant(db, current_user, plant_id, request)
        return success_response(plant.model_dump(mode="json"), "Plant updated successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error updating plant: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@router.post("/{plant_id}/move-to-garden")
async def move_to_garden_endpoint(
    plant_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    """Promotes a wishlist plant into the active garden — costs a garden
    plant slot, not a new identification (see plant_service.move_to_garden)."""
    try:
        plant = await move_to_garden(db, current_user, plant_id)
        return success_response(plant.model_dump(mode="json"), "Plant moved to garden successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error moving plant to garden: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@router.delete("/{plant_id}")
async def delete_plant_endpoint(
    plant_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        await delete_plant(db, current_user, plant_id)
        return success_response(
            {"message": "Plant and associated diagnosis history removed."},
            "Plant deleted successfully",
            200,
            trace_id,
        )
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error deleting plant: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@router.post("/{plant_id}/photo")
async def upload_plant_photo_endpoint(
    plant_id: str,
    request: PhotoUploadRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    try:
        photo_url = await upload_plant_photo(db, current_user, plant_id, request.image_base64)
        return success_response({"photo_url": photo_url}, "Photo uploaded successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error uploading photo: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@router.get("/{plant_id}/diagnoses/latest")
async def get_latest_diagnosis_endpoint(
    plant_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    """No AI provider call — reads whatever was last stored. This is what
    lets a user re-view a past diagnosis for free instead of re-running
    the model just to see something already known."""
    try:
        data = await get_latest_diagnosis(db, current_user, plant_id)
        return success_response(data.model_dump(mode="json"), "Latest diagnosis retrieved successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error fetching latest diagnosis: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@router.get("/weather-preview")
async def get_weather_preview_endpoint(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    current_user: User = Depends(get_current_user),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    """Season + temperature for a raw lat/long — no plant, no calculator
    limit consumed. Lets the Care Calculator screen show what a just-granted
    location resolves to immediately, instead of only after Calculate."""
    try:
        data = await preview_location_weather(latitude, longitude)
        return success_response(data.model_dump(mode="json"), "Location weather resolved successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error resolving location weather: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)


@router.get("/{plant_id}/calculators")
async def get_care_calculators_endpoint(
    plant_id: str,
    pot_diameter_cm: Optional[int] = Query(default=None, ge=5, le=100),
    season: Optional[Literal["spring", "summer", "autumn", "winter"]] = Query(default=None),
    room_light: Optional[Literal["low", "medium", "bright", "direct"]] = Query(default=None),
    latitude: Optional[float] = Query(default=None, ge=-90, le=90),
    longitude: Optional[float] = Query(default=None, ge=-180, le=180),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace_id: str = Depends(get_request_id),
) -> JSONResponse:
    """Watering schedule, fertilizer dosing, and light fit for one plant —
    AI-computed when available (see ai_provider.calculate_care), falling
    back to calculator_service's rule-of-thumb formulas otherwise. `season`
    overrides location-derived season if both are given."""
    try:
        data = await compute_care_calculators(db, current_user, plant_id, pot_diameter_cm, season, room_light, latitude, longitude)
        return success_response(data.model_dump(mode="json"), "Care calculators computed successfully", 200, trace_id)
    except AppException as exc:
        return error_response(exc.message, exc.error_code, exc.status_code, trace_id)
    except Exception as exc:
        return error_response(f"Unexpected error computing care calculators: {exc}", "INTERNAL_SERVER_ERROR", 500, trace_id)
