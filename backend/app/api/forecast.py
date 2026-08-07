"""Read-only forecast endpoints.

Thin by design — every route reads SimulationService's latest computed
forecast (recomputed every simulation tick by ForecastService). There is
no historical forecast archive to query, matching GET /api/cluster and
GET /api/racks (live state) rather than GET /api/events or
GET /api/decisions (durable history) — see
app.forecasting.service's module docstring for why.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_simulation
from app.schemas.forecast import ClusterForecastRead, ForecastPoint, RackForecastRead
from app.simulation.engine import SimulationService

router = APIRouter(prefix="/api", tags=["forecast"])


@router.get("/forecast", response_model=ClusterForecastRead)
async def get_cluster_forecast(simulation: SimulationService = Depends(get_simulation)) -> ClusterForecastRead:
    return ClusterForecastRead(
        predictions=[ForecastPoint.model_validate(p) for p in simulation.cluster_forecast]
    )


@router.get("/forecast/racks", response_model=list[RackForecastRead])
async def list_rack_forecasts(simulation: SimulationService = Depends(get_simulation)) -> list[RackForecastRead]:
    return [
        RackForecastRead(
            rack_id=rack.id,
            rack_name=rack.name,
            predictions=[ForecastPoint.model_validate(p) for p in simulation.rack_forecast(rack.id)],
        )
        for rack in simulation.rack_states
    ]


@router.get("/forecast/racks/{rack_id}", response_model=RackForecastRead)
async def get_rack_forecast(
    rack_id: uuid.UUID, simulation: SimulationService = Depends(get_simulation)
) -> RackForecastRead:
    rack = simulation.rack_state(rack_id)
    if rack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack not found")
    return RackForecastRead(
        rack_id=rack.id,
        rack_name=rack.name,
        predictions=[ForecastPoint.model_validate(p) for p in simulation.rack_forecast(rack.id)],
    )
