"""The DecisionEngine contract.

This is the seam that lets the reasoning *strategy* be swapped later (rule-
based now, an LLM later) without touching anything else — DecisionService,
the REST routes, the WebSocket broadcast — none of it depends on how a
recommendation gets produced, only on this shape. Every concrete engine
(RuleBasedDecisionEngine today, an LLMDecisionEngine later) implements a
single method: `evaluate(context) -> list[DecisionDraft]`.

Deliberately excluded from DecisionContext: database ids, WebSocket
connections, anything persistence- or transport-related. A reasoning engine
only ever sees telemetry; DecisionService (see app.ai.service) is solely
responsible for turning its output into durable, deduplicated, broadcast
Decision rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from app.models.enums import EventSeverity
from app.simulation.state import ClusterState, RackState

if TYPE_CHECKING:
    from app.models.event import Event
    from app.optimization.base import OptimizationPlan


@dataclass(frozen=True)
class DecisionContext:
    """Everything a reasoning engine is allowed to look at.

    Bundled into one object so the DecisionEngine Protocol's signature
    stays stable even if what an engine needs to know grows later (e.g. an
    LLM engine wanting a longer history) — new fields get added here, not
    threaded through every engine's method signature. `plans` is the
    latest example: the Decision Engine now consumes the Optimization
    Engine's output (see app.optimization) instead of reading forecasts
    directly — a plan already reasoned about the forecast, the active
    scenario, topology, and recent history to arrive at a ranked,
    simulated set of candidates, which is strictly more than a raw
    forecast gives a rule to work with. The two engines stay independent —
    this module never imports app.optimization at runtime, only under
    TYPE_CHECKING for the annotation.
    """

    cluster: ClusterState
    racks: list[RackState]
    scenario_key: str
    scenario_target_rack_id: uuid.UUID | None
    recent_events: list["Event"]
    now: datetime
    # Keyed by trigger_rack_id — at most one active plan per rack per tick,
    # see app.optimization.service.OptimizationService.tick.
    plans: dict[uuid.UUID, "OptimizationPlan"] = field(default_factory=dict)


@dataclass(frozen=True)
class AlternativeActionSummary:
    """A compact snapshot of one runner-up candidate from the
    OptimizationPlan a DecisionDraft was derived from — "Alternative 1" /
    "Alternative 2" per the objective. GET /api/plans/{id} (via
    DecisionDraft.plan_id) remains the full-detail record; this is only
    what carries forward onto the persisted Decision row so a single GET
    /api/decisions/{id} already answers "what else was considered and why
    not", without a second round trip.
    """

    action_type: str
    description: str
    overall_score: float
    rejection_reason: str | None


@dataclass(frozen=True)
class DecisionDraft:
    """A recommendation a DecisionEngine has produced but that hasn't been
    persisted yet — the AI equivalent of app.services.event_service.EventDraft.
    """

    rule_key: str
    severity: EventSeverity
    title: str
    reasoning: str
    recommended_action: str
    confidence: float
    affected_racks: list[uuid.UUID] = field(default_factory=list)
    expected_temperature_reduction: float | None = None
    expected_power_saving: float | None = None
    # The OptimizationPlan this draft was derived from, if any — see
    # app.optimization.
    plan_id: uuid.UUID | None = None
    alternative_actions: list[AlternativeActionSummary] = field(default_factory=list)
    # How long this stays valid before auto-expiring if the engine stops
    # re-affirming it (see DecisionService._expire_stale).
    ttl_seconds: float = 300.0


class DecisionEngine(Protocol):
    """Contract every reasoning strategy must satisfy.

    Stateless from the *caller's* perspective — DecisionService owns all
    persistence and lifecycle. An engine only maps "current telemetry" to
    "recommendations right now"; it is free to keep its own private state
    internally (e.g. RuleBasedDecisionEngine tracks short trends) as long
    as `evaluate` keeps this exact signature.
    """

    def evaluate(self, context: DecisionContext) -> list[DecisionDraft]:
        """Return zero or more recommendations for the current context."""
        ...
