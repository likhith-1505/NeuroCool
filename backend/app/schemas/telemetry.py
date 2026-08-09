"""The payload broadcast over /ws/telemetry every simulation tick."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.schemas.cluster import ClusterTelemetry
from app.schemas.decision import DecisionRead
from app.schemas.forecast import ClusterForecastRead, ForecastPoint, RackForecastRead
from app.schemas.optimization import OptimizationPlanRead
from app.schemas.rack import RackTelemetry
from app.schemas.scenario import ScenarioStatus
from app.schemas.simulation import SimulationStatusRead
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.simulation.engine import SimulationService


class TelemetrySnapshot(BaseModel):
    timestamp: datetime
    cluster: ClusterTelemetry
    racks: list[RackTelemetry]
    scenario: ScenarioStatus
    simulation: SimulationStatusRead
    decisions: list[DecisionRead]
    forecast: ClusterForecastRead
    rack_forecasts: list[RackForecastRead]
    plans: list[OptimizationPlanRead]

    @classmethod
    def from_simulation(cls, simulation: "SimulationService", timestamp: datetime | None = None) -> "TelemetrySnapshot":
        """Build a snapshot from the live simulation state.

        Shared by the WebSocket endpoint (initial snapshot on connect), the
        simulation engine (per-tick broadcast), and scenario/decision
        changes (immediate broadcast) — one place that knows how to turn
        engine state into this payload shape. Active decisions, the latest
        forecast, and active optimization plans are always included here
        (not separate message types), which is also what makes a
        confidence-only update visible on the very next regular tick
        without any special-cased broadcast path — "current state,
        prediction, risk, confidence" together, every tick, per the
        objective. `plans` carries every candidate action considered and
        the winner/alternatives split (see OptimizationPlanRead) for
        whichever rack(s) currently have an active plan — "candidate
        actions, best action, expected improvement, confidence" per the
        optimization objective. `simulation` (see
        app.schemas.simulation.SimulationStatusRead) is what lets a client
        connecting while IDLE/PAUSED still see a real, current snapshot —
        see app.simulation.engine.SimulationService's own module docstring.
        """
        return cls(
            timestamp=timestamp or utcnow(),
            cluster=ClusterTelemetry.model_validate(simulation.cluster_state),
            racks=[RackTelemetry.model_validate(rack) for rack in simulation.rack_states],
            scenario=simulation.scenario_status,
            simulation=simulation.status,
            decisions=[DecisionRead.model_validate(d) for d in simulation.active_decisions],
            forecast=ClusterForecastRead(
                predictions=[ForecastPoint.model_validate(p) for p in simulation.cluster_forecast]
            ),
            rack_forecasts=[
                RackForecastRead(
                    rack_id=rack.id,
                    rack_name=rack.name,
                    predictions=[ForecastPoint.model_validate(p) for p in simulation.rack_forecasts.get(rack.id, [])],
                )
                for rack in simulation.rack_states
            ],
            plans=[OptimizationPlanRead.from_row(plan) for plan in simulation.active_plans],
        )
