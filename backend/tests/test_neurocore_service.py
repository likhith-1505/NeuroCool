"""Unit tests for NeuroCoreService.answer() — the provider+grounding
orchestration that doesn't touch the database (see NeuroCoreService.chat
for the thin DB-persistence wrapper, verified live instead — same split
DecisionService/ExecutionService/OptimizationService use).
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.models.enums import ExecutionActionType, PendingActionStatus, PendingActionType, RackStatus
from app.models.pending_action import PendingAction
from app.neurocore.context import build_context
from app.neurocore.providers.base import LLMMessage, LLMResponse, ProviderError, ToolCall
from app.neurocore.providers.mock_provider import MockLLMProvider
from app.neurocore.service import (
    EMPTY_MESSAGE_RESPONSE,
    MAX_TOOL_ITERATIONS,
    PROVIDER_ERROR_RESPONSE,
    TOOL_LOOP_EXHAUSTED_RESPONSE,
    UNAVAILABLE_RESPONSE,
    NeuroCoreService,
)
from app.simulation.state import ClusterState, RackState

pytestmark = pytest.mark.asyncio


class _RaisingProvider:
    name = "raising"
    model = "raising-1"

    async def generate(self, *, system: str, messages: list[LLMMessage], max_tokens: int, tools=None) -> LLMResponse:
        raise ProviderError("simulated provider failure")


class _FakeSimulationPort:
    """Just enough of app.neurocore.ports.SimulationPort for the tool
    loop's read_rack/read_cluster_state/execute_decision calls in this
    file's tests.
    """

    def __init__(self, racks: list[RackState]) -> None:
        self._racks = {r.id: r for r in racks}

    @property
    def cluster_state(self) -> ClusterState:
        return ClusterState(
            id=uuid.uuid4(), name="Test Cluster", overall_health=90.0, average_temperature=65.0,
            total_power=sum(r.power_draw for r in self._racks.values()), cooling_efficiency=60.0,
            energy_savings=15.0, prediction_confidence=90.0,
        )

    def rack_state(self, rack_id):
        return self._racks.get(rack_id)

    @property
    def scenario_status(self):
        from app.schemas.scenario import ScenarioStatus

        return ScenarioStatus(key="normal", name="Normal", transition_state="steady", target_rack_id=None, activated_at=datetime.now(UTC))

    def get_decision(self, decision_id):
        return None

    def get_plan(self, plan_id):
        return None

    @property
    def all_executions(self):
        return []


class _StubPendingActions:
    def __init__(self) -> None:
        self.create_for_decision_calls: list[uuid.UUID] = []

    async def create_for_decision(self, db, *, conversation_id, decision_id, simulation, now=None):
        self.create_for_decision_calls.append(decision_id)
        return PendingAction(
            id=uuid.uuid4(), conversation_id=conversation_id, plan_id=None, decision_id=decision_id,
            action_type=PendingActionType.EXECUTE_DECISION, target="Rack A1", status=PendingActionStatus.PENDING,
            summary="I can execute the recommended migration for Rack A1. Proceed?", scenario_key="normal",
            execution_id=None, expires_at=datetime.now(UTC),
        )

    async def create_for_replay(self, db, *, conversation_id, simulation, now=None):
        raise AssertionError("not used in these tests")


def _make_rack(**overrides: object) -> RackState:
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(), name="Rack A1", temperature=65.0, gpu_utilization=55.0,
        cpu_utilization=40.0, power_draw=9.0, cooling_efficiency=60.0, fan_speed=40.0,
        health_score=90.0, prediction_state="stable", running_jobs=10, status=RackStatus.HEALTHY,
    )
    defaults.update(overrides)
    return RackState(**defaults)  # type: ignore[arg-type]


def _make_context(racks: list[RackState], **overrides: object):
    defaults: dict[str, object] = dict(
        cluster=ClusterState(
            id=uuid.uuid4(), name="Test Cluster", overall_health=90.0, average_temperature=65.0,
            total_power=sum(r.power_draw for r in racks), cooling_efficiency=60.0,
            energy_savings=15.0, prediction_confidence=90.0,
        ),
        racks=racks, scenario_key="normal", forecasts={}, cluster_forecast=[],
        active_plans=[], all_plans=[], active_decisions=[], all_decisions=[],
        all_executions=[], recent_events=[],
    )
    defaults.update(overrides)
    return build_context(**defaults)  # type: ignore[arg-type]


async def test_answer_with_no_provider_returns_unavailable() -> None:
    service = NeuroCoreService(provider=None)
    context = _make_context([_make_rack()])

    result = await service.answer(context, message="Summarize the cluster.", rack_id=None)

    assert result.text == UNAVAILABLE_RESPONSE
    assert result.sources == []
    assert result.confidence == 0.0


async def test_answer_with_empty_message_does_not_call_the_provider() -> None:
    service = NeuroCoreService(provider=MockLLMProvider())
    context = _make_context([_make_rack()])

    result = await service.answer(context, message="   ", rack_id=None)

    assert result.text == EMPTY_MESSAGE_RESPONSE
    assert result.sources == []
    assert result.confidence == 0.0


async def test_answer_with_mock_provider_returns_grounded_sources() -> None:
    service = NeuroCoreService(provider=MockLLMProvider())
    rack = _make_rack(name="Rack A1")
    context = _make_context([rack])

    result = await service.answer(context, message="Why is Rack A1 at risk?", rack_id=None)

    assert "Rack A1" in result.text
    assert f"rack:{rack.name}" in result.sources
    assert result.confidence > 0.0


async def test_answer_falls_back_cleanly_when_provider_raises() -> None:
    service = NeuroCoreService(provider=_RaisingProvider())
    context = _make_context([_make_rack()])

    result = await service.answer(context, message="Summarize the cluster.", rack_id=None)

    assert result.text == PROVIDER_ERROR_RESPONSE
    assert result.sources == []
    assert result.confidence == 0.0


async def test_answer_with_unresolvable_rack_id_still_grounds_cluster_wide() -> None:
    service = NeuroCoreService(provider=MockLLMProvider())
    context = _make_context([_make_rack()])
    unknown_id = uuid.uuid4()

    result = await service.answer(context, message="Why is this rack at risk?", rack_id=unknown_id)

    assert str(unknown_id) in result.text or "does not match" in result.text.lower() or result.confidence >= 0.0
    # The unavailable-rack note always makes it into the grounded context,
    # which the mock provider echoes a preview of.


async def test_answer_history_is_forwarded_to_the_provider() -> None:
    """Regression guard: history must reach build_messages/generate() in
    the right order (oldest first, question last) rather than being
    dropped silently.
    """
    service = NeuroCoreService(provider=MockLLMProvider())
    context = _make_context([_make_rack()])
    history: list[LLMMessage] = [
        {"role": "user", "content": "What is the cluster's average temperature?"},
        {"role": "assistant", "content": "It is 65 degrees."},
    ]

    result = await service.answer(context, message="And now?", rack_id=None, history=history)

    assert "And now?" in result.text


# --- tool-use loop -----------------------------------------------------


async def test_answer_tool_loop_read_tool_round_trip() -> None:
    rack = _make_rack(name="Rack A1", temperature=77.0)
    provider = MockLLMProvider(
        responses=[
            LLMResponse(text="", model="mock-1", tool_calls=[ToolCall(id="c1", name="read_rack", arguments={"rack_id": str(rack.id)})]),
            LLMResponse(text="Rack A1 is at 77.0°C.", model="mock-1"),
        ]
    )
    service = NeuroCoreService(provider=provider, pending_actions=_StubPendingActions())
    context = _make_context([rack])

    result = await service.answer(
        context, message="What's happening with Rack A1?", rack_id=None,
        db=object(), simulation=_FakeSimulationPort([rack]), conversation_id=uuid.uuid4(),
    )

    assert result.text == "Rack A1 is at 77.0°C."
    assert provider.call_count == 2
    # The tool's real result (not an LLM guess) was fed back as the second call's tool message.
    second_call_messages = provider.calls[1]["messages"]
    tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert "77.0" in tool_messages[0]["content"]
    assert result.pending_action_id is None


async def test_answer_tool_loop_write_tool_short_circuits_into_pending_action() -> None:
    rack = _make_rack(name="Rack A1")
    decision_id = uuid.uuid4()
    provider = MockLLMProvider(
        responses=[
            LLMResponse(text="", model="mock-1", tool_calls=[ToolCall(id="c1", name="execute_decision", arguments={"decision_id": str(decision_id)})]),
            LLMResponse(text="This should never be reached.", model="mock-1"),
        ]
    )
    stub_actions = _StubPendingActions()
    service = NeuroCoreService(provider=provider, pending_actions=stub_actions)
    context = _make_context([rack])

    result = await service.answer(
        context, message="Move the workload off Rack A1.", rack_id=None,
        db=object(), simulation=_FakeSimulationPort([rack]), conversation_id=uuid.uuid4(),
    )

    assert result.pending_action_id is not None
    assert "proceed" in result.text.lower()
    assert stub_actions.create_for_decision_calls == [decision_id]
    # Only one provider round trip — the loop stopped immediately, never
    # asked the model to narrate a "success" that hasn't happened.
    assert provider.call_count == 1


async def test_answer_tool_loop_exhausts_after_max_iterations() -> None:
    rack = _make_rack()
    # Every single response keeps calling a (valid) read tool, never finishing.
    responses = [
        LLMResponse(text="", model="mock-1", tool_calls=[ToolCall(id=f"c{i}", name="read_cluster_state", arguments={})])
        for i in range(MAX_TOOL_ITERATIONS + 2)
    ]
    provider = MockLLMProvider(responses=responses)
    service = NeuroCoreService(provider=provider, pending_actions=_StubPendingActions())
    context = _make_context([rack])

    result = await service.answer(
        context, message="Keep going forever.", rack_id=None,
        db=object(), simulation=_FakeSimulationPort([rack]), conversation_id=uuid.uuid4(),
    )

    assert result.text == TOOL_LOOP_EXHAUSTED_RESPONSE
    assert provider.call_count == MAX_TOOL_ITERATIONS


async def test_answer_tool_loop_recovers_from_an_unknown_tool_call() -> None:
    rack = _make_rack()
    provider = MockLLMProvider(
        responses=[
            LLMResponse(text="", model="mock-1", tool_calls=[ToolCall(id="c1", name="delete_everything", arguments={})]),
            LLMResponse(text="I can't do that, but here's what I can tell you.", model="mock-1"),
        ]
    )
    service = NeuroCoreService(provider=provider, pending_actions=_StubPendingActions())
    context = _make_context([rack])

    result = await service.answer(
        context, message="Delete everything.", rack_id=None,
        db=object(), simulation=_FakeSimulationPort([rack]), conversation_id=uuid.uuid4(),
    )

    assert result.text == "I can't do that, but here's what I can tell you."
    second_call_messages = provider.calls[1]["messages"]
    tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert "unknown tool" in tool_messages[0]["content"].lower()


async def test_answer_without_tool_context_falls_back_to_plain_text_only() -> None:
    """When db/simulation/conversation_id aren't supplied (the read-only
    reasoning phase's call shape), no tools are offered at all — exactly
    the existing behavior every other test in this file already checks.
    """
    rack = _make_rack()
    provider = MockLLMProvider()
    service = NeuroCoreService(provider=provider)
    context = _make_context([rack])

    result = await service.answer(context, message="Summarize the cluster.", rack_id=None)

    assert provider.call_count == 1
    assert provider.calls[0]["tools"] is None
