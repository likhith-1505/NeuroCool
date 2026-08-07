"""Application-wide logging configuration."""

import logging
import sys

from app.config import settings


def configure_logging() -> None:
    """Configure the root logger once at process startup."""
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stdout,
        force=True,
    )

    # Keep third-party access logs at the same level as the rest of the app
    # instead of uvicorn's default, so log verbosity is controlled centrally.
    for noisy_logger in ("uvicorn.access", "uvicorn.error", "sqlalchemy.engine"):
        logging.getLogger(noisy_logger).setLevel(settings.LOG_LEVEL)
