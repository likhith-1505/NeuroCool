"""NeuroCore endpoints — chat, and the pending-action confirmation flow
for write tool calls (see app.neurocore.actions.PendingActionService).

Thin by design — every route only maps HTTP concerns (request/response
shape, LookupError -> 404, ActionStateConflict -> 409) onto NeuroCoreService;
all reasoning, grounding, tool dispatch, and persistence logic lives in
app.neurocore. Never exposes internal service objects directly — every
response is a Pydantic schema.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_neurocore, get_simulation
from app.config import settings
from app.models.enums import PendingActionStatus
from app.neurocore.actions import ActionStateConflict
from app.neurocore.providers.factory import provider_status
from app.neurocore.service import NeuroCoreService
from app.schemas.ai import ChatRequest, ChatResponse, ProviderStatusResponse
from app.schemas.ai_stream import ErrorEvent, encode_sse
from app.schemas.pending_action import PendingActionRead
from app.simulation.engine import SimulationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/providers", response_model=ProviderStatusResponse)
async def list_providers() -> ProviderStatusResponse:
    """Which LLMProvider backends are configured/available — never which
    one is currently active or any secret (see
    app.neurocore.providers.factory.provider_status). Reads directly from
    settings; no network call to any vendor.
    """
    return ProviderStatusResponse(providers=provider_status(settings))


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    simulation: SimulationService = Depends(get_simulation),
    neurocore: NeuroCoreService = Depends(get_neurocore),
) -> ChatResponse:
    try:
        result = await neurocore.chat(
            db=db,
            simulation=simulation,
            message=request.message,
            rack_id=request.rack_id,
            conversation_id=request.conversation_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return ChatResponse(
        conversation_id=result.conversation_id,
        response=result.response,
        confidence=result.confidence,
        sources=result.sources,
        pending_action=PendingActionRead.model_validate(result.pending_action) if result.pending_action else None,
    )


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    simulation: SimulationService = Depends(get_simulation),
    neurocore: NeuroCoreService = Depends(get_neurocore),
) -> StreamingResponse:
    """Server-Sent Events for one AI conversation turn — see
    app.schemas.ai_stream for the seven typed event shapes and
    app.neurocore.service.NeuroCoreService.chat_stream for the actual
    orchestration. This is additive to POST /chat, not a replacement —
    that endpoint is untouched and keeps working exactly as before.

    Unlike every other route in this module, a request-level failure here
    (e.g. an unknown conversation_id) is never an HTTP error status: the
    response has already committed to 200 with an SSE content-type by the
    time any failure could be detected, so every failure — "not found" or
    otherwise — is instead the last event sent on the stream (see
    app.schemas.ai_stream.ErrorEvent).

    Does not use the existing WebSocket infrastructure (see
    app.websocket.manager) — that remains dedicated to live cluster
    telemetry and system/AI-action events; this is SSE, scoped to a single
    request/response, one conversation turn at a time.
    """

    async def event_source():
        stream = neurocore.chat_stream(
            db=db, simulation=simulation, message=request.message,
            rack_id=request.rack_id, conversation_id=request.conversation_id,
        )
        try:
            async for event in stream:
                yield encode_sse(event)
        except LookupError as exc:
            yield encode_sse(ErrorEvent(code="not_found", message=str(exc)))
        except Exception:
            # Last line of defense — NeuroCoreService.chat_stream already
            # catches everything it can and turns it into a clean
            # ErrorEvent; this only fires for something that escaped that,
            # and never leaks the raw exception (see the objective's
            # error-streaming requirements).
            logger.exception("Unhandled error while streaming a NeuroCore chat response")
            yield encode_sse(ErrorEvent(code="internal_error", message="An unexpected error occurred."))
        finally:
            # Deterministic cleanup regardless of how we got here (normal
            # completion, an error above, or the client disconnecting —
            # which closes *this* generator via GeneratorExit, and this
            # finally still runs): explicitly closing the inner generator
            # cascades .aclose() down through chat_stream -> answer_stream
            # -> the provider's own stream, rather than waiting on garbage
            # collection to eventually do it. See the objective's
            # cancellation requirements.
            await stream.aclose()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disables response buffering on nginx-style reverse proxies,
            # which would otherwise defeat the whole point of streaming.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/actions/{action_id}/confirm", response_model=PendingActionRead)
async def confirm_action(
    action_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    simulation: SimulationService = Depends(get_simulation),
    neurocore: NeuroCoreService = Depends(get_neurocore),
) -> PendingActionRead:
    """Always returns 200 with the action's outcome — including a FAILED
    status when re-validation or execution didn't succeed (that is data
    about the outcome, not a transport error; see
    PendingActionService.confirm). Only a genuinely missing/already-
    resolved action is a 404/409.
    """
    try:
        action = await neurocore.confirm_action(db=db, simulation=simulation, action_id=action_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ActionStateConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return PendingActionRead.model_validate(action)


@router.post("/actions/{action_id}/cancel", response_model=PendingActionRead)
async def cancel_action(
    action_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    neurocore: NeuroCoreService = Depends(get_neurocore),
) -> PendingActionRead:
    try:
        action = await neurocore.cancel_action(db=db, action_id=action_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ActionStateConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return PendingActionRead.model_validate(action)


@router.get("/actions/{action_id}", response_model=PendingActionRead)
async def get_action(
    action_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    neurocore: NeuroCoreService = Depends(get_neurocore),
) -> PendingActionRead:
    action = await neurocore.get_action(db=db, action_id=action_id)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pending action '{action_id}' not found.")
    return PendingActionRead.model_validate(action)


@router.get("/actions", response_model=list[PendingActionRead])
async def list_actions(
    conversation_id: uuid.UUID | None = None,
    status_filter: PendingActionStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    neurocore: NeuroCoreService = Depends(get_neurocore),
) -> list[PendingActionRead]:
    actions = await neurocore.list_actions(db=db, conversation_id=conversation_id, status=status_filter, limit=limit)
    return [PendingActionRead.model_validate(action) for action in actions]
