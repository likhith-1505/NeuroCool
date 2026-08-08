"""Read-only optimization plan endpoints.

Thin by design — every route reads SimulationService's plan cache (backed
by OptimizationService, see app.optimization.service), never touching a
planning engine directly. Plans are triggered internally every simulation
tick (see app.simulation.engine._tick), not from these routes — mirrors
GET /api/executions (also read-only; the write path lives elsewhere).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_simulation
from app.schemas.optimization import OptimizationPlanRead
from app.simulation.engine import SimulationService

router = APIRouter(prefix="/api", tags=["plans"])


@router.get("/plans", response_model=list[OptimizationPlanRead])
async def list_plans(simulation: SimulationService = Depends(get_simulation)) -> list[OptimizationPlanRead]:
    return [OptimizationPlanRead.from_row(plan) for plan in simulation.all_plans]


@router.get("/plans/latest", response_model=OptimizationPlanRead)
async def get_latest_plan(simulation: SimulationService = Depends(get_simulation)) -> OptimizationPlanRead:
    plan = simulation.latest_plan
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No optimization plan has run yet")
    return OptimizationPlanRead.from_row(plan)


@router.get("/plans/{plan_id}", response_model=OptimizationPlanRead)
async def get_plan(plan_id: uuid.UUID, simulation: SimulationService = Depends(get_simulation)) -> OptimizationPlanRead:
    plan = simulation.get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return OptimizationPlanRead.from_row(plan)
