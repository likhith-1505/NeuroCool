"""Top-level API router aggregation point."""

from fastapi import APIRouter

from app.api import ai, cluster, decisions, events, executions, forecast, health, plans, racks, scenarios

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(cluster.router)
api_router.include_router(racks.router)
api_router.include_router(events.router)
api_router.include_router(scenarios.router)
api_router.include_router(decisions.router)
api_router.include_router(executions.router)
api_router.include_router(forecast.router)
api_router.include_router(plans.router)
api_router.include_router(ai.router)
