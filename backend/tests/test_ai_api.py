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
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.api.deps import get_db, get_neurocore, get_simulation
from app.main import app
from app.models.enums import PendingActionStatus, PendingActionType
from app.models.pending_action import PendingAction
from app.neurocore.actions import ActionStateConflict
from app.neurocore.service import ChatResult

pytestmark = pytest.mark.asyncio


def _make_pending_action(**overrides: object) -> PendingAction:
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(), conversation_id=uuid.uuid4(), plan_id=None, decision_id=uuid.uuid4(),
        action_type=PendingActionType.EXECUTE_DECISION, target="Rack A1", status=PendingActionStatus.PENDING,
        summary="I can execute the recommended migration for Rack A1. Proceed?", error_message=None,
        execution_id=None, created_at=datetime.now(UTC), expires_at=datetime.now(UTC) + timedelta(minutes=5),
        confirmed_at=None, completed_at=None,
    )
    defaults.update(overrides)
    return PendingAction(**defaults)  # type: ignore[arg-type]


class _FakeNeuroCoreService:
    """Stands in for NeuroCoreService at the route layer — no database, no
    provider, just a canned/predictable response so the test can assert on
    the HTTP contract (status code, response shape, error mapping).
    """

    def __init__(
        self, *, raise_lookup_error: bool = False, pending_action: PendingAction | None = None,
        confirm_raises: Exception | None = None, cancel_raises: Exception | None = None,
        action_for_get: PendingAction | None = None, actions_for_list: list[PendingAction] | None = None,
    ) -> None:
        self.raise_lookup_error = raise_lookup_error
        self.received_calls: list[dict] = []
        self._pending_action = pending_action
        self._confirm_raises = confirm_raises
        self._cancel_raises = cancel_raises
        self._action_for_get = action_for_get
        self._actions_for_list = actions_for_list or []
        self.confirm_calls: list[uuid.UUID] = []
        self.cancel_calls: list[uuid.UUID] = []

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
            pending_action=self._pending_action,
        )

    async def confirm_action(self, *, db, simulation, action_id):
        self.confirm_calls.append(action_id)
        if self._confirm_raises is not None:
            raise self._confirm_raises
        return _make_pending_action(id=action_id, status=PendingActionStatus.COMPLETED)

    async def cancel_action(self, *, db, action_id):
        self.cancel_calls.append(action_id)
        if self._cancel_raises is not None:
            raise self._cancel_raises
        return _make_pending_action(id=action_id, status=PendingActionStatus.CANCELLED)

    async def get_action(self, *, db, action_id):
        return self._action_for_get

    async def list_actions(self, *, db, conversation_id=None, status=None, limit=50):
        return self._actions_for_list


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
    assert set(body.keys()) == {"conversation_id", "response", "confidence", "sources", "pending_action"}


async def test_chat_surfaces_a_pending_action_when_proposed(client: AsyncClient) -> None:
    pending = _make_pending_action()
    fake_service = _FakeNeuroCoreService(pending_action=pending)
    _override(fake_service)
    try:
        response = await client.post("/api/ai/chat", json={"message": "Move the workload off Rack A1."})
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["pending_action"]["id"] == str(pending.id)
    assert body["pending_action"]["status"] == "pending"
    assert "proceed" in body["pending_action"]["summary"].lower()


# --- confirm/cancel/get/list actions ----------------------------------


async def test_confirm_action_returns_the_outcome(client: AsyncClient) -> None:
    fake_service = _FakeNeuroCoreService()
    _override(fake_service)
    action_id = uuid.uuid4()
    try:
        response = await client.post(f"/api/ai/actions/{action_id}/confirm")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert fake_service.confirm_calls == [action_id]


async def test_confirm_action_unknown_id_returns_404(client: AsyncClient) -> None:
    action_id = uuid.uuid4()
    fake_service = _FakeNeuroCoreService(confirm_raises=LookupError(f"Pending action '{action_id}' not found."))
    _override(fake_service)
    try:
        response = await client.post(f"/api/ai/actions/{action_id}/confirm")
    finally:
        _clear_overrides()

    assert response.status_code == 404


async def test_confirm_action_conflict_returns_409(client: AsyncClient) -> None:
    fake_service = _FakeNeuroCoreService(confirm_raises=ActionStateConflict("This action is already cancelled and cannot be confirmed."))
    _override(fake_service)
    try:
        response = await client.post(f"/api/ai/actions/{uuid.uuid4()}/confirm")
    finally:
        _clear_overrides()

    assert response.status_code == 409


async def test_cancel_action_returns_the_outcome(client: AsyncClient) -> None:
    fake_service = _FakeNeuroCoreService()
    _override(fake_service)
    action_id = uuid.uuid4()
    try:
        response = await client.post(f"/api/ai/actions/{action_id}/cancel")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert fake_service.cancel_calls == [action_id]


async def test_cancel_action_conflict_returns_409(client: AsyncClient) -> None:
    fake_service = _FakeNeuroCoreService(cancel_raises=ActionStateConflict("This action is already completed and cannot be cancelled."))
    _override(fake_service)
    try:
        response = await client.post(f"/api/ai/actions/{uuid.uuid4()}/cancel")
    finally:
        _clear_overrides()

    assert response.status_code == 409


async def test_get_action_returns_the_action(client: AsyncClient) -> None:
    pending = _make_pending_action()
    fake_service = _FakeNeuroCoreService(action_for_get=pending)
    _override(fake_service)
    try:
        response = await client.get(f"/api/ai/actions/{pending.id}")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json()["id"] == str(pending.id)


async def test_get_action_unknown_id_returns_404(client: AsyncClient) -> None:
    fake_service = _FakeNeuroCoreService(action_for_get=None)
    _override(fake_service)
    try:
        response = await client.get(f"/api/ai/actions/{uuid.uuid4()}")
    finally:
        _clear_overrides()

    assert response.status_code == 404


async def test_list_actions_returns_every_action(client: AsyncClient) -> None:
    actions = [_make_pending_action(), _make_pending_action(status=PendingActionStatus.COMPLETED)]
    fake_service = _FakeNeuroCoreService(actions_for_list=actions)
    _override(fake_service)
    try:
        response = await client.get("/api/ai/actions")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert {a["id"] for a in body} == {str(a.id) for a in actions}


async def test_list_actions_accepts_status_filter(client: AsyncClient) -> None:
    fake_service = _FakeNeuroCoreService(actions_for_list=[])
    _override(fake_service)
    try:
        response = await client.get("/api/ai/actions", params={"status": "pending"})
    finally:
        _clear_overrides()

    assert response.status_code == 200
