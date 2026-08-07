#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] Running database migrations..."
alembic upgrade head

echo "[entrypoint] Starting FastAPI application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
