"""Top-level API router aggregation point."""

from fastapi import APIRouter

from app.api import cluster, events, health, racks

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(cluster.router)
api_router.include_router(racks.router)
api_router.include_router(events.router)
