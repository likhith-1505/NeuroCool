"""Decision listing and lifecycle-control endpoints.

Thin by design — every route calls into SimulationService (which owns the
DecisionService) and maps LookupError/ValueError to 404/400. All actual
reasoning and lifecycle logic lives in app.ai.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_simulation
from app.models.decision import Decision
from app.schemas.decision import DecisionRead
from app.simulation.engine import SimulationService

router = APIRouter(prefix="/api", tags=["decisions"])


@router.get("/decisions", response_model=list[DecisionRead])
async def list_decisions(simulation: SimulationService = Depends(get_simulation)) -> list[DecisionRead]:
    return [DecisionRead.model_validate(d) for d in simulation.all_decisions]


@router.get("/decisions/{decision_id}", response_model=DecisionRead)
async def get_decision(
    decision_id: uuid.UUID, simulation: SimulationService = Depends(get_simulation)
) -> DecisionRead:
    decision = simulation.get_decision(decision_id)
    if decision is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision not found")
    return DecisionRead.model_validate(decision)


@router.post("/decisions/{decision_id}/accept", response_model=DecisionRead)
async def accept_decision(
    decision_id: uuid.UUID, simulation: SimulationService = Depends(get_simulation)
) -> DecisionRead:
    decision = await _apply_transition(simulation.accept_decision, decision_id)
    return DecisionRead.model_validate(decision)


@router.post("/decisions/{decision_id}/reject", response_model=DecisionRead)
async def reject_decision(
    decision_id: uuid.UUID, simulation: SimulationService = Depends(get_simulation)
) -> DecisionRead:
    decision = await _apply_transition(simulation.reject_decision, decision_id)
    return DecisionRead.model_validate(decision)


@router.post("/decisions/{decision_id}/execute", response_model=DecisionRead)
async def execute_decision(
    decision_id: uuid.UUID, simulation: SimulationService = Depends(get_simulation)
) -> DecisionRead:
    decision = await _apply_transition(simulation.execute_decision, decision_id)
    return DecisionRead.model_validate(decision)


async def _apply_transition(transition, decision_id: uuid.UUID) -> Decision:
    try:
        return await transition(decision_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
