"""Redis connection wiring.

A single connection pool is created at import time and reused for the life
of the process; individual clients are cheap views onto that pool.
"""

from collections.abc import AsyncGenerator

from redis.asyncio import ConnectionPool, Redis

from app.config import settings

redis_pool: ConnectionPool = ConnectionPool.from_url(
    settings.REDIS_URL,
    decode_responses=True,
)


def get_redis_client() -> Redis:
    """Build a Redis client bound to the shared connection pool."""
    return Redis(connection_pool=redis_pool)


async def get_redis() -> AsyncGenerator[Redis, None]:
    """FastAPI dependency that yields a request-scoped Redis client."""
    client = get_redis_client()
    try:
        yield client
    finally:
        await client.aclose()
