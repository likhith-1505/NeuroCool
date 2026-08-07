"""WebSocket endpoint streaming live telemetry to connected clients.

Push-only: the server never expects messages from the client. `receive_text`
is still awaited in a loop purely so a client disconnect is detected
promptly (Starlette raises WebSocketDisconnect from it), not because the
server reads anything meaningful from it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.schemas.telemetry import TelemetrySnapshot
from app.simulation.engine import SimulationService
from app.websocket.manager import manager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/telemetry")
async def telemetry_stream(websocket: WebSocket) -> None:
    await manager.connect(websocket)

    simulation: SimulationService = websocket.app.state.simulation
    try:
        # Send an immediate snapshot on connect so the client doesn't have
        # to wait up to a full tick interval to see anything.
        await websocket.send_json(TelemetrySnapshot.from_simulation(simulation).model_dump(mode="json"))

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket telemetry stream error")
    finally:
        await manager.disconnect(websocket)
