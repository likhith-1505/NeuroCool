"""Application configuration.

Settings are sourced from environment variables (and an optional .env file
for local development). Docker Compose injects the real values at runtime —
see docker-compose.yml and .env.example.
"""

import json
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- General ---
    PROJECT_NAME: str = "NeuroCool Backend"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # --- CORS ---
    # NoDecode: pydantic-settings otherwise tries to JSON-decode any env
    # value for a list-typed field *before* field_validator ever runs
    # (docker-compose.yml sets this as a plain comma-separated string, per
    # .env.example) — without it, a real (non-JSON-array) env value raises
    # inside pydantic-settings itself, never reaching _split_cors_origins.
    BACKEND_CORS_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:5173", "http://localhost:3000"]

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        # NoDecode means pydantic-settings never JSON-decodes this field
        # itself (see the field's own comment) — a `[...]`-style env value
        # is parsed here instead, so both a comma-separated list (the
        # documented .env.example format) and a JSON array still work.
        if isinstance(value, str):
            if value.startswith("["):
                return json.loads(value)
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    # --- PostgreSQL ---
    POSTGRES_USER: str = "neurocool"
    POSTGRES_PASSWORD: str = "neurocool"
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "neurocool"
    DB_ECHO: bool = False

    # --- Redis ---
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # --- Simulation ---
    # How often (seconds) the digital twin recomputes telemetry and
    # broadcasts a snapshot to connected WebSocket clients.
    SIMULATION_TICK_SECONDS: float = 1.0

    # --- NeuroCore AI (see app.neurocore) ---
    # Which LLMProvider adapter to construct — "anthropic", "openai", or
    # "mock" (a deterministic, no-network provider useful for local dev
    # without any key). Provider selection is purely configuration-driven;
    # nothing outside app.neurocore.providers imports either vendor SDK.
    # If the selected provider's API key is unset, NeuroCoreService is
    # constructed with no provider and /api/ai/chat reports a clear
    # "unavailable" response — the rest of the backend (simulation,
    # forecasting, optimization, decisions, execution) is unaffected.
    AI_PROVIDER: str = "anthropic"
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str = "claude-sonnet-5"
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    AI_REQUEST_TIMEOUT_SECONDS: float = 30.0
    AI_MAX_RESPONSE_TOKENS: int = 800
    # --- NeuroCore streaming (see app.neurocore.service.NeuroCoreService.
    # answer_stream/chat_stream and POST /api/ai/chat/stream) ---
    # Max time one tool call (see app.neurocore.tools.executor) is allowed
    # to run during a streamed turn before it's treated as failed.
    AI_TOOL_TIMEOUT_SECONDS: float = 15.0
    # Hard ceiling on one streamed chat turn end to end (provider time +
    # every tool call), independent of how many tool round trips it takes —
    # bounds worst-case latency even when every individual timeout above is
    # respected.
    AI_STREAM_TIMEOUT_SECONDS: float = 60.0

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
