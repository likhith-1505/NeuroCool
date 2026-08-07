"""In-memory live telemetry state for the digital twin.

These are plain, mutable dataclasses — deliberately NOT the SQLAlchemy
models. The database rows (app.models.cluster.Cluster, app.models.rack.Rack)
represent durable identity: which racks exist and which cluster they belong
to. This module represents the fast-moving numbers that change every tick
and are never persisted directly — only significant transitions become
Event rows (see app.services.event_service).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.models.enums import RackStatus


@dataclass
class RackState:
    """A rack's current telemetry snapshot — exactly the fields the API exposes."""

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


@dataclass
class RackInternals:
    """Slow-moving bookkeeping the engine needs between ticks.

    These are the "hidden" targets the visible RackState fields wander
    toward — never exposed to the API, only used internally by the physics
    step to keep evolution smooth instead of purely random.
    """

    gpu_baseline: float
    jobs_baseline: float


@dataclass
class ClusterState:
    """Cluster-wide telemetry, always derived from the current racks."""

    id: uuid.UUID
    name: str
    overall_health: float
    average_temperature: float
    total_power: float
    cooling_efficiency: float
    energy_savings: float
    prediction_confidence: float
