"""API representation of the simulation lifecycle — see
app.simulation.state.SimulationStatus and
app.simulation.engine.SimulationService.start/pause/resume/reset. Also
embedded as TelemetrySnapshot.simulation (see app.schemas.telemetry) so
every WebSocket frame — not just GET /api/simulation — carries it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.simulation.state import SimulationStatus


class SimulationStatusRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: SimulationStatus
    tick: int
    started_at: datetime | None
    paused_at: datetime | None
