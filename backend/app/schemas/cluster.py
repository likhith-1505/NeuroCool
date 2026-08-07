"""API representation of cluster-wide live telemetry."""

import uuid

from pydantic import BaseModel, ConfigDict


class ClusterTelemetry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    overall_health: float
    average_temperature: float
    total_power: float
    cooling_efficiency: float
    energy_savings: float
    prediction_confidence: float
