"""SimulationPort — exactly the slice of SimulationService that
app.neurocore depends on: read access to live telemetry/forecasts/plans/
decisions/executions, plus the two existing mutating entry points a write
tool may eventually trigger (execute_decision, replay_scenario).

SimulationService already implements every member here with a matching
signature, so it satisfies this Protocol structurally with zero changes —
this is the same swappable-engine pattern the backend already uses for
DecisionEngine/ForecastEngine/OptimizationEngine (app.ai.base,
app.forecasting.base, app.optimization.base), applied one layer up so
NeuroCore never has a hard, concrete dependency on the class that drives
the tick loop. The practical payoff: tests can hand PendingActionService/
the tool layer a small hand-built fake instead of constructing (and
seeding, and never ticking) the real SimulationService.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.forecasting.base import RackPrediction
    from app.models.decision import Decision
    from app.models.execution import Execution
    from app.models.optimization_plan import OptimizationPlan
    from app.schemas.scenario import ScenarioStatus
    from app.simulation.state import ClusterState, RackState


class SimulationPort(Protocol):
    @property
    def cluster_state(self) -> "ClusterState": ...

    @property
    def rack_states(self) -> list["RackState"]: ...

    def rack_state(self, rack_id: uuid.UUID) -> "RackState | None": ...

    @property
    def scenario_status(self) -> "ScenarioStatus": ...

    @property
    def cluster_forecast(self) -> list["RackPrediction"]: ...

    @property
    def rack_forecasts(self) -> dict[uuid.UUID, list["RackPrediction"]]: ...

    def rack_forecast(self, rack_id: uuid.UUID) -> list["RackPrediction"]: ...

    @property
    def active_plans(self) -> list["OptimizationPlan"]: ...

    @property
    def all_plans(self) -> list["OptimizationPlan"]: ...

    def get_plan(self, plan_id: uuid.UUID) -> "OptimizationPlan | None": ...

    @property
    def active_decisions(self) -> list["Decision"]: ...

    @property
    def all_decisions(self) -> list["Decision"]: ...

    def get_decision(self, decision_id: uuid.UUID) -> "Decision | None": ...

    @property
    def all_executions(self) -> list["Execution"]: ...

    def get_execution(self, execution_id: uuid.UUID) -> "Execution | None": ...

    async def execute_decision(self, decision_id: uuid.UUID) -> "Decision":
        """Raises LookupError if the decision doesn't exist, ValueError if
        it's no longer in an executable status — see SimulationService.
        """
        ...

    async def replay_scenario(self) -> "ScenarioStatus":
        """Raises ValueError if nothing has run yet — see SimulationService."""
        ...
