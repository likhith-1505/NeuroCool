"""Shared FastAPI dependencies, collected in one place for endpoints to import."""

from fastapi import Request

from app.core.redis import get_redis
from app.db.session import get_db
from app.neurocore.service import NeuroCoreService
from app.simulation.engine import SimulationService

__all__ = ["get_db", "get_redis", "get_simulation", "get_neurocore"]


def get_simulation(request: Request) -> SimulationService:
    """The single SimulationService instance, created in app.main's lifespan
    and stored on app.state — not a bare module-level global.
    """
    return request.app.state.simulation


def get_neurocore(request: Request) -> NeuroCoreService:
    """The single NeuroCoreService instance, created in app.main's lifespan
    with whichever LLMProvider (or None) app.config.settings resolved to —
    not a bare module-level global. See app.neurocore.providers.factory.
    """
    return request.app.state.neurocore
