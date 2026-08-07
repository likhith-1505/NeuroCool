"""WebSocket router aggregation point (mirrors app.api.router)."""

from fastapi import APIRouter

from app.websocket import telemetry

websocket_router = APIRouter()
websocket_router.include_router(telemetry.router)
