"""NeuroCore endpoints — chat, and the pending-action confirmation flow
for write tool calls (see app.neurocore.actions.PendingActionService).

Thin by design — every route only maps HTTP concerns (request/response
shape, LookupError -> 404, ActionStateConflict -> 409) onto NeuroCoreService;
all reasoning, grounding, tool dispatch, and persistence logic lives in
app.neurocore. Never exposes internal service objects directly — every
response is a Pydantic schema.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_neurocore, get_simulation
from app.models.enums import PendingActionStatus
from app.neurocore.actions import ActionStateConflict
from app.neurocore.service import NeuroCoreService
from app.schemas.ai import ChatRequest, ChatResponse
from app.schemas.pending_action import PendingActionRead
from app.simulation.engine import SimulationService

router = APIRouter(prefix="/api/ai", tags=["ai"])


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
