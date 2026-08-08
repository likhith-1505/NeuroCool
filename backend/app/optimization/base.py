"""The OptimizationEngine contract.

Mirrors app.ai.base's DecisionEngine and app.forecasting.base's
ForecastEngine exactly, for the same reason: this is the seam that lets
the planning *strategy* be swapped later (today: simulate every candidate
through the physics engine and rank by a weighted score; later: a
reinforcement-learning policy, or a heavier search over action sequences)
without touching OptimizationService, the REST routes, or the WebSocket
payload. Every concrete engine implements one method:
`plan(context) -> list[OptimizationPlan]`.

Deliberately excluded from OptimizationContext: database ids, WebSocket
connections, anything persistence- or transport-related — same principle
as DecisionContext/ForecastContext. OptimizationService (see
app.optimization.service) owns turning plans into durable, broadcast,
event-raising OptimizationPlan rows; a planning engine only ever sees
telemetry, forecasts, scenario state, topology, and recent history.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from app.models.enums import ExecutionActionType
from app.simulation.state import ClusterState, RackState

if TYPE_CHECKING:
    from app.forecasting.base import RackPrediction
    from app.models.event import Event


@dataclass(frozen=True)
class CandidateScore:
    """Every dimension the objective asks a candidate to be scored on.

    Every field here is *derived* from a simulated projection (see
    app.optimization.simulator/scoring) — nothing is a fixed number per
    action type; two candidates of the same action_type on different racks
    at different telemetry will score differently.
    """

    temperature_reduction_c: float  # positive = cooler than today
    power_impact_kw: float  # positive = draws more power, negative = less
    cooling_improvement_pct: float  # positive = better cooling efficiency
    execution_cost: float  # 0-100, derived from the action's driver magnitude
    operational_disruption: float  # 0-100, derived from how many racks/jobs it touches
    risk_reduction: float  # -100..100, current thermal risk minus projected (app.forecasting.risk)
    estimated_recovery_seconds: float  # projected time to reach a safe state
    confidence: float  # 0-100
    overall_score: float  # 0-100 weighted composite — the ranking key


@dataclass(frozen=True)
class OptimizationCandidate:
    """One remediation option the planner considered, with its simulated
    outcome and score. `rejection_reason` is filled in by the planner for
    every candidate except the winner (see OptimizationPlan.alternatives).
    """

    action_type: ExecutionActionType
    description: str
    affected_racks: list[uuid.UUID]
    redistribute_racks: list[uuid.UUID]
    projected_temperature: float
    projected_cooling: float
    projected_power: float
    score: CandidateScore
    rejection_reason: str | None = None


@dataclass(frozen=True)
class OptimizationContext:
    """Everything a planning engine is allowed to look at. Matches the
    objective's stated inputs exactly: Current Cluster State (cluster/
    racks), Forecasts, Active Scenario, Rack Topology (racks is topology-
    ordered — see app.simulation.state.ring_neighbors), Recent Events.

    Cluster-wide, like DecisionContext (app.ai.base) — a planning engine
    decides for itself which rack(s), if any, warrant a plan this tick,
    the same way RuleBasedDecisionEngine decides for itself which rules
    fire. OptimizationService (see app.optimization.service) owns turning
    the result into durable rows and events, never the triggering logic.
    """

    cluster: ClusterState
    racks: list[RackState]  # every rack, topology-ordered
    scenario_key: str
    scenario_active: bool
    forecasts: dict[uuid.UUID, list["RackPrediction"]]
    recent_events: list["Event"]
    now: datetime


@dataclass(frozen=True)
class OptimizationPlan:
    """One completed planning cycle: every candidate considered for
    `trigger_rack`, ranked best-first. `id` is assigned up front (not only
    once persisted) so a same-tick DecisionDraft can reference it before
    OptimizationService has written the row — see app.optimization.service.
    """

    trigger_key: str
    trigger_rack_id: uuid.UUID
    trigger_reason: str
    candidates: list[OptimizationCandidate]  # ranked, best first
    now: datetime
    id: uuid.UUID = field(default_factory=uuid.uuid4)

    @property
    def winner(self) -> OptimizationCandidate:
        return self.candidates[0]

    @property
    def alternatives(self) -> list[OptimizationCandidate]:
        return self.candidates[1:]


class OptimizationEngine(Protocol):
    """Contract every planning strategy must satisfy.

    Stateless from the *caller's* perspective — OptimizationService owns
    persistence and lifecycle. An engine only maps "current context for one
    triggered rack" to "a ranked plan"; it is free to keep private state
    internally as long as `plan` keeps this exact signature.
    """

    def plan(self, context: OptimizationContext) -> list[OptimizationPlan]:
        """Return zero or more ranked plans — one per rack whose telemetry
        or forecast crosses a planning trigger this tick. Each plan always
        contains at least the NO_ACTION candidate.
        """
        ...
