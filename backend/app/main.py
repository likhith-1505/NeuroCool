"""FastAPI application entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import settings
from app.core.logging import configure_logging
from app.db import base as _db_base  # noqa: F401 — import registers every ORM model
from app.neurocore.providers.factory import build_provider_from_settings
from app.neurocore.service import NeuroCoreService
from app.simulation.engine import SimulationService
from app.websocket.router import websocket_router

configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The simulation lives on app.state (not a bare module-level global) so
    # it stays request-accessible via Depends(get_simulation) while still
    # being trivially swappable in tests.
    simulation = SimulationService()
    app.state.simulation = simulation
    await simulation.start()

    # NeuroCore starts regardless of whether an LLM provider is
    # configured — build_provider_from_settings returns None rather than
    # raising when no API key is set, so the rest of the backend (this
    # simulation included) is never affected by missing AI configuration.
    # See app.neurocore.providers.factory and the objective's "backend
    # must still start" requirement.
    provider = build_provider_from_settings(settings)
    app.state.neurocore = NeuroCoreService(provider=provider, max_response_tokens=settings.AI_MAX_RESPONSE_TOKENS)
    if provider is None:
        logger.warning("NeuroCore: no LLM provider available (AI_PROVIDER=%s) — /api/ai/chat will report unavailable.", settings.AI_PROVIDER)
    else:
        logger.info("NeuroCore: using provider=%s model=%s", provider.name, provider.model)

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
