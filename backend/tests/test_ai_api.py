"""HTTP-level tests for POST /api/ai/chat.

The `client` fixture (see conftest.py) builds the ASGI app without running
FastAPI's lifespan, so app.state.simulation/neurocore are never set — every
test here overrides get_db/get_simulation/get_neurocore with lightweight
fakes via app.dependency_overrides, the standard FastAPI testing pattern,
rather than standing up a real database. This exercises the route layer
(request validation, response shape, LookupError -> 404 mapping) without
needing Postgres — conversation persistence itself (NeuroCoreService.chat's
DB-touching half) is verified live, the same as every other DB-touching
service method in this backend (see this phase's commit for the podman
verification).
"""

import uuid

import pytest
from httpx import AsyncClient

from app.api.deps import get_db, get_neurocore, get_simulation
from app.main import app
from app.neurocore.service import ChatResult

pytestmark = pytest.mark.asyncio


class _FakeNeuroCoreService:
    """Stands in for NeuroCoreService at the route layer — no database, no
    provider, just a canned/predictable response so the test can assert on
    the HTTP contract (status code, response shape, error mapping).
    """

    def __init__(self, *, raise_lookup_error: bool = False) -> None:
        self.raise_lookup_error = raise_lookup_error
        self.received_calls: list[dict] = []

    async def chat(self, *, db, simulation, message, rack_id, conversation_id):
        self.received_calls.append(
            {"message": message, "rack_id": rack_id, "conversation_id": conversation_id}
        )
        if self.raise_lookup_error:
            raise LookupError(f"Conversation '{conversation_id}' not found.")
        return ChatResult(
            conversation_id=conversation_id or uuid.uuid4(),
            response="This is a fake grounded answer.",
            confidence=87.5,
            sources=["rack:Rack A1", "forecast:Rack A1"],
        )


async def _fake_get_db():
    yield None


def _override(fake_service: _FakeNeuroCoreService) -> None:
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_simulation] = lambda: object()
    app.dependency_overrides[get_neurocore] = lambda: fake_service


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


async def test_chat_happy_path_returns_grounded_response(client: AsyncClient) -> None:
    fake_service = _FakeNeuroCoreService()
    _override(fake_service)
    try:
        response = await client.post("/api/ai/chat", json={"message": "Why is Rack A1 at risk?"})
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "This is a fake grounded answer."
    assert body["confidence"] == 87.5
    assert body["sources"] == ["rack:Rack A1", "forecast:Rack A1"]
    assert uuid.UUID(body["conversation_id"])
    assert fake_service.received_calls[0]["message"] == "Why is Rack A1 at risk?"


async def test_chat_passes_rack_id_and_conversation_id_through(client: AsyncClient) -> None:
    fake_service = _FakeNeuroCoreService()
    _override(fake_service)
    rack_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    try:
        response = await client.post(
            "/api/ai/chat",
            json={"message": "What alternatives were considered?", "rack_id": str(rack_id), "conversation_id": str(conversation_id)},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200
    call = fake_service.received_calls[0]
    assert call["rack_id"] == rack_id
    assert call["conversation_id"] == conversation_id


async def test_chat_unknown_conversation_id_returns_404(client: AsyncClient) -> None:
    fake_service = _FakeNeuroCoreService(raise_lookup_error=True)
    _override(fake_service)
    try:
        response = await client.post(
            "/api/ai/chat", json={"message": "Continue our chat.", "conversation_id": str(uuid.uuid4())}
        )
    finally:
        _clear_overrides()

    assert response.status_code == 404


async def test_chat_empty_message_is_rejected_with_422(client: AsyncClient) -> None:
    _override(_FakeNeuroCoreService())
    try:
        response = await client.post("/api/ai/chat", json={"message": ""})
    finally:
        _clear_overrides()

    assert response.status_code == 422


async def test_chat_missing_message_is_rejected_with_422(client: AsyncClient) -> None:
    _override(_FakeNeuroCoreService())
    try:
        response = await client.post("/api/ai/chat", json={})
    finally:
        _clear_overrides()

    assert response.status_code == 422


async def test_chat_malformed_rack_id_is_rejected_with_422(client: AsyncClient) -> None:
    _override(_FakeNeuroCoreService())
    try:
        response = await client.post("/api/ai/chat", json={"message": "hello", "rack_id": "not-a-uuid"})
    finally:
        _clear_overrides()

    assert response.status_code == 422


async def test_chat_malformed_conversation_id_is_rejected_with_422(client: AsyncClient) -> None:
    _override(_FakeNeuroCoreService())
    try:
        response = await client.post("/api/ai/chat", json={"message": "hello", "conversation_id": "not-a-uuid"})
    finally:
        _clear_overrides()

    assert response.status_code == 422


async def test_chat_response_never_includes_raw_prompt_or_system_fields(client: AsyncClient) -> None:
    _override(_FakeNeuroCoreService())
    try:
        response = await client.post("/api/ai/chat", json={"message": "Explain the current thermal situation."})
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"conversation_id", "response", "confidence", "sources"}
