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


@dataclass(frozen=True)
class RackDrivers:
    """External bias applied to one rack's targets for a single physics tick.

    This is the seam both ScenarioManager and ExecutionManager use to
    "influence the simulator through inputs" (per design) without touching
    the physics engine's own logic: every field defaults to a no-op, so a
    rack with no active influence behaves exactly as if RackDrivers didn't
    exist. The physics engine folds these into targets it was already
    computing — it gains no new branches per scenario or per remediation
    action.

    gpu_bias / power_bias_kw / cooling_ceiling are ScenarioManager's original
    fields (workload-driven incidents). fan_bias / cooling_bias exist for
    ExecutionManager's remediation actions (see app.execution) — a "cooling
    adjustment" execution pushes fans harder and boosts cooling capacity
    directly, the mirror image of a cooling_ceiling cap.
    """

    gpu_bias: float = 0.0
    cooling_ceiling: float | None = None
    power_bias_kw: float = 0.0
    fan_bias: float = 0.0
    cooling_bias: float = 0.0


NO_DRIVERS = RackDrivers()


def combine_drivers(*drivers: RackDrivers) -> RackDrivers:
    """Merge multiple RackDrivers acting on the same rack into one: bias
    fields add together, cooling_ceiling takes the most restrictive (lowest)
    value set, if any. Used to combine scenario-driven and execution-driven
    influences within a single tick — e.g. an active "cooling adjustment"
    execution trying to boost cooling while a cooling_failure scenario is
    still capping it, which is exactly the realistic tension that should
    happen when a remediation partially — not fully — compensates for an
    ongoing external cause.
    """
    ceilings = [d.cooling_ceiling for d in drivers if d.cooling_ceiling is not None]
    return RackDrivers(
        gpu_bias=sum(d.gpu_bias for d in drivers),
        cooling_ceiling=min(ceilings) if ceilings else None,
        power_bias_kw=sum(d.power_bias_kw for d in drivers),
        fan_bias=sum(d.fan_bias for d in drivers),
        cooling_bias=sum(d.cooling_bias for d in drivers),
    )
