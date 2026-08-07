"""Shared FastAPI dependencies, collected in one place for endpoints to import."""

from app.core.redis import get_redis
from app.db.session import get_db

__all__ = ["get_db", "get_redis"]
