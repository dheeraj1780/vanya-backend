"""
ORM models for PLANTS, DIAGNOSIS_LOG, and AI_CALL_LOG.

AI_CALL_LOG is a small addition beyond the original ER diagram: it backs the
per-user daily rate limit described in the OpenAPI spec's 429 responses,
which needs *some* table to count against. Rate-limiting is per-user here
rather than strictly per-device (the ER diagram has no device identifier),
which is a deliberate simplification worth knowing about if per-device
limits are needed later.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Plant(Base):
    __tablename__ = "plants"

    plant_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"), index=True, nullable=False)
    # "active" = a real plant in the user's garden (counts against
    # max_plants, gets reminders/calculators/diagnose). "wishlist" = a
    # plant the user identified but hasn't committed a garden slot to yet
    # (counts against wishlist_limit instead — see plans.py's WISHLIST
    # note). Moving one to the garden just flips this field, see
    # plant_service.move_to_garden.
    status: Mapped[str] = mapped_column(String(10), default="active", nullable=False)
    nickname: Mapped[str] = mapped_column(String(100), nullable=False)
    species: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    species_confidence: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # high|medium|low
    light_needs: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    water_frequency_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    photo_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    # Growth Journey's background texture for this plant — either
    # "preset:<key>" (one of the app's bundled options, see
    # plant_service._GROWTH_BACKGROUND_PRESETS) or a real R2/S3 URL for a
    # custom photo the user picked from their own gallery. null = no
    # choice made yet, client falls back to its own default.
    growth_background: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    fun_facts: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    last_watered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Smart Filters — populated from the same POST /ai/identify call that
    # already runs when a plant is added, no separate AI call needed.
    is_indoor: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_pet_safe: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_air_purifying: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    care_difficulty: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # easy | moderate | hard

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    diagnoses: Mapped[list["DiagnosisLog"]] = relationship(back_populates="plant", cascade="all, delete-orphan")
    growth_memories: Mapped[list["GrowthMemory"]] = relationship(back_populates="plant", cascade="all, delete-orphan")


class DiagnosisLog(Base):
    __tablename__ = "diagnosis_log"

    diagnosis_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    plant_id: Mapped[str] = mapped_column(String(36), ForeignKey("plants.plant_id"), index=True, nullable=False)
    # Denormalized so the monthly-count query behind the paywall is a single-table scan.
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"), index=True, nullable=False)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False)
    likely_causes: Mapped[list] = mapped_column(JSON, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    urgency: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    plant: Mapped["Plant"] = relationship(back_populates="diagnoses")


class GrowthMemory(Base):
    """One dated, named photo in a plant's growth timeline — the
    Photosynthesis PhD (unlimited) / Green Thumb (one-time, see
    plans.py's growth_memory_limit) feature. Works identically for
    wishlist plants too — nothing here or in plant_service checks
    Plant.status, same as diagnoses/photos."""

    __tablename__ = "growth_memories"

    memory_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    plant_id: Mapped[str] = mapped_column(String(36), ForeignKey("plants.plant_id"), index=True, nullable=False)
    # Denormalized so check_growth_memory_limit's count is a single-table
    # scan — same reasoning as DiagnosisLog.user_id.
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    photo_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    plant: Mapped["Plant"] = relationship(back_populates="growth_memories")


class AiCallLog(Base):
    """One row per identify/calculate/diagnose call. Originally just the
    backing table for the daily rate limit; now doubles as the entitlement
    engine's usage ledger too (see usage_repository.py + entitlement_service.py)
    — every tier's weekly/monthly/lifetime allowance is a count of these
    rows filtered by call_type and a time window. Separate from
    DiagnosisLog, which is product data the user actually sees."""

    __tablename__ = "ai_call_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"), index=True, nullable=False)
    # identify | garden_setup (a one-time-allowance identify, see
    # entitlement_service.check_identification_limit) | diagnose | calculator
    call_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
