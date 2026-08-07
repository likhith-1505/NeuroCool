"""Rolling telemetry history — the forecasting engine's raw material.

Kept entirely in-memory, consistent with the rest of the digital twin (see
app.simulation.state's module docstring): per-tick readings are never
persisted directly, only significant transitions become Event rows. A
forecast is derived data, not a record of what happened, so there is
nothing here worth writing to the database either.
"""

from __future__ import annotations

import uuid
from collections import deque
from datetime import datetime, timedelta

from app.forecasting.base import HistoryPoint
from app.simulation.state import RackState

RETENTION = timedelta(minutes=10)


class RackHistory:
    """Rolling history for one rack, capped at RETENTION regardless of tick
    cadence — time-based pruning, not a fixed sample count, so it stays
    correct if SIMULATION_TICK_SECONDS ever changes.
    """

    def __init__(self) -> None:
        self._points: deque[HistoryPoint] = deque()

    def append(self, now: datetime, rack: RackState) -> None:
        # "Avoid storing duplicate data": skip an exact repeat of the last
        # reading. Rare in practice (the physics engine's continuous
        # jitter means back-to-back readings almost never match exactly),
        # but a rack that has fully settled could repeat, and there's
        # nothing gained by keeping redundant points.
        if self._points:
            last = self._points[-1]
            if (
                last.temperature == rack.temperature
                and last.gpu_utilization == rack.gpu_utilization
                and last.power_draw == rack.power_draw
                and last.cooling_efficiency == rack.cooling_efficiency
            ):
                return

        self._points.append(
            HistoryPoint(
                timestamp=now,
                temperature=rack.temperature,
                gpu_utilization=rack.gpu_utilization,
                power_draw=rack.power_draw,
                cooling_efficiency=rack.cooling_efficiency,
            )
        )
        cutoff = now - RETENTION
        while self._points and self._points[0].timestamp < cutoff:
            self._points.popleft()

    def recent(self, window: timedelta | None = None) -> list[HistoryPoint]:
        """All retained points, or only those within `window` of the most
        recent one if given. The trend engine uses a shorter recent window
        than the full 10-minute retention (see app.forecasting.trend) — a
        fresher trend is more informative than one diluted by old history.
        """
        if window is None or not self._points:
            return list(self._points)
        cutoff = self._points[-1].timestamp - window
        return [p for p in self._points if p.timestamp >= cutoff]

    def __len__(self) -> int:
        return len(self._points)


class TelemetryHistory:
    """Per-rack RackHistory, keyed by rack id."""

    def __init__(self) -> None:
        self._by_rack: dict[uuid.UUID, RackHistory] = {}

    def append(self, now: datetime, racks: list[RackState]) -> None:
        for rack in racks:
            self._by_rack.setdefault(rack.id, RackHistory()).append(now, rack)

    def for_rack(self, rack_id: uuid.UUID) -> RackHistory:
        return self._by_rack.setdefault(rack_id, RackHistory())
