"""Scenario listing, status, and control endpoints.

Thin by design: every route just calls into SimulationService (which owns
the ScenarioManager) and maps a ValueError (unknown/unavailable scenario)
to a 400. All the actual decision-making lives in
app.simulation.scenario_manager.ScenarioManager.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_simulation
from app.schemas.scenario import ScenarioActivateRequest, ScenarioDefinitionRead, ScenarioStatus
from app.simulation.engine import SimulationService

router = APIRouter(prefix="/api", tags=["scenarios"])


@router.get("/scenarios", response_model=list[ScenarioDefinitionRead])
async def list_scenarios(
    simulation: SimulationService = Depends(get_simulation),
) -> list[ScenarioDefinitionRead]:
    return [ScenarioDefinitionRead.model_validate(d) for d in simulation.available_scenarios()]


@router.get("/scenario", response_model=ScenarioStatus)
async def get_active_scenario(simulation: SimulationService = Depends(get_simulation)) -> ScenarioStatus:
    return simulation.scenario_status


@router.post("/scenario", response_model=ScenarioStatus)
async def activate_scenario(
    body: ScenarioActivateRequest,
    simulation: SimulationService = Depends(get_simulation),
) -> ScenarioStatus:
    try:
        return await simulation.activate_scenario(body.scenario)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/scenario/reset", response_model=ScenarioStatus)
async def reset_scenario(simulation: SimulationService = Depends(get_simulation)) -> ScenarioStatus:
    return await simulation.reset_scenario()


@router.post("/scenario/replay", response_model=ScenarioStatus)
async def replay_scenario(simulation: SimulationService = Depends(get_simulation)) -> ScenarioStatus:
    try:
        return await simulation.replay_scenario()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
