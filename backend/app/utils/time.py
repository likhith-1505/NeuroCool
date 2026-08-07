"""Time helpers shared across the backend."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return a timezone-aware current UTC timestamp.

    Centralized so every "now" in the codebase is timezone-aware and
    consistent, matching the timezone-aware DateTime columns in the models.
    """
    return datetime.now(UTC)
