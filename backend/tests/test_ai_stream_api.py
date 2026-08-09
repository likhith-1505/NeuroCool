"""HTTP-level tests for POST /api/ai/chat/stream — SSE framing over the
wire, the "every stream failure is an `error` event, never an HTTP status"
contract, and regression checks that this phase didn't disturb the
existing (unchanged) POST /api/ai/chat route or the existing WebSocket
telemetry infrastructure.

Same testing approach as tests/test_ai_api.py: the `client` fixture builds
the ASGI app without running FastAPI's lifespan, so every test overrides
get_db/get_simulation/get_neurocore with a lightweight fake via
app.dependency_overrides rather than standing up a real database — no LLM
API key, no Postgres, required anywhere in this file.
"""

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.api.deps import get_db, get_neurocore, get_simulation
from app.main import app
from app.schemas.ai_stream import (
    ActionConfirmationRequiredEvent,
    CompletedEvent,
    TextDeltaEvent,
    ThinkingEvent,
)

# No blanket `pytestmark = pytest.mark.asyncio` here — this file mixes
# sync (route-wiring regression checks) and async (HTTP) tests; see
# tests/test_neurocore_streaming.py's identical note.


class _FakeStreamingNeuroCoreService:
    """Stands in for NeuroCoreService at the route layer — see
    tests/test_ai_api.py's _FakeNeuroCoreService for the same pattern
    applied to the non-streaming route.
    """

    def __init__(
        self, *, events: list | None = None, raise_lookup_error: bool = False, raise_generic_error: bool = False
    ) -> None:
        self._events = events or []
        self._raise_lookup_error = raise_lookup_error
        self._raise_generic_error = raise_generic_error
        self.received_calls: list[dict] = []

    async def chat_stream(self, *, db, simulation, message, rack_id, conversation_id):
        self.received_calls.append({"message": message, "rack_id": rack_id, "conversation_id": conversation_id})
        if self._raise_lookup_error:
            raise LookupError(f"Conversation '{conversation_id}' not found.")
        for event in self._events:
            yield event
        if self._raise_generic_error:
            # Simulates a bug leaking a raw exception out of the service
            # layer — the route must still never expose this text to the
            # client (see the objective's error-streaming requirements).
            raise RuntimeError("super-secret-internal-detail sk-should-never-leak")

    # Only used by the coexistence test below.
    async def chat(self, *, db, simulation, message, rack_id, conversation_id):
        from app.neurocore.service import ChatResult

        return ChatResult(conversation_id=conversation_id or uuid.uuid4(), response="non-streaming reply", confidence=80.0, sources=[])


async def _fake_get_db():
    yield None


def _override(fake_service: _FakeStreamingNeuroCoreService) -> None:
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_simulation] = lambda: object()
    app.dependency_overrides[get_neurocore] = lambda: fake_service


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def _parsed_events(body: str) -> list[tuple[str, dict]]:
    """Splits raw SSE text into (event_type, json_payload) pairs — a tiny,
    deliberately independent re-parse of the wire format (not a reuse of
    encode_sse/iter_sse_events) so this test suite is actually checking
    what went over the wire, not just that our own encoder round-trips.
    """
    import json

    events: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        event_type = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:") :].strip())
        assert event_type is not None and data is not None
        events.append((event_type, data))
    return events


async def test_chat_stream_returns_sse_content_type_and_framed_events(client: AsyncClient) -> None:
    events = [ThinkingEvent(message="Analyzing cluster state..."), TextDeltaEvent(text="Hello "), TextDeltaEvent(text="operator.")]
    fake_service = _FakeStreamingNeuroCoreService(events=events)
    _override(fake_service)
    try:
        response = await client.post("/api/ai/chat/stream", json={"message": "Summarize the cluster."})
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    parsed = _parsed_events(response.text)
    assert parsed[0] == ("thinking", {"type": "thinking", "message": "Analyzing cluster state..."})
    assert parsed[1] == ("text_delta", {"type": "text_delta", "text": "Hello "})
    assert parsed[2] == ("text_delta", {"type": "text_delta", "text": "operator."})
    assert fake_service.received_calls[0]["message"] == "Summarize the cluster."


# --- (5) action confirmation event, over the wire --------------------------


async def test_chat_stream_action_confirmation_required_event_shape(client: AsyncClient) -> None:
    action_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    events = [
        ActionConfirmationRequiredEvent(
            action_id=action_id, action_type="execute_decision", summary="I can execute the recommended action. Proceed?",
            expires_at=expires_at,
        )
    ]
    _override(_FakeStreamingNeuroCoreService(events=events))
    try:
        response = await client.post("/api/ai/chat/stream", json={"message": "Move the workload off Rack A1."})
    finally:
        _clear_overrides()

    parsed = _parsed_events(response.text)
    assert parsed[0][0] == "action_confirmation_required"
    payload = parsed[0][1]
    assert payload["action_id"] == str(action_id)
    assert payload["action_type"] == "execute_decision"
    assert "proceed" in payload["summary"].lower()
    assert "expires_at" in payload
    # This event only ever *describes* the PendingAction created elsewhere
    # — nothing about actually confirming it is reachable through this
    # route; that remains exclusively POST /api/ai/actions/{id}/confirm.


async def test_chat_stream_completed_event_carries_conversation_and_message_ids(client: AsyncClient) -> None:
    conversation_id = uuid.uuid4()
    message_id = uuid.uuid4()
    events = [TextDeltaEvent(text="Done."), CompletedEvent(conversation_id=conversation_id, message_id=message_id)]
    _override(_FakeStreamingNeuroCoreService(events=events))
    try:
        response = await client.post("/api/ai/chat/stream", json={"message": "Summarize."})
    finally:
        _clear_overrides()

    parsed = _parsed_events(response.text)
    completed_payload = parsed[-1][1]
    assert parsed[-1][0] == "completed"
    assert completed_payload["conversation_id"] == str(conversation_id)
    assert completed_payload["message_id"] == str(message_id)


# --- request-level failures become `error` events, never HTTP statuses ---


async def test_chat_stream_unknown_conversation_id_is_an_error_event_not_a_404(client: AsyncClient) -> None:
    fake_service = _FakeStreamingNeuroCoreService(raise_lookup_error=True)
    _override(fake_service)
    try:
        response = await client.post(
            "/api/ai/chat/stream", json={"message": "Continue.", "conversation_id": str(uuid.uuid4())}
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200  # already committed to SSE — see the route's docstring
    parsed = _parsed_events(response.text)
    assert parsed[-1][0] == "error"
    assert parsed[-1][1]["code"] == "not_found"


# --- (13) a raw exception escaping the service layer never reaches the client


async def test_chat_stream_unexpected_exception_is_a_generic_error_event_with_no_leaked_detail(client: AsyncClient) -> None:
    fake_service = _FakeStreamingNeuroCoreService(
        events=[TextDeltaEvent(text="partial")], raise_generic_error=True
    )
    _override(fake_service)
    try:
        response = await client.post("/api/ai/chat/stream", json={"message": "Summarize."})
    finally:
        _clear_overrides()

    assert response.status_code == 200
    parsed = _parsed_events(response.text)
    assert parsed[0] == ("text_delta", {"type": "text_delta", "text": "partial"})
    assert parsed[-1][0] == "error"
    assert parsed[-1][1]["code"] == "internal_error"
    assert "sk-should-never-leak" not in response.text
    assert "super-secret-internal-detail" not in response.text
    assert "RuntimeError" not in response.text


async def test_chat_stream_request_validation_still_returns_422_before_any_streaming_starts(client: AsyncClient) -> None:
    _override(_FakeStreamingNeuroCoreService())
    try:
        response = await client.post("/api/ai/chat/stream", json={})
    finally:
        _clear_overrides()

    assert response.status_code == 422


# --- (14) the existing non-streaming REST endpoint still works -----------


async def test_both_chat_and_chat_stream_endpoints_work_side_by_side(client: AsyncClient) -> None:
    fake_service = _FakeStreamingNeuroCoreService(events=[TextDeltaEvent(text="streamed reply")])
    _override(fake_service)
    try:
        rest_response = await client.post("/api/ai/chat", json={"message": "Hello"})
        stream_response = await client.post("/api/ai/chat/stream", json={"message": "Hello"})
    finally:
        _clear_overrides()

    assert rest_response.status_code == 200
    assert rest_response.json()["response"] == "non-streaming reply"
    assert stream_response.status_code == 200
    assert stream_response.headers["content-type"].startswith("text/event-stream")
    assert "streamed reply" in stream_response.text


# --- (15) existing WebSocket telemetry infrastructure is untouched -------


def test_websocket_telemetry_route_is_still_registered_on_the_single_websocket_router() -> None:
    """This phase adds SSE for AI chat but must never touch the existing
    WebSocket infrastructure (see app.websocket) — a full end-to-end
    telemetry-stream check (connect, receive a snapshot) is covered by
    this phase's live podman verification, same as every other DB/
    simulation-driven behavior in this backend; this is the fast,
    dependency-free regression guard that the route wiring itself is
    intact and unchanged.
    """
    from starlette.routing import WebSocketRoute

    websocket_routes = [route for route in _flatten_routes(app.routes) if getattr(route, "path", None) == "/ws/telemetry"]
    assert len(websocket_routes) == 1
    assert isinstance(websocket_routes[0], WebSocketRoute)


def _flatten_routes(routes) -> list:
    """Recursively resolves every leaf route, regardless of how deeply
    app.include_router(...) nested/wrapped it internally (FastAPI's own
    representation of an included router is an implementation detail that
    has changed across versions — see e.g. `_IncludedRouter`'s
    `original_router` below) — this test only cares about what's actually
    reachable, not the wrapper shape of the moment.
    """
    flattened: list = []
    for route in routes:
        nested = getattr(getattr(route, "original_router", None), "routes", None) or getattr(route, "routes", None)
        if nested is not None:
            flattened.extend(_flatten_routes(nested))
        else:
            flattened.append(route)
    return flattened


def test_only_one_websocket_connection_manager_exists_for_the_whole_app() -> None:
    """Guards against ever accidentally introducing a second WebSocket
    system for AI streaming (the objective explicitly forbids this) — SSE
    is what POST /api/ai/chat/stream uses instead; app.websocket.manager.
    manager remains the single registry both telemetry and AI action
    events (see app.neurocore.actions.PendingActionService._broadcast)
    share.
    """
    from app.neurocore.actions import manager as actions_manager
    from app.websocket.manager import manager as the_manager
    from app.websocket.telemetry import manager as telemetry_manager

    assert actions_manager is the_manager
    assert telemetry_manager is the_manager
