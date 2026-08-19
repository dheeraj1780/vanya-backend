from datetime import datetime
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InternalServerError
from app.models.plant import Plant


async def list_plants_by_user(
    db: AsyncSession,
    user_id: str,
    is_indoor: Optional[bool] = None,
    is_pet_safe: Optional[bool] = None,
    is_air_purifying: Optional[bool] = None,
    care_difficulty: Optional[str] = None,
    light_needs: Optional[str] = None,
    status: str = "active",
) -> List[Plant]:
    try:
        query = select(Plant).where(Plant.user_id == user_id, Plant.status == status)
        if is_indoor is not None:
            query = query.where(Plant.is_indoor == is_indoor)
        if is_pet_safe is not None:
            query = query.where(Plant.is_pet_safe == is_pet_safe)
        if is_air_purifying is not None:
            query = query.where(Plant.is_air_purifying == is_air_purifying)
        if care_difficulty is not None:
            query = query.where(Plant.care_difficulty == care_difficulty)
        if light_needs is not None:
            query = query.where(Plant.light_needs == light_needs)
        query = query.order_by(Plant.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())
    except Exception as exc:
        raise InternalServerError(f"Failed to list plants: {exc}") from exc


async def count_plants_by_user(db: AsyncSession, user_id: str, status: str = "active") -> int:
    try:
        result = await db.execute(
            select(func.count(Plant.plant_id)).where(Plant.user_id == user_id, Plant.status == status)
        )
        return result.scalar_one()
    except Exception as exc:
        raise InternalServerError(f"Failed to count plants: {exc}") from exc


async def get_plant_by_id(db: AsyncSession, plant_id: str) -> Optional[Plant]:
    try:
        result = await db.execute(select(Plant).where(Plant.plant_id == plant_id))
        return result.scalar_one_or_none()
    except Exception as exc:
        raise InternalServerError(f"Failed to fetch plant: {exc}") from exc


async def create_plant(
    db: AsyncSession,
    user_id: str,
    nickname: str,
    species: Optional[str],
    species_confidence: Optional[str],
    light_needs: Optional[str],
    water_frequency_days: int,
    fun_facts: Optional[list],
    notes: Optional[str],
    is_indoor: Optional[bool] = None,
    is_pet_safe: Optional[bool] = None,
    is_air_purifying: Optional[bool] = None,
    care_difficulty: Optional[str] = None,
    status: str = "active",
) -> Plant:
    try:
        plant = Plant(
            user_id=user_id,
            nickname=nickname,
            species=species,
            species_confidence=species_confidence,
            light_needs=light_needs,
            water_frequency_days=water_frequency_days,
            fun_facts=fun_facts,
            notes=notes,
            is_indoor=is_indoor,
            is_pet_safe=is_pet_safe,
            is_air_purifying=is_air_purifying,
            care_difficulty=care_difficulty,
            status=status,
        )
        db.add(plant)
        await db.commit()
        await db.refresh(plant)
        return plant
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to create plant: {exc}") from exc


async def update_plant(
    db: AsyncSession,
    plant: Plant,
    nickname: Optional[str],
    last_watered_at: Optional[datetime],
    notes: Optional[str],
) -> Plant:
    try:
        if nickname is not None:
            plant.nickname = nickname
        if last_watered_at is not None:
            plant.last_watered_at = last_watered_at
        if notes is not None:
            plant.notes = notes
        await db.commit()
        await db.refresh(plant)
        return plant
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to update plant: {exc}") from exc


async def move_to_garden(db: AsyncSession, plant: Plant) -> Plant:
    """Wishlist -> active. No new identification is spent — the plant was
    already identified when it was first added to the wishlist; this just
    reclassifies the same row (see plans.py's WISHLIST note)."""
    try:
        plant.status = "active"
        await db.commit()
        await db.refresh(plant)
        return plant
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to move plant to garden: {exc}") from exc


async def update_plant_photo(db: AsyncSession, plant: Plant, photo_url: str) -> Plant:
    try:
        plant.photo_url = photo_url
        await db.commit()
        await db.refresh(plant)
        return plant
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to attach photo to plant: {exc}") from exc


async def delete_plant(db: AsyncSession, plant: Plant) -> None:
    try:
        await db.delete(plant)  # DIAGNOSIS_LOG rows cascade via relationship config
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise InternalServerError(f"Failed to delete plant: {exc}") from exc
