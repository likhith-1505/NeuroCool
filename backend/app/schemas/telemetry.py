"""The payload broadcast over /ws/telemetry every simulation tick."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.schemas.cluster import ClusterTelemetry
from app.schemas.rack import RackTelemetry
from app.schemas.scenario import ScenarioStatus
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.simulation.engine import SimulationService


class TelemetrySnapshot(BaseModel):
    timestamp: datetime
    cluster: ClusterTelemetry
    racks: list[RackTelemetry]
    scenario: ScenarioStatus

    @classmethod
    def from_simulation(cls, simulation: "SimulationService", timestamp: datetime | None = None) -> "TelemetrySnapshot":
        """Build a snapshot from the live simulation state.

        Shared by the WebSocket endpoint (initial snapshot on connect), the
        simulation engine (per-tick broadcast), and scenario activation
        (immediate broadcast on change) — one place that knows how to turn
        engine state into this payload shape, so "whenever a scenario
        changes, broadcast active scenario / transition state / updated
        cluster / updated racks" and the regular per-tick broadcast are
        exactly the same code path, not two.
        """
        return cls(
            timestamp=timestamp or utcnow(),
            cluster=ClusterTelemetry.model_validate(simulation.cluster_state),
            racks=[RackTelemetry.model_validate(rack) for rack in simulation.rack_states],
            scenario=simulation.scenario_status,
        )
