"""NeuroCore chat endpoint — the read-only AI reasoning/explanation layer.

Thin by design — the route only maps HTTP concerns (request/response
shape, LookupError -> 404) onto NeuroCoreService.chat; all reasoning,
grounding, and persistence logic lives in app.neurocore.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_neurocore, get_simulation
from app.neurocore.service import NeuroCoreService
from app.schemas.ai import ChatRequest, ChatResponse
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
    )
