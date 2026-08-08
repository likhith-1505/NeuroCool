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
    # The OptimizationPlan this decision was derived from, if any — see
    # app.optimization. Full candidate/score detail lives at
    # GET /api/plans/{plan_id}; alternative_actions below is the compact
    # "Alternative 1 / Alternative 2 / reason for rejection" summary.
    plan_id: uuid.UUID | None = None
    alternative_actions: list = Field(default_factory=list)
    status: DecisionStatus
