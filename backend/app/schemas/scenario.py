"""API representations for scenario definitions and live scenario status."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TransitionState = Literal["transitioning", "steady"]
ScenarioScope = Literal["cluster", "single_rack"]


class ScenarioDefinitionRead(BaseModel):
    """A built-in scenario's static, descriptive profile."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str
    description: str
    scope: ScenarioScope
    ramp_seconds: float
    duration_seconds: float | None


class ScenarioStatus(BaseModel):
    """The currently active scenario and how far its transition has progressed."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str
    transition_state: TransitionState
    target_rack_id: uuid.UUID | None
    activated_at: datetime


class ScenarioActivateRequest(BaseModel):
    scenario: str = Field(..., description="Scenario key, e.g. 'thermal_spike'.", min_length=1)
