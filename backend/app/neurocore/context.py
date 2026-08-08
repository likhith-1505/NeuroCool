"""NeuroCoreContext — the single structured object every NeuroCore answer
is grounded in.

Built once per chat turn from real backend state only: SimulationService's
already-computed, in-memory read properties (cluster/rack telemetry,
forecasts, active scenario, cached decisions/executions/optimization
plans) plus a fresh query for recent Events (the one thing SimulationService
doesn't cache in memory — see app.api.events for the same query shape).
Nothing here recomputes physics, forecasts, scores, or telemetry; it only
reads what the deterministic backend already produced.

Split in two on purpose:
  - `build_context` is a pure function (plain data in, NeuroCoreContext
    out) — fully unit-testable without a database or a running simulation.
  - `load_context` is the thin, DB-touching wrapper that gathers those
    plain arguments from a real AsyncSession + SimulationService. It is
    intentionally *not* unit tested directly, the same way DecisionService/
    OptimizationService's own DB-touching methods aren't — see those
    modules' test files, which only unit-test the pure logic underneath.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.forecasting.base import RackPrediction
    from app.models.decision import Decision
    from app.models.execution import Execution
    from app.models.optimization_plan import OptimizationPlan
    from app.simulation.engine import SimulationService
    from app.simulation.state import ClusterState, RackState

# How far back NeuroCore is allowed to look for "what changed recently" —
# matches app.ai.service.DecisionService.RECENT_EVENTS_LIMIT's window
# proportions, generous enough to answer a real question, still bounded.
RECENT_EVENTS_LIMIT = 50


@dataclass(frozen=True)
class NeuroCoreContext:
    """Everything app.neurocore.grounding is allowed to look at. Every
    field is either live simulation state, a cached backend row, or a
    freshly queried Event — never a value NeuroCore itself computed.
    """

    cluster: "ClusterState"
    racks: list["RackState"]  # topology-ordered, same list SimulationService exposes
    scenario_key: str
    scenario_active: bool
    forecasts: dict[uuid.UUID, list["RackPrediction"]]
    cluster_forecast: list["RackPrediction"]
    active_plans: list["OptimizationPlan"]
    all_plans: list["OptimizationPlan"]  # newest first
    active_decisions: list["Decision"]
    all_decisions: list["Decision"]  # newest first
    all_executions: list["Execution"]  # newest first
    recent_events: list[Event]  # newest first
    generated_at: datetime = field(default_factory=utcnow)


def build_context(
    *,
    cluster: "ClusterState",
    racks: list["RackState"],
    scenario_key: str,
    forecasts: dict[uuid.UUID, list["RackPrediction"]],
    cluster_forecast: list["RackPrediction"],
    active_plans: list["OptimizationPlan"],
    all_plans: list["OptimizationPlan"],
    active_decisions: list["Decision"],
    all_decisions: list["Decision"],
    all_executions: list["Execution"],
    recent_events: list[Event],
    now: datetime | None = None,
) -> NeuroCoreContext:
    return NeuroCoreContext(
        cluster=cluster,
        racks=racks,
        scenario_key=scenario_key,
        scenario_active=scenario_key != "normal",
        forecasts=forecasts,
        cluster_forecast=cluster_forecast,
        active_plans=active_plans,
        all_plans=all_plans,
        active_decisions=active_decisions,
        all_decisions=all_decisions,
        all_executions=all_executions,
        recent_events=recent_events,
        generated_at=now or utcnow(),
    )


async def load_context(db: AsyncSession, simulation: "SimulationService") -> NeuroCoreContext:
    """Thin, DB-touching glue — see module docstring. `simulation`'s
    properties are already the live, in-memory source of truth for
    everything except recent events.
    """
    result = await db.execute(select(Event).order_by(Event.occurred_at.desc()).limit(RECENT_EVENTS_LIMIT))
    recent_events = list(result.scalars().all())

    return build_context(
        cluster=simulation.cluster_state,
        racks=simulation.rack_states,
        scenario_key=simulation.scenario_status.key,
        forecasts=simulation.rack_forecasts,
        cluster_forecast=simulation.cluster_forecast,
        active_plans=simulation.active_plans,
        all_plans=simulation.all_plans,
        active_decisions=simulation.active_decisions,
        all_decisions=simulation.all_decisions,
        all_executions=simulation.all_executions,
        recent_events=recent_events,
    )
