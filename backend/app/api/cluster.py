"""Read-only cluster telemetry endpoint."""

from fastapi import APIRouter, Depends

from app.api.deps import get_simulation
from app.schemas.cluster import ClusterTelemetry
from app.simulation.engine import SimulationService

router = APIRouter(prefix="/api", tags=["cluster"])


@router.get("/cluster", response_model=ClusterTelemetry)
async def get_cluster(simulation: SimulationService = Depends(get_simulation)) -> ClusterTelemetry:
    return ClusterTelemetry.model_validate(simulation.cluster_state)
