"""Read-only rack telemetry endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_simulation
from app.schemas.rack import RackTelemetry
from app.simulation.engine import SimulationService

router = APIRouter(prefix="/api", tags=["racks"])


@router.get("/racks", response_model=list[RackTelemetry])
async def list_racks(simulation: SimulationService = Depends(get_simulation)) -> list[RackTelemetry]:
    return [RackTelemetry.model_validate(rack) for rack in simulation.rack_states]


@router.get("/racks/{rack_id}", response_model=RackTelemetry)
async def get_rack(rack_id: uuid.UUID, simulation: SimulationService = Depends(get_simulation)) -> RackTelemetry:
    rack = simulation.rack_state(rack_id)
    if rack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack not found")
    return RackTelemetry.model_validate(rack)
