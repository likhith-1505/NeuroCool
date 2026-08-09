"""Tests for NeuroCore streaming — POST /api/ai/chat/stream's underlying
machinery (see app.neurocore.service.NeuroCoreService.answer_stream/
chat_stream, app.neurocore.providers.mock_provider's streaming support,
app.neurocore.providers.sse, and app.schemas.ai_stream). Everything here
runs against MockLLMProvider or a small hand-built test double — no real
LLM API key is ever required, per the objective.

HTTP-level (POST /api/ai/chat/stream route, SSE framing over the wire) and
WebSocket-regression coverage lives in tests/test_ai_stream_api.py instead.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationMessage
from app.models.enums import PendingActionStatus, PendingActionType, RackStatus
from app.models.pending_action import PendingAction
from app.neurocore.context import build_context
from app.neurocore.providers.base import LLMResponse, LLMStreamChunk, ProviderError, ToolCall
from app.neurocore.providers.mock_provider import MockLLMProvider, NonStreamingMockProvider
from app.neurocore.providers.sse import iter_sse_events
from app.neurocore.service import (
    _TOOL_THINKING_MESSAGES,
    EMPTY_MESSAGE_RESPONSE,
    NeuroCoreService,
)
from app.schemas.ai_stream import (
    ActionConfirmationRequiredEvent,
    CompletedEvent,
    ErrorEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolCompletedEvent,
    ToolStartedEvent,
    encode_sse,
)
from app.simulation.state import ClusterState, RackState

# No blanket `pytestmark = pytest.mark.asyncio` here — this file mixes
# sync (SSE framing, mock-provider determinism) and async tests, and
# pytest.ini's asyncio_mode=auto already detects async def tests on its
# own; marking every test would just produce a PytestWarning on the sync
# ones (see tests/test_neurocore_providers.py for the same convention).


# --- shared fixtures/helpers (mirrors tests/test_neurocore_service.py) ---


class _FakeSimulationPort:
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
        from datetime import UTC, datetime

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
    """Mirrors tests/test_neurocore_service.py's stub, extended with a
    `.get()` — chat_stream/answer_stream's action-confirmation path looks
    the full row back up (via ToolContext.pending_actions.get) right after
    a write tool creates it, to fill in the event's action_type/expires_at.
    """

    def __init__(self) -> None:
        self.create_for_decision_calls: list[uuid.UUID] = []
        self._rows: dict[uuid.UUID, PendingAction] = {}

    async def create_for_decision(self, db, *, conversation_id, decision_id, simulation, now=None):
        self.create_for_decision_calls.append(decision_id)
        from datetime import UTC, datetime, timedelta

        row = PendingAction(
            id=uuid.uuid4(), conversation_id=conversation_id, plan_id=None, decision_id=decision_id,
            action_type=PendingActionType.EXECUTE_DECISION, target="Rack A1", status=PendingActionStatus.PENDING,
            summary="I can execute the recommended migration for Rack A1. Proceed?", scenario_key="normal",
            execution_id=None, expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        self._rows[row.id] = row
        return row

    async def create_for_replay(self, db, *, conversation_id, simulation, now=None):
        raise AssertionError("not used in these tests")

    async def get(self, db, action_id, *, now=None):
        return self._rows.get(action_id)


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


async def _collect(agen):
    return [event async for event in agen]


# --- app.neurocore.providers.sse ------------------------------------------


async def _lines(items: list[str]):
    for item in items:
        yield item


async def test_iter_sse_events_parses_named_and_unnamed_events() -> None:
    raw = ["event: text_delta", 'data: {"text": "hi"}', "", 'data: {"type": "message_stop"}', ""]
    events = await _collect(iter_sse_events(_lines(raw)))
    assert events == [("text_delta", {"text": "hi"}), (None, {"type": "message_stop"})]


async def test_iter_sse_events_skips_malformed_json_without_raising() -> None:
    raw = ["data: {this is not json", "", "data: {\"ok\": true}", ""]
    events = await _collect(iter_sse_events(_lines(raw)))
    assert events[0] == (None, None)
    assert events[1] == (None, {"ok": True})


async def test_iter_sse_events_swallows_the_openai_done_sentinel() -> None:
    raw = ['data: {"a": 1}', "", "data: [DONE]", ""]
    events = await _collect(iter_sse_events(_lines(raw)))
    assert events == [(None, {"a": 1})]


async def test_iter_sse_events_ignores_comment_lines() -> None:
    raw = [": keep-alive", 'data: {"a": 1}', ""]
    events = await _collect(iter_sse_events(_lines(raw)))
    assert events == [(None, {"a": 1})]


async def test_iter_sse_events_flushes_a_trailing_event_without_final_blank_line() -> None:
    raw = ['data: {"a": 1}']
    events = await _collect(iter_sse_events(_lines(raw)))
    assert events == [(None, {"a": 1})]


# --- app.schemas.ai_stream --------------------------------------------------


def test_encode_sse_frames_event_and_json_data() -> None:
    framed = encode_sse(TextDeltaEvent(text="hello"))
    assert framed == 'event: text_delta\ndata: {"type": "text_delta", "text": "hello"}\n\n'


def test_encode_sse_action_confirmation_required_shape() -> None:
    from datetime import UTC, datetime

    event = ActionConfirmationRequiredEvent(
        action_id=uuid.uuid4(), action_type="execute_decision", summary="Proceed?", expires_at=datetime.now(UTC)
    )
    framed = encode_sse(event)
    assert framed.startswith("event: action_confirmation_required\ndata: ")
    assert '"action_type": "execute_decision"' in framed


# --- MockLLMProvider.generate_stream / NonStreamingMockProvider -----------


async def test_mock_provider_streams_multiple_text_chunks_that_concatenate_to_the_full_answer() -> None:
    provider = MockLLMProvider(chunk_words=2)
    non_streamed = await provider.generate(system="sys context", messages=[{"role": "user", "content": "hi"}], max_tokens=100)

    chunks = [
        c
        async for c in provider.generate_stream(system="sys context", messages=[{"role": "user", "content": "hi"}], max_tokens=100)
    ]
    assert len(chunks) > 1
    assert "".join(c.text_delta for c in chunks) == non_streamed.text
    assert chunks[-1].finished is True
    assert all(not c.finished for c in chunks[:-1])
    # call_count advanced exactly once per generate_stream() call, same as generate().
    assert provider.call_count == 2


async def test_mock_provider_streams_tool_calls_as_a_single_complete_chunk() -> None:
    provider = MockLLMProvider(
        responses=[LLMResponse(text="", model="mock-1", tool_calls=[ToolCall(id="c1", name="read_rack", arguments={"rack_id": "x"})])]
    )
    chunks = [c async for c in provider.generate_stream(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)]
    assert len(chunks) == 1
    assert chunks[0].tool_calls[0]["name"] == "read_rack"
    assert chunks[0].finished is True


async def test_mock_provider_is_deterministic_when_streamed_twice_with_the_same_input() -> None:
    provider = MockLLMProvider()
    messages = [{"role": "user", "content": "Why is Rack A1 at risk?"}]
    first = "".join([c.text_delta async for c in provider.generate_stream(system="sys", messages=messages, max_tokens=100)])
    provider2 = MockLLMProvider()
    second = "".join([c.text_delta async for c in provider2.generate_stream(system="sys", messages=messages, max_tokens=100)])
    assert first == second


def test_non_streaming_mock_provider_reports_no_streaming_support() -> None:
    provider = NonStreamingMockProvider()
    assert provider.supports_streaming is False
    assert not hasattr(provider, "generate_stream")


# --- (1)/(2) NeuroCoreService.answer_stream: plain text, multiple chunks --


async def test_answer_stream_streams_text_in_multiple_ordered_chunks() -> None:
    service = NeuroCoreService(provider=MockLLMProvider(chunk_words=2))
    context = _make_context([_make_rack()])

    events = await _collect(service.answer_stream(context, message="Summarize the cluster.", rack_id=None))

    text_deltas = [e for e in events if isinstance(e, TextDeltaEvent)]
    assert len(text_deltas) > 1
    full_text = "".join(e.text for e in text_deltas)
    assert "Summarize the cluster." in full_text
    # No action/tool events for a plain text-only turn with no db/simulation.
    assert not any(isinstance(e, (ToolStartedEvent, ToolCompletedEvent, ActionConfirmationRequiredEvent)) for e in events)


async def test_answer_stream_with_empty_message_streams_the_fallback_as_one_chunk() -> None:
    service = NeuroCoreService(provider=MockLLMProvider())
    context = _make_context([_make_rack()])

    events = await _collect(service.answer_stream(context, message="   ", rack_id=None))

    assert len(events) == 1
    assert isinstance(events[0], TextDeltaEvent)
    assert events[0].text == EMPTY_MESSAGE_RESPONSE


# --- (3)/(4) tool call streaming: started -> completed -----------------


async def test_answer_stream_read_tool_round_trip_streams_started_then_completed() -> None:
    rack = _make_rack(name="Rack A1", temperature=77.0)
    provider = MockLLMProvider(
        responses=[
            LLMResponse(text="", model="mock-1", tool_calls=[ToolCall(id="c1", name="read_rack", arguments={"rack_id": str(rack.id)})]),
            LLMResponse(text="Rack A1 is at 77.0C.", model="mock-1"),
        ]
    )
    service = NeuroCoreService(provider=provider, pending_actions=_StubPendingActions())
    context = _make_context([rack])

    events = await _collect(
        service.answer_stream(
            context, message="What's happening with Rack A1?", rack_id=None,
            db=object(), simulation=_FakeSimulationPort([rack]), conversation_id=uuid.uuid4(),
        )
    )

    started = [e for e in events if isinstance(e, ToolStartedEvent)]
    completed = [e for e in events if isinstance(e, ToolCompletedEvent)]
    assert [e.tool for e in started] == ["ReadRack"]
    assert [(e.tool, e.ok) for e in completed] == [("ReadRack", True)]
    # started strictly before completed, and both before the final answer's text.
    started_index = events.index(started[0])
    completed_index = events.index(completed[0])
    assert started_index < completed_index
    final_text = "".join(e.text for e in events if isinstance(e, TextDeltaEvent))
    assert final_text == "Rack A1 is at 77.0C."


async def test_answer_stream_unknown_tool_call_reports_tool_completed_not_ok() -> None:
    rack = _make_rack()
    provider = MockLLMProvider(
        responses=[
            LLMResponse(text="", model="mock-1", tool_calls=[ToolCall(id="c1", name="delete_everything", arguments={})]),
            LLMResponse(text="I can't do that.", model="mock-1"),
        ]
    )
    service = NeuroCoreService(provider=provider, pending_actions=_StubPendingActions())
    context = _make_context([rack])

    events = await _collect(
        service.answer_stream(
            context, message="Delete everything.", rack_id=None,
            db=object(), simulation=_FakeSimulationPort([rack]), conversation_id=uuid.uuid4(),
        )
    )

    completed = [e for e in events if isinstance(e, ToolCompletedEvent)]
    assert completed == [ToolCompletedEvent(tool="DeleteEverything", ok=False)]
    assert "".join(e.text for e in events if isinstance(e, TextDeltaEvent)) == "I can't do that."


# --- (5) action confirmation streaming ------------------------------------


async def test_answer_stream_write_tool_yields_action_confirmation_required_and_stops() -> None:
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

    events = await _collect(
        service.answer_stream(
            context, message="Move the workload off Rack A1.", rack_id=None,
            db=object(), simulation=_FakeSimulationPort([rack]), conversation_id=uuid.uuid4(),
        )
    )

    assert isinstance(events[-1], ActionConfirmationRequiredEvent)
    confirmation = events[-1]
    assert confirmation.action_type == "execute_decision"
    assert "proceed" in confirmation.summary.lower()
    assert confirmation.expires_at is not None
    # The loop stopped immediately — never streamed a second, unearned "success" turn.
    assert provider.call_count == 1
    assert not any(isinstance(e, TextDeltaEvent) and "never be reached" in e.text for e in events)


# --- (6) provider without streaming support --------------------------------


async def test_answer_stream_falls_back_to_one_chunk_for_a_non_streaming_provider() -> None:
    provider = NonStreamingMockProvider(response=LLMResponse(text="Whole-text fallback answer.", model="mock-nonstreaming-1"))
    service = NeuroCoreService(provider=provider)
    context = _make_context([_make_rack()])

    events = await _collect(service.answer_stream(context, message="Summarize.", rack_id=None))

    text_deltas = [e for e in events if isinstance(e, TextDeltaEvent)]
    assert len(text_deltas) == 1
    assert text_deltas[0].text == "Whole-text fallback answer."
    assert provider.call_count == 1


# --- (7) provider timeout ---------------------------------------------------


class _SlowStreamingProvider:
    name = "slow"
    model = "slow-1"
    supports_streaming = True

    async def generate(self, *, system, messages, max_tokens, tools=None):
        raise AssertionError("generate() should never be called when supports_streaming is True")

    async def generate_stream(self, *, system, messages, max_tokens, tools=None):
        await asyncio.sleep(2)
        yield LLMStreamChunk(text_delta="too late", finished=True)  # pragma: no cover - never reached


async def test_answer_stream_provider_timeout_yields_error_event() -> None:
    service = NeuroCoreService(provider=_SlowStreamingProvider(), llm_timeout_seconds=0.05, stream_timeout_seconds=5.0)
    context = _make_context([_make_rack()])

    events = await _collect(service.answer_stream(context, message="Summarize.", rack_id=None))

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].code == "provider_timeout"


# --- (8) tool timeout --------------------------------------------------


async def test_answer_stream_tool_timeout_yields_error_event() -> None:
    rack = _make_rack()
    provider = MockLLMProvider(
        responses=[LLMResponse(text="", model="mock-1", tool_calls=[ToolCall(id="c1", name="read_cluster_state", arguments={})])]
    )
    service = NeuroCoreService(provider=provider, pending_actions=_StubPendingActions(), tool_timeout_seconds=0.05, stream_timeout_seconds=5.0)
    context = _make_context([rack])

    async def _slow_execute_tool_call(call, tool_context):
        await asyncio.sleep(2)
        raise AssertionError("should have been cancelled by the tool timeout")  # pragma: no cover

    with patch("app.neurocore.service.execute_tool_call", new=_slow_execute_tool_call):
        events = await _collect(
            service.answer_stream(
                context, message="Check the cluster.", rack_id=None,
                db=object(), simulation=_FakeSimulationPort([rack]), conversation_id=uuid.uuid4(),
            )
        )

    completed = [e for e in events if isinstance(e, ToolCompletedEvent)]
    assert completed == [ToolCompletedEvent(tool="ReadClusterState", ok=False)]
    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].code == "tool_timeout"


# --- (9) client disconnect / (11) final message persistence --------------


async def test_chat_stream_persists_one_assistant_message_on_early_disconnect(db: AsyncSession, monkeypatch) -> None:
    conversation = Conversation()
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)

    context = _make_context([_make_rack()])
    monkeypatch.setattr("app.neurocore.service.load_context", AsyncMock(return_value=context))

    provider = MockLLMProvider(chunk_words=1)
    service = NeuroCoreService(provider=provider, pending_actions=_StubPendingActions())

    stream = service.chat_stream(
        db=db, simulation=object(), message="Summarize the cluster.", rack_id=None, conversation_id=conversation.id
    )
    first_event = await stream.__anext__()
    assert isinstance(first_event, (ThinkingEvent, TextDeltaEvent))
    # Simulate a client disconnecting mid-stream — Starlette calls .aclose()
    # on the SSE generator chain; nothing above should hang or raise.
    await stream.aclose()

    result = await db.execute(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation.id)
        .order_by(ConversationMessage.created_at)
    )
    messages = list(result.scalars().all())
    assert [m.role.value for m in messages] == ["user", "assistant"]


async def test_chat_stream_persists_exactly_one_assistant_message_and_emits_completed(db: AsyncSession, monkeypatch) -> None:
    conversation = Conversation()
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)

    context = _make_context([_make_rack()])
    monkeypatch.setattr("app.neurocore.service.load_context", AsyncMock(return_value=context))

    provider = MockLLMProvider(chunk_words=2)
    service = NeuroCoreService(provider=provider, pending_actions=_StubPendingActions())

    events = await _collect(
        service.chat_stream(db=db, simulation=object(), message="Summarize the cluster.", rack_id=None, conversation_id=conversation.id)
    )

    text_deltas = [e for e in events if isinstance(e, TextDeltaEvent)]
    assert len(text_deltas) > 1  # (2) multiple chunks
    assert isinstance(events[-1], CompletedEvent)
    completed = events[-1]
    assert completed.conversation_id == conversation.id

    result = await db.execute(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation.id)
        .order_by(ConversationMessage.created_at)
    )
    messages = list(result.scalars().all())
    assert len(messages) == 2  # exactly one user + one assistant row — never one per token
    assert messages[0].role.value == "user"
    assert messages[1].role.value == "assistant"
    assert messages[1].id == completed.message_id
    assert messages[1].content == "".join(e.text for e in text_deltas)


# --- (10) provider failure -------------------------------------------------


class _RaisingStreamProvider:
    name = "raising-stream"
    model = "raising-1"
    supports_streaming = True

    async def generate(self, *, system, messages, max_tokens, tools=None):
        raise AssertionError("not used")

    async def generate_stream(self, *, system, messages, max_tokens, tools=None):
        yield LLMStreamChunk(text_delta="partial answer, ")
        raise ProviderError("simulated failure mid-stream")


async def test_answer_stream_provider_failure_yields_error_event_after_any_partial_text() -> None:
    service = NeuroCoreService(provider=_RaisingStreamProvider())
    context = _make_context([_make_rack()])

    events = await _collect(service.answer_stream(context, message="Summarize.", rack_id=None))

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].code == "provider_error"
    # No API keys/internals leak into the operator-facing message.
    assert "simulated failure" not in events[-1].message


# --- (12) no hidden chain-of-thought leakage ------------------------------


async def test_answer_stream_thinking_events_are_always_from_the_fixed_operational_vocabulary() -> None:
    rack = _make_rack(name="Rack A1")
    provider = MockLLMProvider(
        responses=[
            LLMResponse(text="", model="mock-1", tool_calls=[ToolCall(id="c1", name="read_rack", arguments={"rack_id": str(rack.id)})]),
            LLMResponse(text="", model="mock-1", tool_calls=[ToolCall(id="c2", name="read_cluster_state", arguments={})]),
            LLMResponse(text="All clear.", model="mock-1"),
        ]
    )
    service = NeuroCoreService(provider=provider, pending_actions=_StubPendingActions())
    context = _make_context([rack])

    events = await _collect(
        service.answer_stream(
            context, message="What's going on?", rack_id=None,
            db=object(), simulation=_FakeSimulationPort([rack]), conversation_id=uuid.uuid4(),
        )
    )

    allowed = set(_TOOL_THINKING_MESSAGES.values()) | {"Analyzing cluster state...", "Generating explanation..."}
    thinking_messages = [e.message for e in events if isinstance(e, ThinkingEvent)]
    assert thinking_messages
    assert all(message in allowed for message in thinking_messages)
    # Never anything resembling actual model output (the mock's scripted
    # tool-call responses never populate .text, so there is nothing of the
    # model's own reasoning available to leak here in the first place).


# --- (13) malformed provider events -----------------------------------


class _MalformedStreamProvider:
    name = "malformed"
    model = "malformed-1"
    supports_streaming = True

    async def generate(self, *, system, messages, max_tokens, tools=None):
        raise AssertionError("not used")

    async def generate_stream(self, *, system, messages, max_tokens, tools=None):
        yield LLMStreamChunk(text_delta="ok so far")
        raise KeyError("some_unexpected_field")  # simulates a parsing bug, not a clean ProviderError


async def test_answer_stream_lets_a_non_provider_exception_propagate_for_the_caller_to_handle() -> None:
    """answer_stream only ever turns ProviderError/TimeoutError into a
    clean ErrorEvent itself — anything else (a genuinely unexpected/
    malformed event that broke an adapter's own parsing) is left to
    propagate, and it's chat_stream's/the API route's job (see
    tests/test_ai_stream_api.py) to be the actual last line of defense
    that never lets a raw exception reach the client.
    """
    service = NeuroCoreService(provider=_MalformedStreamProvider())
    context = _make_context([_make_rack()])

    with pytest.raises(KeyError):
        await _collect(service.answer_stream(context, message="Summarize.", rack_id=None))
