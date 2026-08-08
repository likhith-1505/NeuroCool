"""ExecutionService — persists Execution rows and drives their lifecycle
via ExecutionManager.

Mirrors DecisionService's role for decisions: SimulationService and the
REST API only ever talk to this service, never to ExecutionManager
directly. Not WebSocket-aware, on purpose (same separation as
event_service.py and app.ai.service) — this module persists and tracks
state; SimulationService broadcasts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from app.db.session import AsyncSessionLocal
from app.execution.manager import ExecutionManager
from app.models.decision import Decision
from app.models.enums import EventSeverity, ExecutionActionType, ExecutionStatus, RackStatus
from app.models.event import Event
from app.models.execution import Execution
from app.services.event_service import EventDraft, persist_events
from app.simulation.state import RackDrivers, RackState

logger = logging.getLogger(__name__)

MAX_CACHE_SIZE = 200  # bounds the in-memory execution cache for a long-running process

# A Decision's rule_key encodes what kind of recommendation it is (see
# app.ai.rules) — its prefix maps 1:1 onto a remediation action. Kept here,
# not in app.ai, since "what to actually do about a decision" is an
# execution concern, not a reasoning one.
_ACTION_BY_RULE_PREFIX: dict[str, ExecutionActionType] = {
    "workload_migration": ExecutionActionType.WORKLOAD_MIGRATION,
    "cooling_intervention": ExecutionActionType.COOLING_ADJUSTMENT,
    "delay_new_jobs": ExecutionActionType.JOB_DELAY,
    "cluster_rebalance": ExecutionActionType.CLUSTER_REBALANCE,
    # A decision derived from an app.optimization.OptimizationPlan (see
    # app.ai.rules._rule_from_optimization_plan) always uses this
    # "optimized_<action_type value>" rule_key prefix, kept in its own
    # namespace rather than aliased onto the reactive prefixes above — the
    # action_type *values* don't all textually match their reactive-rule
    # counterparts (e.g. "cooling_adjustment" vs "cooling_intervention"),
    # so a shared namespace would dedupe some pairs and not others by
    # accident. NO_ACTION is deliberately absent: a plan whose winner is
    # NO_ACTION never produces a decision at all.
    "optimized_workload_migration": ExecutionActionType.WORKLOAD_MIGRATION,
    "optimized_cooling_adjustment": ExecutionActionType.COOLING_ADJUSTMENT,
    "optimized_job_delay": ExecutionActionType.JOB_DELAY,
    "optimized_cluster_rebalance": ExecutionActionType.CLUSTER_REBALANCE,
    "optimized_fan_override": ExecutionActionType.FAN_OVERRIDE,
}

# Event raised when an action reaches full effect (ramp-in complete).
# JOB_DELAY has no specific title in the objective's event list — it only
# gets "Execution Started" and, later, the generic "Execution Completed".
_TOOK_EFFECT_TITLES: dict[ExecutionActionType, str] = {
    ExecutionActionType.WORKLOAD_MIGRATION: "Migration Completed",
    ExecutionActionType.COOLING_ADJUSTMENT: "Cooling Increased",
    ExecutionActionType.CLUSTER_REBALANCE: "Cluster Rebalanced",
    ExecutionActionType.FAN_OVERRIDE: "Fan Override Engaged",
}

_SUMMARY_TEMPLATES: dict[ExecutionActionType, str] = {
    ExecutionActionType.WORKLOAD_MIGRATION: "Migrating workload off {primary} onto {redistribute}.",
    ExecutionActionType.COOLING_ADJUSTMENT: "Increasing fan response and cooling capacity on {primary}.",
    ExecutionActionType.JOB_DELAY: "Delaying new job scheduling on {primary}.",
    ExecutionActionType.CLUSTER_REBALANCE: "Redistributing utilization from {primary} onto {redistribute}.",
    ExecutionActionType.FAN_OVERRIDE: "Overriding fan curve on {primary}.",
}


def action_type_for_rule_key(rule_key: str) -> ExecutionActionType | None:
    """The action a decision's rule maps to, or None if there isn't one
    (see the Execution.action_type nullability note in app.models.execution).
    """
    prefix = rule_key.split(":", 1)[0]
    return _ACTION_BY_RULE_PREFIX.get(prefix)


class ExecutionService:
    def __init__(self) -> None:
        self._manager = ExecutionManager()
        self._cache: dict[uuid.UUID, Execution] = {}

    # --- read access -------------------------------------------------------

    @property
    def all_executions(self) -> list[Execution]:
        return sorted(self._cache.values(), key=lambda e: e.started_at, reverse=True)

    def get(self, execution_id: uuid.UUID) -> Execution | None:
        return self._cache.get(execution_id)

    # --- starting an execution (called from execute_decision) --------------

    async def start(
        self,
        decision: Decision,
        racks: list[RackState],
        cluster_db_id: uuid.UUID,
        scenario_db_id: uuid.UUID | None,
        now: datetime,
    ) -> tuple[Execution, list[Event]]:
        """Validate and begin remediation for an executed decision.

        Never raises — an unactionable decision (no known remediation, or
        no viable target rack) still produces a durable FAILED Execution
        row with an "Execution Failed" event, rather than silently doing
        nothing or bubbling an HTTP error out of an already-successful
        decision-execute call.
        """
        racks_by_id = {r.id: r for r in racks}
        action_type = action_type_for_rule_key(decision.rule_key)
        primary_ids = [rid for rid in decision.affected_racks if rid in racks_by_id]

        redistribute_ids: set[uuid.UUID] = set()
        error_message: str | None = None

        if action_type is None:
            error_message = f"No known remediation is mapped for rule '{decision.rule_key}'."
        elif not primary_ids:
            error_message = "None of the decision's affected racks are currently known to the simulation."
        elif action_type in (ExecutionActionType.WORKLOAD_MIGRATION, ExecutionActionType.CLUSTER_REBALANCE):
            candidates = {r.id for r in racks if r.id not in primary_ids and r.status == RackStatus.HEALTHY}
            if not candidates:
                error_message = "No healthy rack is available to redistribute workload onto."
            else:
                redistribute_ids = candidates

        primary_names = ", ".join(racks_by_id[rid].name for rid in primary_ids) or "the affected rack(s)"
        redistribute_names = (
            ", ".join(racks_by_id[rid].name for rid in redistribute_ids) if redistribute_ids else "other racks"
        )

        async with AsyncSessionLocal() as db:
            if error_message is not None:
                row = Execution(
                    decision_id=decision.id,
                    cluster_id=cluster_db_id,
                    scenario_id=scenario_db_id,
                    action_type=action_type,
                    status=ExecutionStatus.FAILED,
                    affected_racks=primary_ids,
                    summary=f"Execution failed: {error_message}",
                    error_message=error_message,
                    completed_at=now,
                )
                db.add(row)
                await db.commit()
                await db.refresh(row)
                persisted = await persist_events(
                    db, [self._event_draft(row, "Execution Failed", EventSeverity.WARNING)]
                )
                self._remember(row)
                logger.warning("Execution failed for decision %s: %s", decision.id, error_message)
                return row, persisted

            assert action_type is not None  # narrowed: error_message is None only when this holds
            template = _SUMMARY_TEMPLATES[action_type]
            summary = template.format(primary=primary_names, redistribute=redistribute_names)

            row = Execution(
                decision_id=decision.id,
                cluster_id=cluster_db_id,
                scenario_id=scenario_db_id,
                action_type=action_type,
                status=ExecutionStatus.RUNNING,
                affected_racks=primary_ids,
                summary=summary,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            persisted = await persist_events(db, [self._event_draft(row, "Execution Started", EventSeverity.INFO)])

        self._manager.start(row.id, action_type, set(primary_ids), redistribute_ids, now)
        self._remember(row)
        logger.info("Execution started: %s on %s", action_type.value, primary_names)
        return row, persisted

    # --- per-tick lifecycle --------------------------------------------------

    async def tick(self, now: datetime) -> tuple[dict[uuid.UUID, RackDrivers], list[Event]]:
        """Advance every active execution. Returns this tick's combined
        per-rack driver contribution plus any lifecycle events (an action
        reaching full effect, or fully completing).
        """
        result = self._manager.compute_tick(now)
        if not result.took_effect and not result.finished:
            return result.drivers, []

        event_drafts: list[EventDraft] = []
        async with AsyncSessionLocal() as db:
            for execution_id in result.took_effect:
                row = await db.get(Execution, execution_id)
                if row is None or row.action_type is None:
                    continue
                title = _TOOK_EFFECT_TITLES.get(row.action_type)
                if title is not None:
                    event_drafts.append(self._event_draft(row, title, EventSeverity.INFO))

            for execution_id in result.finished:
                row = await db.get(Execution, execution_id)
                if row is None:
                    continue
                row.status = ExecutionStatus.COMPLETED
                row.completed_at = now
                event_drafts.append(self._event_draft(row, "Execution Completed", EventSeverity.INFO))
                self._remember(row)

            persisted = await persist_events(db, event_drafts) if event_drafts else []

        for event in persisted:
            logger.info("Execution event: [%s] %s", event.severity.value, event.title)
        return result.drivers, persisted

    # --- internals -----------------------------------------------------------

    def _remember(self, execution: Execution) -> None:
        self._cache[execution.id] = execution
        if len(self._cache) > MAX_CACHE_SIZE:
            oldest_id = next(iter(self._cache))
            if oldest_id != execution.id:
                self._cache.pop(oldest_id, None)

    @staticmethod
    def _event_draft(execution: Execution, title: str, severity: EventSeverity) -> EventDraft:
        return EventDraft(
            cluster_id=execution.cluster_id,
            rack_id=execution.affected_racks[0] if execution.affected_racks else None,
            scenario_id=execution.scenario_id,
            severity=severity,
            title=title,
            message=execution.summary,
        )
