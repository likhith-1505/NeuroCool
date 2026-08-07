"""API representation of forecast predictions.

Forecasts are always the *latest* computed snapshot — ForecastService
recomputes them every simulation tick, and there is no persisted forecast
history to query (see app.forecasting.service's module docstring). These
schemas mirror app.forecasting.base.RackPrediction's fields exactly.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ForecastPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    horizon_seconds: int
    timestamp: datetime
    predicted_temperature: float
    predicted_gpu_utilization: float
    predicted_power: float
    predicted_health: float
    predicted_cooling: float
    predicted_risk: float
    confidence: float


class ClusterForecastRead(BaseModel):
    """Cluster-wide prediction — GET /api/forecast."""

    predictions: list[ForecastPoint]


class RackForecastRead(BaseModel):
    """One rack's prediction — GET /api/forecast/racks and .../racks/{id}."""

    rack_id: uuid.UUID
    rack_name: str
    predictions: list[ForecastPoint]
