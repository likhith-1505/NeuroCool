"""Unit tests for the NeuroCore tool framework — schema validation, unknown
tool/argument rejection, permission checks, and read tool execution. No
database required: read tools only ever touch a hand-built fake
SimulationPort (and, for read_recent_events, a real db fixture — the one
tool that queries Events directly).
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.models.enums import ExecutionActionType, ExecutionStatus, RackStatus
from app.models.execution import Execution
from app.neurocore.permissions import Permission, PermissionDenied, Principal, require
from app.neurocore.providers.base import ToolCall
from app.neurocore.tools.base import ToolContext, ToolExecutionError
from app.neurocore.tools.executor import execute_tool_call
from app.neurocore.tools.read_tools import (
    ReadClusterStateTool,
    ReadExecutionHistoryTool,
    ReadForecastTool,
    ReadOptimizationPlanTool,
    ReadRackTool,
)
from app.neurocore.tools.registry import ALL_TOOLS, get_tool, tool_specs
from app.neurocore.tools.write_tools import ExecuteDecisionTool, PendingActionProposal, ReplaySimulationTool
from app.simulation.state import ClusterState, RackState

# No blanket `pytestmark = pytest.mark.asyncio` — this file mixes sync
# (registry/permission) and async (tool execution) tests; pytest.ini's
# asyncio_mode=auto already detects async def tests on its own.


def _make_rack(**overrides: object) -> RackState:
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(), name="Rack A1", temperature=65.0, gpu_utilization=55.0,
        cpu_utilization=40.0, power_draw=9.0, cooling_efficiency=60.0, fan_speed=40.0,
        health_score=90.0, prediction_state="stable", running_jobs=10, status=RackStatus.HEALTHY,
    )
    defaults.update(overrides)
    return RackState(**defaults)  # type: ignore[arg-type]


class _FakeSimulation:
    def __init__(self, racks: list[RackState] | None = None, executions: list | None = None) -> None:
        self._racks = {r.id: r for r in (racks or [])}
        self._executions = list(executions or [])

    @property
    def cluster_state(self) -> ClusterState:
        return ClusterState(
            id=uuid.uuid4(), name="Test Cluster", overall_health=90.0, average_temperature=65.0,
            total_power=sum(r.power_draw for r in self._racks.values()), cooling_efficiency=60.0,
            energy_savings=15.0, prediction_confidence=90.0,
        )

    @property
    def rack_states(self):
        return list(self._racks.values())

    def rack_state(self, rack_id):
        return self._racks.get(rack_id)

    @property
    def scenario_status(self):
        from app.schemas.scenario import ScenarioStatus

        return ScenarioStatus(key="normal", name="Normal", transition_state="steady", target_rack_id=None, activated_at=datetime.now(UTC))

    @property
    def cluster_forecast(self):
        return []

    @property
    def rack_forecasts(self):
        return {}

    def rack_forecast(self, rack_id):
        return []

    @property
    def active_plans(self):
        return []

    @property
    def all_plans(self):
        return []

    def get_plan(self, plan_id):
        return None

    @property
    def active_decisions(self):
        return []

    @property
    def all_decisions(self):
        return []

    def get_decision(self, decision_id):
        return None

    @property
    def all_executions(self):
        return list(self._executions)

    def get_execution(self, execution_id):
        return next((e for e in self._executions if e.id == execution_id), None)


class _StubPendingActions:
    """Records calls instead of touching a database — good enough for
    write_tools.py's own thin pass-through logic, which is the only thing
    under test here (the real PendingActionService is covered by
    tests/test_pending_actions.py).
    """

    def __init__(self) -> None:
        self.create_for_decision_calls: list[uuid.UUID] = []
        self.create_for_replay_calls = 0

    async def create_for_decision(self, db, *, conversation_id, decision_id, simulation, now=None):
        self.create_for_decision_calls.append(decision_id)
        from app.models.enums import PendingActionStatus, PendingActionType
        from app.models.pending_action import PendingAction

        return PendingAction(
            id=uuid.uuid4(), conversation_id=conversation_id, plan_id=None, decision_id=decision_id,
            action_type=PendingActionType.EXECUTE_DECISION, target="Rack A1", status=PendingActionStatus.PENDING,
            summary="I can execute the recommended action. Proceed?", scenario_key="normal",
            execution_id=None, expires_at=datetime.now(UTC),
        )

    async def create_for_replay(self, db, *, conversation_id, simulation, now=None):
        self.create_for_replay_calls += 1
        from app.models.enums import PendingActionStatus, PendingActionType
        from app.models.pending_action import PendingAction

        return PendingAction(
            id=uuid.uuid4(), conversation_id=conversation_id, plan_id=None, decision_id=None,
            action_type=PendingActionType.REPLAY_SIMULATION, target="cluster", status=PendingActionStatus.PENDING,
            summary="I can replay the scenario. Proceed?", scenario_key="normal",
            execution_id=None, expires_at=datetime.now(UTC),
        )


def _make_context(simulation=None, pending_actions=None, principal=None) -> ToolContext:
    return ToolContext(
        db=None,  # type: ignore[arg-type]
        simulation=simulation or _FakeSimulation(),
        principal=principal or Principal(),
        conversation_id=uuid.uuid4(),
        pending_actions=pending_actions or _StubPendingActions(),
    )


# --- registry --------------------------------------------------------------


def test_registry_exposes_every_required_tool() -> None:
    names = {tool.name for tool in ALL_TOOLS}
    assert names == {
        "read_cluster_state", "read_rack", "read_forecast", "read_optimization_plan",
        "read_decision", "read_recent_events", "read_execution_history",
        "execute_decision", "replay_simulation",
    }


def test_tool_specs_are_json_schema_shaped() -> None:
    specs = tool_specs()
    assert len(specs) == len(ALL_TOOLS)
    for spec in specs:
        assert isinstance(spec["input_schema"], dict)
        assert spec["input_schema"].get("type") == "object"
        assert spec["description"]


def test_get_tool_returns_none_for_unknown_name() -> None:
    assert get_tool("delete_everything") is None


# --- permissions -----------------------------------------------------------


def test_require_passes_when_principal_has_permission() -> None:
    principal = Principal(permissions=frozenset({Permission.READ_ONLY}))
    require(principal, Permission.READ_ONLY)  # does not raise


def test_require_raises_when_principal_lacks_permission() -> None:
    principal = Principal(permissions=frozenset({Permission.READ_ONLY}))
    with pytest.raises(PermissionDenied):
        require(principal, Permission.OPERATE)


def test_write_tools_require_operate_permission() -> None:
    assert ExecuteDecisionTool.required_permission == Permission.OPERATE
    assert ReplaySimulationTool.required_permission == Permission.OPERATE


def test_read_tools_require_only_read_only_permission() -> None:
    assert ReadRackTool.required_permission == Permission.READ_ONLY
    assert ReadClusterStateTool.required_permission == Permission.READ_ONLY


# --- read tool execution -------------------------------------------------


async def test_read_cluster_state_tool_returns_real_cluster_data() -> None:
    rack = _make_rack(power_draw=9.5)
    context = _make_context(simulation=_FakeSimulation([rack]))
    result = await ReadClusterStateTool().run(ReadClusterStateTool.input_schema(), context)
    assert result.total_power == 9.5
    assert result.scenario_key == "normal"


async def test_read_rack_tool_returns_the_matching_rack() -> None:
    rack = _make_rack(name="Rack A1", temperature=77.5)
    context = _make_context(simulation=_FakeSimulation([rack]))
    result = await ReadRackTool().run(ReadRackTool.input_schema(rack_id=rack.id), context)
    assert result.name == "Rack A1"
    assert result.temperature == 77.5


async def test_read_rack_tool_raises_for_unknown_rack() -> None:
    context = _make_context(simulation=_FakeSimulation([]))
    with pytest.raises(ToolExecutionError):
        await ReadRackTool().run(ReadRackTool.input_schema(rack_id=uuid.uuid4()), context)


async def test_read_forecast_tool_raises_for_unknown_rack() -> None:
    context = _make_context(simulation=_FakeSimulation([]))
    with pytest.raises(ToolExecutionError):
        await ReadForecastTool().run(ReadForecastTool.input_schema(rack_id=uuid.uuid4()), context)


async def test_read_optimization_plan_requires_at_least_one_argument() -> None:
    context = _make_context()
    with pytest.raises(ToolExecutionError):
        await ReadOptimizationPlanTool().run(ReadOptimizationPlanTool.input_schema(), context)


# --- write tool execution (create-only; real execution is confirm()'s job) --


async def test_execute_decision_tool_creates_a_pending_action_and_never_executes() -> None:
    stub = _StubPendingActions()
    context = _make_context(pending_actions=stub)
    decision_id = uuid.uuid4()

    result = await ExecuteDecisionTool().run(ExecuteDecisionTool.input_schema(decision_id=decision_id), context)

    assert isinstance(result, PendingActionProposal)
    assert result.status.value == "pending"
    assert stub.create_for_decision_calls == [decision_id]


async def test_replay_simulation_tool_creates_a_pending_action() -> None:
    stub = _StubPendingActions()
    context = _make_context(pending_actions=stub)

    result = await ReplaySimulationTool().run(ReplaySimulationTool.input_schema(), context)

    assert isinstance(result, PendingActionProposal)
    assert stub.create_for_replay_calls == 1


# --- executor: validation / rejection -------------------------------------


async def test_executor_rejects_unknown_tool_name() -> None:
    context = _make_context()
    call = ToolCall(id="call_1", name="delete_everything", arguments={})
    outcome = await execute_tool_call(call, context)
    assert outcome.ok is False
    assert "unknown tool" in outcome.result_json.lower()


async def test_executor_rejects_unknown_arguments() -> None:
    rack = _make_rack()
    context = _make_context(simulation=_FakeSimulation([rack]))
    call = ToolCall(id="call_1", name="read_rack", arguments={"rack_id": str(rack.id), "sudo": True})
    outcome = await execute_tool_call(call, context)
    assert outcome.ok is False
    assert "invalid arguments" in outcome.result_json.lower()


async def test_executor_rejects_malformed_uuid_argument() -> None:
    context = _make_context()
    call = ToolCall(id="call_1", name="read_rack", arguments={"rack_id": "not-a-real-uuid"})
    outcome = await execute_tool_call(call, context)
    assert outcome.ok is False


async def test_executor_rejects_missing_required_argument() -> None:
    context = _make_context()
    call = ToolCall(id="call_1", name="read_rack", arguments={})
    outcome = await execute_tool_call(call, context)
    assert outcome.ok is False


async def test_executor_succeeds_for_a_valid_read_tool_call() -> None:
    rack = _make_rack()
    context = _make_context(simulation=_FakeSimulation([rack]))
    call = ToolCall(id="call_1", name="read_rack", arguments={"rack_id": str(rack.id)})
    outcome = await execute_tool_call(call, context)
    assert outcome.ok is True
    assert outcome.creates_pending_action is False
    assert str(rack.id) in outcome.result_json


async def test_executor_marks_write_tool_outcome_as_pending_action() -> None:
    stub = _StubPendingActions()
    context = _make_context(pending_actions=stub)
    decision_id = uuid.uuid4()
    call = ToolCall(id="call_1", name="execute_decision", arguments={"decision_id": str(decision_id)})

    outcome = await execute_tool_call(call, context)

    assert outcome.ok is True
    assert outcome.creates_pending_action is True
    assert outcome.pending_action_id is not None
    assert outcome.confirmation_text and "proceed" in outcome.confirmation_text.lower()


async def test_executor_denies_write_tool_without_operate_permission() -> None:
    principal = Principal(permissions=frozenset({Permission.READ_ONLY}))
    stub = _StubPendingActions()
    context = _make_context(pending_actions=stub, principal=principal)
    call = ToolCall(id="call_1", name="execute_decision", arguments={"decision_id": str(uuid.uuid4())})

    outcome = await execute_tool_call(call, context)

    assert outcome.ok is False
    assert "permission" in outcome.result_json.lower()
    assert stub.create_for_decision_calls == []  # never reached the service


# --- read_execution_history / read_recent_events --------------------------


async def test_read_execution_history_tool_filters_by_rack() -> None:
    rack = _make_rack()
    other_rack_id = uuid.uuid4()
    matching = Execution(
        id=uuid.uuid4(), decision_id=uuid.uuid4(), cluster_id=uuid.uuid4(), scenario_id=None,
        action_type=ExecutionActionType.WORKLOAD_MIGRATION, status=ExecutionStatus.COMPLETED,
        affected_racks=[rack.id], summary="Migrated.", error_message=None,
        started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
    )
    other = Execution(
        id=uuid.uuid4(), decision_id=uuid.uuid4(), cluster_id=uuid.uuid4(), scenario_id=None,
        action_type=ExecutionActionType.COOLING_ADJUSTMENT, status=ExecutionStatus.COMPLETED,
        affected_racks=[other_rack_id], summary="Adjusted cooling.", error_message=None,
        started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
    )
    context = _make_context(simulation=_FakeSimulation([rack], executions=[matching, other]))

    result = await ReadExecutionHistoryTool().run(ReadExecutionHistoryTool.input_schema(rack_id=rack.id), context)

    assert len(result.executions) == 1
    assert result.executions[0].id == matching.id


async def test_read_recent_events_tool_queries_real_events(db) -> None:
    from sqlalchemy import select

    from app.models.enums import EventSeverity
    from app.models.event import Event
    from app.neurocore.tools.read_tools import ReadRecentEventsTool

    # cluster_id left None (it's nullable) rather than a random UUID — a
    # fabricated cluster_id would violate the real foreign key constraint.
    event = Event(
        cluster_id=None, rack_id=None, scenario_id=None, severity=EventSeverity.INFO,
        title="Test event for read_recent_events", message="hello", occurred_at=datetime.now(UTC),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    context = ToolContext(
        db=db, simulation=_FakeSimulation([]), principal=Principal(), conversation_id=uuid.uuid4(),
        pending_actions=_StubPendingActions(),
    )
    result = await ReadRecentEventsTool().run(ReadRecentEventsTool.input_schema(limit=50), context)

    assert any(e.id == event.id for e in result.events)

    # cleanup — this test's own row shouldn't linger for other tests
    await db.execute(select(Event).where(Event.id == event.id))
    await db.delete(event)
    await db.commit()
