from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

# Every event name the product spec asks to track — kept as a plain list
# (not a strict enum on the request) so a client on an older build never
# hard-fails an analytics call over an unrecognized name; unknown names
# are still stored, just worth noticing in a query later.
KNOWN_EVENT_NAMES = {
    "guest_identification_used",
    "guest_care_used",
    "guest_diagnose_used",
    "google_signup_completed",
    "plantie_identification_used",
    "plantie_care_used",
    "plantie_diagnose_used",
    "garden_setup_started",
    "garden_setup_completed",
    "green_thumb_paywall_viewed",
    "photosynthesis_phd_paywall_viewed",
    "subscription_purchase_started",
    "subscription_purchase_completed",
    "subscription_cancelled",
    "subscription_expired",
    "identification_limit_reached",
    "care_limit_reached",
    "diagnose_limit_reached",
    "plant_limit_reached",
}


class AnalyticsEventRequest(BaseModel):
    event_name: str = Field(max_length=100)
    # Small, non-PII key/value context (e.g. {"plan": "green_thumb"} is
    # already captured separately below — this is for extra event-specific
    # detail like {"feature": "identification"}), never account identifiers,
    # emails, or free-text user input.
    properties: Optional[Dict[str, Any]] = None
