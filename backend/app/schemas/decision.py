"""API representation of an AI-generated Decision."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DecisionStatus, EventSeverity


class DecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    timestamp: datetime
    severity: EventSeverity
    title: str
    reasoning: str
    recommended_action: str
    expected_temperature_reduction: float | None
    expected_power_saving: float | None
    confidence: float
    affected_racks: list[uuid.UUID]
    affected_jobs: list = Field(default_factory=list)  # placeholder — no Job model exists yet
    status: DecisionStatus
