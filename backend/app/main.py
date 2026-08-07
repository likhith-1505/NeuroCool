"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import settings
from app.core.logging import configure_logging
from app.db import base as _db_base  # noqa: F401 — import registers every ORM model
from app.simulation.engine import SimulationService
from app.websocket.router import websocket_router

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The simulation lives on app.state (not a bare module-level global) so
    # it stays request-accessible via Depends(get_simulation) while still
    # being trivially swappable in tests.
    simulation = SimulationService()
    app.state.simulation = simulation
    await simulation.start()
    try:
        yield
    finally:
        await simulation.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="0.1.0",
        lifespan=lifespan,
    )

    if settings.BACKEND_CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(api_router)
    app.include_router(websocket_router)
    return app


app = create_app()
