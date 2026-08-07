"""API representation of a rack's live telemetry."""

import uuid

from pydantic import BaseModel, ConfigDict

from app.models.enums import RackStatus


class RackTelemetry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    temperature: float
    gpu_utilization: float
    cpu_utilization: float
    power_draw: float
    cooling_efficiency: float
    fan_speed: float
    health_score: float
    prediction_state: str
    running_jobs: int
    status: RackStatus
