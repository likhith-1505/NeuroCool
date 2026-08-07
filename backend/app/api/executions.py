"""Read-only execution history endpoints.

Execution is triggered via the existing POST /api/decisions/{id}/execute
(see app.api.decisions), not here — these routes only expose the resulting
history, per the objective ("These are read-only").
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_simulation
from app.schemas.execution import ExecutionRead
from app.simulation.engine import SimulationService

router = APIRouter(prefix="/api", tags=["executions"])


@router.get("/executions", response_model=list[ExecutionRead])
async def list_executions(simulation: SimulationService = Depends(get_simulation)) -> list[ExecutionRead]:
    return [ExecutionRead.model_validate(e) for e in simulation.all_executions]


@router.get("/executions/{execution_id}", response_model=ExecutionRead)
async def get_execution(
    execution_id: uuid.UUID, simulation: SimulationService = Depends(get_simulation)
) -> ExecutionRead:
    execution = simulation.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    return ExecutionRead.model_validate(execution)
