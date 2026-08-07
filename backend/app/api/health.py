"""Liveness/readiness endpoint.

Performs a real round-trip against both PostgreSQL and Redis on every call
rather than reporting a hardcoded status, so the response is trustworthy.
"""

import logging

from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_redis
from app.schemas.health import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
) -> HealthResponse:
    database_status: str = "connected"
    redis_status: str = "connected"

    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Database health check failed")
        database_status = "disconnected"

    try:
        await redis_client.ping()
    except Exception:
        logger.exception("Redis health check failed")
        redis_status = "disconnected"

    overall = "healthy" if database_status == "connected" and redis_status == "connected" else "unhealthy"
    response.status_code = (
        status.HTTP_200_OK if overall == "healthy" else status.HTTP_503_SERVICE_UNAVAILABLE
    )

    return HealthResponse(status=overall, database=database_status, redis=redis_status)
