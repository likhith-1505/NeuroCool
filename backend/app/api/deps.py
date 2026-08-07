"""Shared FastAPI dependencies, collected in one place for endpoints to import."""

from fastapi import Request

from app.core.redis import get_redis
from app.db.session import get_db
from app.simulation.engine import SimulationService

__all__ = ["get_db", "get_redis", "get_simulation"]


def get_simulation(request: Request) -> SimulationService:
    """The single SimulationService instance, created in app.main's lifespan
    and stored on app.state — not a bare module-level global.
    """
    return request.app.state.simulation
