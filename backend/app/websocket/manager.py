"""In-process WebSocket connection registry for broadcasting live telemetry.

A simple in-memory set is sufficient for a single backend process; if the
service is ever scaled to multiple processes, this is the seam where a
Redis pub/sub fan-out would replace it without touching call sites.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info("WebSocket connected (%d active)", len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        logger.info("WebSocket disconnected (%d active)", len(self._connections))

    async def broadcast(self, payload: dict) -> None:
        """Send a JSON-safe payload to every connected client.

        A broken/disconnected socket is dropped silently instead of raising
        so one bad client can never break the broadcast for everyone else.
        """
        async with self._lock:
            targets = list(self._connections)

        stale: list[WebSocket] = []
        for connection in targets:
            try:
                await connection.send_json(payload)
            except Exception:
                stale.append(connection)

        if stale:
            async with self._lock:
                for connection in stale:
                    self._connections.discard(connection)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()
