"""Simulation lifecycle control — start/pause/resume/reset the tick loop
itself. Distinct from app.api.scenarios, which controls *what* a running
simulation is doing (which scenario is active), not *whether* it's running
at all — see app.simulation.state.SimulationStatus.

Thin by design: every route just calls into SimulationService, the sole
owner of this state (see app.simulation.engine.SimulationService). Every
operation is idempotent by construction there — this layer adds no locking
or validation of its own.
"""

from fastapi import APIRouter, Depends

from app.api.deps import get_simulation
from app.schemas.simulation import SimulationStatusRead
from app.simulation.engine import SimulationService

router = APIRouter(prefix="/api", tags=["simulation"])


@router.get("/simulation", response_model=SimulationStatusRead)
async def get_simulation_status(simulation: SimulationService = Depends(get_simulation)) -> SimulationStatusRead:
    return simulation.status


@router.post("/simulation/start", response_model=SimulationStatusRead)
async def start_simulation(simulation: SimulationService = Depends(get_simulation)) -> SimulationStatusRead:
    return await simulation.start()


@router.post("/simulation/pause", response_model=SimulationStatusRead)
async def pause_simulation(simulation: SimulationService = Depends(get_simulation)) -> SimulationStatusRead:
    return await simulation.pause()


@router.post("/simulation/resume", response_model=SimulationStatusRead)
async def resume_simulation(simulation: SimulationService = Depends(get_simulation)) -> SimulationStatusRead:
    return await simulation.resume()


@router.post("/simulation/reset", response_model=SimulationStatusRead)
async def reset_simulation(simulation: SimulationService = Depends(get_simulation)) -> SimulationStatusRead:
    return await simulation.reset()
