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
    fun_facts: List[str] = Field(max_length=4)
    # E-MP001: common household/vernacular names this plant goes by in
    # Indian homes, alongside (not instead of) common_name/species — e.g.
    # "Money Plant" / "Paisa Paudha" for Epipremnum aureum. Empty when the
    # AI has no genuine distinct one rather than inventing something.
    regional_names: List[str] = Field(default_factory=list, max_length=4)
    # E-MP002: a named soil type (e.g. "Red soil", "Black soil", "Sandy
    # loam"), not a vague description — plus what to mix into it.
    soil_type: str = ""
    soil_amendments: str = ""
    is_indoor: bool
    is_pet_safe: bool
    is_air_purifying: bool
    care_difficulty: Literal["easy", "moderate", "hard"]
    # True if this call was charged against the one-time Garden Setup
    # allowance (see entitlement_service.check_identification_limit)
    # instead of the regular recurring weekly allowance — the client uses
    # this to show "Garden setup X of Y" instead of the usual weekly copy.
    used_garden_setup: bool = False
    # BUG-C003: the old prompt forced a confident-looking species guess out
    # of every image, including artificial plants and non-plant objects —
    # it would "identify" a fake fern as a real one just as readily as a
    # real one. False here means Gemini judged the photo isn't a real,
    # living plant; the client shows fun_message as a playful pop-up
    # instead of treating this as a normal identification. Defaults true so
    # any identify result missing this field (shouldn't happen against the
    # real prompt, but defensive) behaves exactly as it always has.
    is_real_plant: bool = True
    fun_message: Optional[str] = None


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
