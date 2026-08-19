from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class IdentifyRequest(BaseModel):
    image_base64: str


class IdentifyData(BaseModel):
    species: str
    common_name: str
    confidence: Literal["high", "medium", "low"]
    water_frequency_days: int
    light_needs: str
    care_note: str
    fun_facts: List[str] = Field(max_length=3)
    is_indoor: bool
    is_pet_safe: bool
    is_air_purifying: bool
    care_difficulty: Literal["easy", "moderate", "hard"]
    # True if this call was charged against the one-time Garden Setup
    # allowance (see entitlement_service.check_identification_limit)
    # instead of the regular recurring weekly allowance — the client uses
    # this to show "Garden setup X of Y" instead of the usual weekly copy.
    used_garden_setup: bool = False


class DiagnoseRequest(BaseModel):
    plant_id: str
    full_plant_image_base64: str
    closeup_image_base64: str


class DiagnoseData(BaseModel):
    confidence: Literal["high", "medium", "low"]
    likely_causes: List[str] = Field(min_length=1, max_length=2)
    recommended_action: str
    urgency: Literal["low", "medium", "high"]


class LatestDiagnosisData(BaseModel):
    """Read from storage — zero AI calls involved. `has_diagnosis` lets the
    client distinguish "no diagnosis yet" from an actual error."""
    has_diagnosis: bool
    confidence: Optional[Literal["high", "medium", "low"]] = None
    likely_causes: Optional[List[str]] = None
    recommended_action: Optional[str] = None
    urgency: Optional[Literal["low", "medium", "high"]] = None
    created_at: Optional[datetime] = None
