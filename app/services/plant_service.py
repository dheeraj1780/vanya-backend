from datetime import datetime
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ForbiddenError, InternalServerError, NotFoundError
from app.models.plant import Plant
from app.models.user import User
from app.repositories.plant_repository import (
    count_plants_by_user,
    create_plant as create_plant_repo,
    delete_plant as delete_plant_repo,
    get_plant_by_id,
    list_plants_by_user,
    move_to_garden as move_to_garden_repo,
    update_plant as update_plant_repo,
    update_plant_photo,
)
from app.schemas.plant import PlantInput, PlantItem, PlantUpdateInput
from app.services.entitlement_service import check_plant_slot_limit, check_wishlist_limit
from app.utils.storage import delete_plant_photo, save_plant_photo


def _to_plant_item(plant: Plant) -> PlantItem:
    return PlantItem(
        id=plant.plant_id,
        status=plant.status,
        nickname=plant.nickname,
        species=plant.species,
        species_confidence=plant.species_confidence,
        light_needs=plant.light_needs,
        water_frequency_days=plant.water_frequency_days,
        photo_url=plant.photo_url,
        last_watered_at=plant.last_watered_at,
        fun_facts=plant.fun_facts or [],
        is_indoor=plant.is_indoor,
        is_pet_safe=plant.is_pet_safe,
        is_air_purifying=plant.is_air_purifying,
        care_difficulty=plant.care_difficulty,
        created_at=plant.created_at,
    )


async def list_plants(
    db: AsyncSession,
    user: User,
    is_indoor: Optional[bool] = None,
    is_pet_safe: Optional[bool] = None,
    is_air_purifying: Optional[bool] = None,
    care_difficulty: Optional[str] = None,
    light_needs: Optional[str] = None,
    status: str = "active",
) -> List[PlantItem]:
    try:
        plants = await list_plants_by_user(
            db, user.user_id, is_indoor, is_pet_safe, is_air_purifying, care_difficulty, light_needs, status
        )
        return [_to_plant_item(p) for p in plants]
    except Exception as exc:
        raise InternalServerError(f"Failed to list plants: {exc}") from exc


async def check_plant_limit(db: AsyncSession, user: User) -> None:
    """Raises before any caller (plant creation or AI identification)
    proceeds, so a blocked user never costs an AI provider call. Thin
    wrapper kept for call-site stability (ai_service imports this name) —
    the actual tier-aware limit and messaging live in
    entitlement_service.check_plant_slot_limit, the one place plan config
    is read from."""
    await check_plant_slot_limit(db, user)


async def create_plant(db: AsyncSession, user: User, plant_input: PlantInput) -> PlantItem:
    try:
        # Which slot pool this draws from depends on where it's going —
        # a garden plant checks the (small, tier-limited) max_plants pool,
        # a wishlist plant checks the separate (larger) wishlist_limit
        # pool. Both were already charged one identification-allowance use
        # at identify time regardless of this choice.
        if plant_input.status == "wishlist":
            await check_wishlist_limit(db, user)
        else:
            await check_plant_limit(db, user)
        plant = await create_plant_repo(
            db,
            user_id=user.user_id,
            nickname=plant_input.nickname,
            species=plant_input.species,
            species_confidence=plant_input.species_confidence,
            light_needs=plant_input.light_needs,
            water_frequency_days=plant_input.water_frequency_days,
            fun_facts=plant_input.fun_facts,
            notes=plant_input.notes,
            is_indoor=plant_input.is_indoor,
            is_pet_safe=plant_input.is_pet_safe,
            is_air_purifying=plant_input.is_air_purifying,
            care_difficulty=plant_input.care_difficulty,
            status=plant_input.status,
        )
        return _to_plant_item(plant)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to create plant: {exc}") from exc


async def _get_owned_plant(db: AsyncSession, user: User, plant_id: str) -> Plant:
    plant = await get_plant_by_id(db, plant_id)
    if plant is None:
        raise NotFoundError("Plant not found")
    if plant.user_id != user.user_id:
        raise ForbiddenError("You do not have permission to modify this plant")
    return plant


async def move_to_garden(db: AsyncSession, user: User, plant_id: str) -> PlantItem:
    """Promotes a wishlist plant into the active garden — costs a garden
    plant slot (checked here, same as a fresh add) but no new
    identification, since this plant was already identified when it was
    first saved to the wishlist."""
    try:
        plant = await _get_owned_plant(db, user, plant_id)
        if plant.status == "active":
            return _to_plant_item(plant)  # already there — no-op, not an error
        await check_plant_limit(db, user)
        moved = await move_to_garden_repo(db, plant)
        return _to_plant_item(moved)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to move plant to garden: {exc}") from exc


async def update_plant(db: AsyncSession, user: User, plant_id: str, update: PlantUpdateInput) -> PlantItem:
    try:
        plant = await _get_owned_plant(db, user, plant_id)
        updated = await update_plant_repo(db, plant, update.nickname, update.last_watered_at, update.notes)
        return _to_plant_item(updated)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to update plant: {exc}") from exc


async def delete_plant(db: AsyncSession, user: User, plant_id: str) -> None:
    try:
        plant = await _get_owned_plant(db, user, plant_id)
        photo_url = plant.photo_url
        await delete_plant_repo(db, plant)
        # After the DB row is gone, not before — the plant record is the
        # thing that actually matters here, and a storage hiccup shouldn't
        # block the delete. See delete_plant_photo's docstring.
        await delete_plant_photo(photo_url)
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to delete plant: {exc}") from exc


async def upload_plant_photo(db: AsyncSession, user: User, plant_id: str, image_base64: str) -> str:
    try:
        plant = await _get_owned_plant(db, user, plant_id)
        previous_photo_url = plant.photo_url
        photo_url = await save_plant_photo(image_base64, plant_id)
        await update_plant_photo(db, plant, photo_url)
        # Clean up the photo this one replaces (e.g. re-scanning the same
        # plant) — after the new photo/DB row are both in place, so a
        # cleanup failure never risks the plant ending up with no photo.
        if previous_photo_url and previous_photo_url != photo_url:
            await delete_plant_photo(previous_photo_url)
        return photo_url
    except AppException:
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to upload plant photo: {exc}") from exc


async def get_plant_for_user(db: AsyncSession, user: User, plant_id: str) -> Plant:
    """Exposed for ai_service, which needs the ORM row (for .species) rather
    than the API-facing PlantItem schema."""
    return await _get_owned_plant(db, user, plant_id)
