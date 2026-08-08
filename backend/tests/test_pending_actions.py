"""Integration tests for PendingActionService — the confirm/cancel/expiry/
idempotency/re-validation flow (see app.neurocore.actions).

Needs a real database: the idempotency guarantee this module tests is a
genuine Postgres row-level-locking property (see PendingActionService's
own docstring) that cannot be meaningfully demonstrated against an
in-memory fake. The `db`/`db2` fixtures (see conftest.py) skip these tests
when Postgres isn't reachable rather than failing outright; the podman-
based verification this project runs before every commit does have a real
Postgres attached, which is where these tests actually run for real.

The simulation side (decisions/plans/racks/executing) is a small, faithful
fake (_FakeSimulation) conforming to app.neurocore.ports.SimulationPort —
its execute_decision() opens its own database session and really persists
an Execution row, exactly like the real SimulationService.execute_decision
does, so PendingAction.execution_id's foreign key is always satisfied.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.cluster import Cluster
from app.models.conversation import Conversation
from app.models.decision import Decision
from app.models.enums import (
    DecisionStatus,
    EventSeverity,
    ExecutionActionType,
    ExecutionStatus,
    PendingActionStatus,
    PendingActionType,
)
from app.models.execution import Execution
from app.models.optimization_plan import OptimizationPlan
from app.models.pending_action import PendingAction
from app.models.rack import Rack
from app.neurocore.actions import ActionStateConflict, PendingActionService
from app.schemas.scenario import ScenarioStatus

pytestmark = pytest.mark.asyncio


# --- DB row helpers ---------------------------------------------------


async def _make_cluster(db: AsyncSession) -> Cluster:
    cluster = Cluster(name=f"Test Cluster {uuid.uuid4()}")
    db.add(cluster)
    await db.commit()
    await db.refresh(cluster)
    return cluster


async def _make_rack(db: AsyncSession, cluster: Cluster, name: str = "Rack A1") -> Rack:
    rack = Rack(cluster_id=cluster.id, name=f"{name} {uuid.uuid4()}")
    db.add(rack)
    await db.commit()
    await db.refresh(rack)
    return rack


async def _make_conversation(db: AsyncSession) -> Conversation:
    conversation = Conversation()
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def _make_decision(
    db: AsyncSession, cluster: Cluster, rack: Rack, *, status: DecisionStatus = DecisionStatus.PENDING,
    plan: OptimizationPlan | None = None,
) -> Decision:
    decision = Decision(
        cluster_id=cluster.id,
        scenario_id=None,
        plan_id=plan.id if plan is not None else None,
        rule_key=f"workload_migration:{rack.id}",
        severity=EventSeverity.WARNING,
        title=f"Migrate workload off {rack.name}",
        reasoning="Rack is running hot.",
        recommended_action=f"Migrate workload off {rack.name} to a cooler rack.",
        expected_temperature_reduction=7.4,
        expected_power_saving=None,
        confidence=94.0,
        affected_racks=[rack.id],
        affected_jobs=[],
        alternative_actions=[],
        status=status,
    )
    db.add(decision)
    await db.commit()
    await db.refresh(decision)
    return decision


async def _make_plan(db: AsyncSession, cluster: Cluster, rack: Rack) -> OptimizationPlan:
    plan = OptimizationPlan(
        cluster_id=cluster.id,
        scenario_id=None,
        trigger_rack_id=rack.id,
        trigger_key=f"rack_plan:{rack.id}",
        trigger_reason=f"{rack.name} is running hot.",
        candidates=[],
        winner_action_type=None,
        winner_overall_score=None,
        winner_confidence=None,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


# --- fake SimulationPort -------------------------------------------------


class _FakeSimulation:
    """Conforms structurally to app.neurocore.ports.SimulationPort."""

    def __init__(
        self, *, racks: list[Rack], decisions: list[Decision], plans: list[OptimizationPlan] | None = None,
        scenario_key: str = "normal", execution_status: ExecutionStatus = ExecutionStatus.COMPLETED,
        execution_error: str | None = None,
    ) -> None:
        self._racks = {r.id: r for r in racks}
        self._decisions = {d.id: d for d in decisions}
        self._plans = {p.id: p for p in (plans or [])}
        self._scenario_key = scenario_key
        self._execution_status = execution_status
        self._execution_error = execution_error
        self._executions: list[Execution] = []
        self.execute_decision_calls: list[uuid.UUID] = []
        self.replay_calls = 0

    def rack_state(self, rack_id):
        return self._racks.get(rack_id)

    @property
    def scenario_status(self) -> ScenarioStatus:
        return ScenarioStatus(
            key=self._scenario_key, name=self._scenario_key.title(),
            transition_state="steady", target_rack_id=None, activated_at=datetime.now(UTC),
        )

    @property
    def active_plans(self):
        return list(self._plans.values())

    @property
    def all_plans(self):
        return list(self._plans.values())

    def get_plan(self, plan_id):
        return self._plans.get(plan_id)

    @property
    def active_decisions(self):
        return [d for d in self._decisions.values() if d.status in (DecisionStatus.PENDING, DecisionStatus.ACCEPTED)]

    @property
    def all_decisions(self):
        return list(self._decisions.values())

    def get_decision(self, decision_id):
        return self._decisions.get(decision_id)

    @property
    def all_executions(self):
        return list(self._executions)

    def get_execution(self, execution_id):
        return next((e for e in self._executions if e.id == execution_id), None)

    async def execute_decision(self, decision_id: uuid.UUID) -> Decision:
        self.execute_decision_calls.append(decision_id)
        decision = self._decisions.get(decision_id)
        if decision is None:
            raise LookupError(f"No decision found with id {decision_id}.")
        if decision.status not in (DecisionStatus.PENDING, DecisionStatus.ACCEPTED):
            raise ValueError(f"Decision '{decision_id}' is already {decision.status.value} and cannot be changed.")

        # Mirrors the real SimulationService.execute_decision: opens its
        # own session and really persists the resulting rows, so
        # PendingAction.execution_id's foreign key is always satisfiable.
        async with AsyncSessionLocal() as db:
            row = await db.get(Decision, decision_id)
            row.status = DecisionStatus.EXECUTED
            execution = Execution(
                decision_id=decision.id, cluster_id=decision.cluster_id, scenario_id=None,
                action_type=ExecutionActionType.WORKLOAD_MIGRATION, status=self._execution_status,
                affected_racks=decision.affected_racks,
                summary=f"Migrating workload off rack onto another." if self._execution_status == ExecutionStatus.COMPLETED
                else "Execution failed: no healthy rack available.",
                error_message=self._execution_error,
            )
            db.add(execution)
            await db.commit()
            await db.refresh(row)
            await db.refresh(execution)

        decision.status = DecisionStatus.EXECUTED
        self._executions.insert(0, execution)
        return decision

    async def replay_scenario(self) -> ScenarioStatus:
        self.replay_calls += 1
        return self.scenario_status


# --- creation ------------------------------------------------------------


async def test_create_for_decision_persists_a_pending_row_with_real_summary(db: AsyncSession) -> None:
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    service = PendingActionService()
    action = await service.create_for_decision(
        db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation
    )

    assert action.status == PendingActionStatus.PENDING
    assert action.action_type == PendingActionType.EXECUTE_DECISION
    assert action.decision_id == decision.id
    assert action.target == rack.name
    assert "7.4" in action.summary and "94" in action.summary
    assert action.expires_at > datetime.now(UTC)

    fetched = await service.get(db, action.id)
    assert fetched is not None
    assert fetched.status == PendingActionStatus.PENDING


async def test_create_for_decision_rejects_an_already_executed_decision(db: AsyncSession) -> None:
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack, status=DecisionStatus.EXECUTED)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    service = PendingActionService()
    with pytest.raises(ValueError, match="executed"):
        await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)


async def test_create_for_decision_rejects_unknown_decision(db: AsyncSession) -> None:
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[], decisions=[])

    service = PendingActionService()
    with pytest.raises(LookupError):
        await service.create_for_decision(db, conversation_id=conversation.id, decision_id=uuid.uuid4(), simulation=simulation)


async def test_create_for_replay_persists_a_pending_row(db: AsyncSession) -> None:
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[], decisions=[])

    service = PendingActionService()
    action = await service.create_for_replay(db, conversation_id=conversation.id, simulation=simulation)

    assert action.status == PendingActionStatus.PENDING
    assert action.action_type == PendingActionType.REPLAY_SIMULATION
    assert action.decision_id is None
    assert action.target == "cluster"


# --- confirmation / successful execution ------------------------------


async def test_confirm_executes_successfully(db: AsyncSession) -> None:
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    service = PendingActionService()
    action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)

    result = await service.confirm(db, action_id=action.id, simulation=simulation)

    assert result.status == PendingActionStatus.COMPLETED
    assert result.execution_id is not None
    assert result.confirmed_at is not None
    assert result.completed_at is not None
    assert simulation.execute_decision_calls == [decision.id]


async def test_confirm_marks_failed_on_execution_failure(db: AsyncSession) -> None:
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(
        racks=[rack], decisions=[decision],
        execution_status=ExecutionStatus.FAILED, execution_error="No healthy rack available to redistribute onto.",
    )

    service = PendingActionService()
    action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)
    result = await service.confirm(db, action_id=action.id, simulation=simulation)

    assert result.status == PendingActionStatus.FAILED
    assert result.execution_id is not None  # a real (failed) Execution row still exists
    assert "No healthy rack" in (result.error_message or "")


async def test_confirm_replay_action(db: AsyncSession) -> None:
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[], decisions=[])

    service = PendingActionService()
    action = await service.create_for_replay(db, conversation_id=conversation.id, simulation=simulation)
    result = await service.confirm(db, action_id=action.id, simulation=simulation)

    assert result.status == PendingActionStatus.COMPLETED
    assert simulation.replay_calls == 1


# --- cancellation ----------------------------------------------------------


async def test_cancel_pending_action(db: AsyncSession) -> None:
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    service = PendingActionService()
    action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)
    result = await service.cancel(db, action_id=action.id)

    assert result.status == PendingActionStatus.CANCELLED
    assert simulation.execute_decision_calls == []  # never executed


async def test_cancel_unknown_action_raises_lookup_error(db: AsyncSession) -> None:
    service = PendingActionService()
    with pytest.raises(LookupError):
        await service.cancel(db, action_id=uuid.uuid4())


async def test_cancel_already_confirmed_action_raises_conflict(db: AsyncSession) -> None:
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    service = PendingActionService()
    action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)
    await service.confirm(db, action_id=action.id, simulation=simulation)

    with pytest.raises(ActionStateConflict):
        await service.cancel(db, action_id=action.id)


# --- double confirmation (sequential) --------------------------------------


async def test_double_confirmation_only_executes_once(db: AsyncSession) -> None:
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    service = PendingActionService()
    action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)

    first = await service.confirm(db, action_id=action.id, simulation=simulation)
    assert first.status == PendingActionStatus.COMPLETED

    with pytest.raises(ActionStateConflict):
        await service.confirm(db, action_id=action.id, simulation=simulation)

    assert simulation.execute_decision_calls == [decision.id]  # exactly one execution


# --- concurrent confirmation (real race, two independent sessions) --------


async def test_concurrent_confirmation_results_in_exactly_one_execution(db: AsyncSession) -> None:
    """The idempotency guarantee the objective explicitly asks to be
    tested: two confirm() calls firing at (as close to) the same instant
    as asyncio allows, each on its own database session/connection —
    simulating two concurrent HTTP requests hitting
    POST /api/ai/actions/{id}/confirm at once. Setup runs on the `db`
    fixture's session (same event loop as the two concurrent calls below,
    so no cross-loop connection issue — see conftest.py's `db` fixture for
    why that matters here).
    """
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)

    simulation = _FakeSimulation(racks=[rack], decisions=[decision])
    service = PendingActionService()
    action = await service.create_for_decision(
        db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation
    )
    action_id = action.id

    async def _confirm_with_own_session():
        async with AsyncSessionLocal() as db:
            try:
                return await service.confirm(db, action_id=action_id, simulation=simulation)
            except ActionStateConflict as exc:
                return exc

    results = await asyncio.gather(_confirm_with_own_session(), _confirm_with_own_session())

    outcomes = [r for r in results if not isinstance(r, ActionStateConflict)]
    conflicts = [r for r in results if isinstance(r, ActionStateConflict)]

    assert len(outcomes) == 1, "exactly one of the two concurrent confirmations should have proceeded"
    assert len(conflicts) == 1, "the other should have been rejected as a conflict, not executed"
    assert outcomes[0].status == PendingActionStatus.COMPLETED
    assert simulation.execute_decision_calls == [decision.id]  # never called twice


# --- expiration --------------------------------------------------------


async def test_expired_action_cannot_be_confirmed(db: AsyncSession) -> None:
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    service = PendingActionService()
    action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)

    far_future = datetime.now(UTC) + timedelta(seconds=301)
    with pytest.raises(ActionStateConflict, match="expired"):
        await service.confirm(db, action_id=action.id, simulation=simulation, now=far_future)

    refreshed = await service.get(db, action.id)
    assert refreshed is not None
    assert refreshed.status == PendingActionStatus.EXPIRED


async def test_get_lazily_expires_a_stale_pending_action(db: AsyncSession) -> None:
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    service = PendingActionService()
    action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)

    far_future = datetime.now(UTC) + timedelta(seconds=301)
    fetched = await service.get(db, action.id, now=far_future)

    assert fetched is not None
    assert fetched.status == PendingActionStatus.EXPIRED


# --- stale plan / decision re-validation ------------------------------


async def test_confirm_rejects_when_plan_no_longer_known_to_live_service(db: AsyncSession) -> None:
    """The plan row still exists in the database (satisfying the foreign
    key), but the live in-memory OptimizationService no longer knows about
    it (e.g. it aged out of its bounded cache) — get_plan() returning None
    is exactly what that looks like. confirm() must not blindly trust the
    plan_id captured when the PendingAction was created.
    """
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    plan = await _make_plan(db, cluster, rack)
    decision = await _make_decision(db, cluster, rack, plan=plan)
    conversation = await _make_conversation(db)
    # Deliberately omit `plan` from the fake's known plans.
    simulation = _FakeSimulation(racks=[rack], decisions=[decision], plans=[])

    service = PendingActionService()
    action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)
    assert action.plan_id == plan.id

    result = await service.confirm(db, action_id=action.id, simulation=simulation)

    assert result.status == PendingActionStatus.FAILED
    assert "plan" in (result.error_message or "").lower()
    assert simulation.execute_decision_calls == []  # never actually executed


async def test_confirm_rejects_when_decision_already_resolved_since_proposal(db: AsyncSession) -> None:
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    service = PendingActionService()
    action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)

    # Something else (e.g. a direct POST /api/decisions/{id}/execute call)
    # resolved the decision after the PendingAction was proposed.
    decision.status = DecisionStatus.REJECTED

    result = await service.confirm(db, action_id=action.id, simulation=simulation)

    assert result.status == PendingActionStatus.FAILED
    assert "rejected" in (result.error_message or "").lower()
    assert simulation.execute_decision_calls == []


async def test_confirm_rejects_when_target_rack_no_longer_exists(db: AsyncSession) -> None:
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    service = PendingActionService()
    action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)

    # The rack was decommissioned/removed from the live simulation state.
    simulation._racks.pop(rack.id)

    result = await service.confirm(db, action_id=action.id, simulation=simulation)

    assert result.status == PendingActionStatus.FAILED
    assert "rack" in (result.error_message or "").lower()


async def test_confirm_rejects_when_scenario_changed_since_proposal(db: AsyncSession) -> None:
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision], scenario_key="thermal_spike")

    service = PendingActionService()
    action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)
    assert action.scenario_key == "thermal_spike"

    simulation._scenario_key = "normal"  # the incident resolved on its own before confirmation

    result = await service.confirm(db, action_id=action.id, simulation=simulation)

    assert result.status == PendingActionStatus.FAILED
    assert "scenario" in (result.error_message or "").lower()


# --- listing -------------------------------------------------------------


async def test_list_filters_by_conversation_and_status(db: AsyncSession) -> None:
    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision_a = await _make_decision(db, cluster, rack)
    decision_b = await _make_decision(db, cluster, rack)
    conversation_a = await _make_conversation(db)
    conversation_b = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision_a, decision_b])

    service = PendingActionService()
    action_a = await service.create_for_decision(db, conversation_id=conversation_a.id, decision_id=decision_a.id, simulation=simulation)
    await service.create_for_decision(db, conversation_id=conversation_b.id, decision_id=decision_b.id, simulation=simulation)

    only_a = await service.list(db, conversation_id=conversation_a.id)
    assert {a.id for a in only_a} == {action_a.id}

    only_pending = await service.list(db, status=PendingActionStatus.PENDING)
    assert action_a.id in {a.id for a in only_pending}


# --- WebSocket broadcasting ----------------------------------------------


class _FakeWebSocket:
    """Minimal stand-in for a FastAPI WebSocket — just enough for
    app.websocket.manager.ConnectionManager to accept it and record what
    gets sent, without a real network connection.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def accept(self) -> None:
        pass

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


async def test_lifecycle_transitions_broadcast_over_the_existing_websocket_manager(db: AsyncSession) -> None:
    """Reuses app.websocket.manager.manager (the same connection registry
    the telemetry stream uses) — no second WebSocket system. Distinct
    message shape (a "type" key) from the regular per-tick snapshot.
    """
    from app.websocket.manager import manager

    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    fake_ws = _FakeWebSocket()
    await manager.connect(fake_ws)
    try:
        service = PendingActionService()
        action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)
        await service.confirm(db, action_id=action.id, simulation=simulation)
    finally:
        await manager.disconnect(fake_ws)

    event_types = [message["type"] for message in fake_ws.sent]
    assert "AI_ACTION_PENDING" in event_types
    assert "AI_ACTION_CONFIRMED" in event_types
    assert "AI_ACTION_EXECUTING" in event_types
    assert "AI_ACTION_COMPLETED" in event_types
    # Every broadcast carries the real, current action row — not a
    # fabricated summary.
    for message in fake_ws.sent:
        assert message["action"]["id"] == str(action.id)


async def test_cancellation_broadcasts_ai_action_cancelled(db: AsyncSession) -> None:
    from app.websocket.manager import manager

    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    fake_ws = _FakeWebSocket()
    await manager.connect(fake_ws)
    try:
        service = PendingActionService()
        action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)
        await service.cancel(db, action_id=action.id)
    finally:
        await manager.disconnect(fake_ws)

    event_types = [message["type"] for message in fake_ws.sent]
    assert "AI_ACTION_CANCELLED" in event_types


async def test_failed_revalidation_broadcasts_ai_action_failed(db: AsyncSession) -> None:
    from app.websocket.manager import manager

    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    fake_ws = _FakeWebSocket()
    await manager.connect(fake_ws)
    try:
        service = PendingActionService()
        action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)
        decision.status = DecisionStatus.REJECTED  # invalidate before confirming
        await service.confirm(db, action_id=action.id, simulation=simulation)
    finally:
        await manager.disconnect(fake_ws)

    event_types = [message["type"] for message in fake_ws.sent]
    assert "AI_ACTION_FAILED" in event_types


# --- audit logging -------------------------------------------------------


async def test_audit_log_records_proposal_and_execution(db: AsyncSession) -> None:
    from sqlalchemy import select

    from app.models.audit_log import ActionAuditLog

    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    service = PendingActionService()
    action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)
    await service.confirm(db, action_id=action.id, simulation=simulation)

    result = await db.execute(
        select(ActionAuditLog).where(ActionAuditLog.pending_action_id == action.id).order_by(ActionAuditLog.occurred_at)
    )
    rows = list(result.scalars().all())
    labels = [row.action for row in rows]

    assert "proposed" in labels
    assert "confirmed" in labels
    assert "executed" in labels
    for row in rows:
        assert row.conversation_id == conversation.id
        assert row.decision_id == decision.id
        # No secret-shaped content anywhere in the audit trail.
        assert "api_key" not in row.result.lower()
        assert "authorization" not in row.result.lower()


async def test_audit_log_records_failure_reason(db: AsyncSession) -> None:
    from sqlalchemy import select

    from app.models.audit_log import ActionAuditLog

    cluster = await _make_cluster(db)
    rack = await _make_rack(db, cluster)
    decision = await _make_decision(db, cluster, rack)
    conversation = await _make_conversation(db)
    simulation = _FakeSimulation(racks=[rack], decisions=[decision])

    service = PendingActionService()
    action = await service.create_for_decision(db, conversation_id=conversation.id, decision_id=decision.id, simulation=simulation)
    decision.status = DecisionStatus.REJECTED
    await service.confirm(db, action_id=action.id, simulation=simulation)

    result = await db.execute(select(ActionAuditLog).where(ActionAuditLog.pending_action_id == action.id))
    rows = list(result.scalars().all())

    rejected_rows = [row for row in rows if row.action == "rejected"]
    assert len(rejected_rows) == 1
    assert rejected_rows[0].success is False
    assert "rejected" in rejected_rows[0].result.lower()
