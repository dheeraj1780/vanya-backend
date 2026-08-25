from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class PlantInput(BaseModel):
    nickname: str = Field(min_length=1, max_length=100)
    species: Optional[str] = None
    species_confidence: Optional[Literal["high", "medium", "low"]] = None
    light_needs: Optional[str] = None
    water_frequency_days: int = Field(default=7, ge=1)
    fun_facts: Optional[List[str]] = Field(default=None, max_length=4)
    regional_names: Optional[List[str]] = Field(default=None, max_length=4)
    soil_type: Optional[str] = None
    notes: Optional[str] = None
    is_indoor: Optional[bool] = None
    is_pet_safe: Optional[bool] = None
    is_air_purifying: Optional[bool] = None
    care_difficulty: Optional[Literal["easy", "moderate", "hard"]] = None
    # "active" spends a garden plant slot (max_plants); "wishlist" spends a
    # wishlist slot instead (wishlist_limit) — see plans.py's WISHLIST note.
    # Either way the identification that produced this input already spent
    # one identification-allowance use, regardless of which list it lands in.
    status: Literal["active", "wishlist"] = "active"


class PlantUpdateInput(BaseModel):
    nickname: Optional[str] = None
    last_watered_at: Optional[datetime] = None
    notes: Optional[str] = None


class PlantItem(BaseModel):
    id: str
    status: Literal["active", "wishlist"] = "active"
    nickname: str
    species: Optional[str] = None
    species_confidence: Optional[str] = None
    light_needs: Optional[str] = None
    water_frequency_days: int
    photo_url: Optional[str] = None
    last_watered_at: Optional[datetime] = None
    fun_facts: List[str] = Field(default_factory=list)
    regional_names: List[str] = Field(default_factory=list)
    soil_type: Optional[str] = None
    is_indoor: Optional[bool] = None
    is_pet_safe: Optional[bool] = None
    is_air_purifying: Optional[bool] = None
    care_difficulty: Optional[str] = None
    created_at: datetime
    # "preset:<key>" (bundled app option) or a real photo URL (custom
    # gallery pick) — null if the user hasn't chosen one yet. See
    # plant_service._GROWTH_BACKGROUND_PRESETS for the valid preset keys.
    growth_background: Optional[str] = None

    model_config = {"from_attributes": True}


class PhotoUploadRequest(BaseModel):
    image_base64: str


class PhotoUploadData(BaseModel):
    photo_url: str


class GrowthMemoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    note: Optional[str] = Field(default=None, max_length=500)
    image_base64: str


class GrowthMemoryItem(BaseModel):
    id: str
    plant_id: str
    name: str
    note: Optional[str] = None
    photo_url: str
    created_at: datetime

    model_config = {"from_attributes": True}


class GrowthBackgroundInput(BaseModel):
    """Exactly one of these two — a bundled preset key, or a custom photo
    from the user's own gallery. See plant_service.set_growth_background
    for the validation/upload logic."""
    preset: Optional[str] = None
    image_base64: Optional[str] = None


class GrowthBackgroundData(BaseModel):
    growth_background: Optional[str] = None
