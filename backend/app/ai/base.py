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
    from app.forecasting.base import RackPrediction
    from app.models.event import Event


@dataclass(frozen=True)
class DecisionContext:
    """Everything a reasoning engine is allowed to look at.

    Bundled into one object so the DecisionEngine Protocol's signature
    stays stable even if what an engine needs to know grows later (e.g. an
    LLM engine wanting a longer history) — new fields get added here, not
    threaded through every engine's method signature. `forecasts` is the
    latest example: the Decision Engine now consumes ForecastService's
    output (see app.forecasting) to reason proactively, but the two
    engines stay independent — this module never imports app.forecasting
    at runtime, only under TYPE_CHECKING for the annotation.
    """

    cluster: ClusterState
    racks: list[RackState]
    scenario_key: str
    scenario_target_rack_id: uuid.UUID | None
    recent_events: list["Event"]
    now: datetime
    forecasts: dict[uuid.UUID, list["RackPrediction"]] = field(default_factory=dict)


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
