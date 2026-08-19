from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class PlantInput(BaseModel):
    nickname: str = Field(min_length=1, max_length=100)
    species: Optional[str] = None
    species_confidence: Optional[Literal["high", "medium", "low"]] = None
    light_needs: Optional[str] = None
    water_frequency_days: int = Field(default=7, ge=1)
    fun_facts: Optional[List[str]] = Field(default=None, max_length=3)
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
    is_indoor: Optional[bool] = None
    is_pet_safe: Optional[bool] = None
    is_air_purifying: Optional[bool] = None
    care_difficulty: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PhotoUploadRequest(BaseModel):
    image_base64: str


class PhotoUploadData(BaseModel):
    photo_url: str
