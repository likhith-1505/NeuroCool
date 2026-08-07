"""SimulationService — owns the in-memory digital twin and its tick loop.

A single instance is created in app.main's lifespan handler and lives for
the process's lifetime (stored on `app.state.simulation`, not a bare module
global — see app.api.deps.get_simulation). All live telemetry stays in
memory; only significant transitions become durable Event rows (see
app.services.event_service).
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models.cluster import Cluster
from app.models.enums import RackStatus
from app.models.rack import Rack
from app.schemas.event import EventRead
from app.schemas.telemetry import TelemetrySnapshot
from app.services.event_service import detect_rack_events, persist_events
from app.simulation.physics import compute_cluster_state, compute_next_rack_state
from app.simulation.seed import DEFAULT_CLUSTER_LOCATION, DEFAULT_CLUSTER_NAME, RACK_SEEDS
from app.simulation.state import ClusterState, RackInternals, RackState
from app.utils.time import utcnow
from app.websocket.manager import manager

logger = logging.getLogger(__name__)

# Sane starting point for a freshly-seeded, "just booted" rack — the
# physics chain takes it from here every subsequent tick.
_INITIAL_TEMPERATURE_C = 60.0
_INITIAL_CPU_UTILIZATION = 40.0
_INITIAL_POWER_KW = 8.0
_INITIAL_COOLING_EFFICIENCY = 65.0
_INITIAL_FAN_SPEED = 40.0
_INITIAL_HEALTH_SCORE = 90.0


class SimulationService:
    """Maintains live cluster/rack telemetry and drives the tick loop."""

    def __init__(self, tick_seconds: float | None = None) -> None:
        self.tick_seconds = tick_seconds if tick_seconds is not None else settings.SIMULATION_TICK_SECONDS
        self._rng = random.Random()
        self._cluster: ClusterState | None = None
        self._racks: dict[uuid.UUID, RackState] = {}
        self._internals: dict[uuid.UUID, RackInternals] = {}
        self._task: asyncio.Task[None] | None = None
        self._last_tick_at: datetime | None = None

    # --- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        """Seed (if needed) and begin the tick loop. Safe to call once."""
        if self._task is not None:
            return

        async with AsyncSessionLocal() as db:
            cluster, racks = await self._ensure_seed_data(db)

        self._cluster = ClusterState(
            id=cluster.id,
            name=cluster.name,
            overall_health=_INITIAL_HEALTH_SCORE,
            average_temperature=_INITIAL_TEMPERATURE_C,
            total_power=_INITIAL_POWER_KW * len(racks),
            cooling_efficiency=_INITIAL_COOLING_EFFICIENCY,
            energy_savings=15.0,
            prediction_confidence=90.0,
        )
        for rack in racks:
            self._racks[rack.id] = self._initial_rack_state(rack)
            self._internals[rack.id] = RackInternals(
                gpu_baseline=self._racks[rack.id].gpu_utilization,
                jobs_baseline=float(self._racks[rack.id].running_jobs),
            )

        self._task = asyncio.create_task(self._run(), name="simulation-tick-loop")
        logger.info("Simulation started: %d rack(s), tick=%.1fs", len(self._racks), self.tick_seconds)

    async def stop(self) -> None:
        """Cancel the tick loop. Safe to call even if never started."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("Simulation stopped")

    # --- read access for the REST API and WebSocket endpoint -----------

    @property
    def cluster_state(self) -> ClusterState:
        if self._cluster is None:
            raise RuntimeError("Simulation has not started yet")
        return self._cluster

    @property
    def rack_states(self) -> list[RackState]:
        return list(self._racks.values())

    def rack_state(self, rack_id: uuid.UUID) -> RackState | None:
        return self._racks.get(rack_id)

    # --- tick loop -------------------------------------------------------

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A single bad tick (e.g. a transient DB error while
                # persisting events) should never kill the simulation —
                # log it and keep the digital twin running.
                logger.exception("Simulation tick failed")
            await asyncio.sleep(self.tick_seconds)

    async def _tick(self) -> None:
        assert self._cluster is not None

        drafts = []
        next_racks: dict[uuid.UUID, RackState] = {}
        cluster_id = self._cluster.id

        for rack_id, previous in self._racks.items():
            internals = self._internals[rack_id]
            current, next_internals = compute_next_rack_state(previous, internals, self._rng)
            next_racks[rack_id] = current
            self._internals[rack_id] = next_internals
            drafts.extend(detect_rack_events(cluster_id, previous, current))

        self._racks = next_racks
        self._cluster = compute_cluster_state(cluster_id, self._cluster.name, list(self._racks.values()))
        self._last_tick_at = utcnow()

        events_payload: list[dict] = []
        if drafts:
            async with AsyncSessionLocal() as db:
                persisted = await persist_events(db, drafts)
            events_payload = [EventRead.model_validate(event).model_dump(mode="json") for event in persisted]
            for event in persisted:
                logger.info("Event: [%s] %s", event.severity.value, event.title)

        await self._broadcast(events_payload)

    async def _broadcast(self, events_payload: list[dict]) -> None:
        if manager.connection_count == 0:
            return

        snapshot = TelemetrySnapshot.from_simulation(self, timestamp=self._last_tick_at)
        payload = snapshot.model_dump(mode="json")
        if events_payload:
            payload["events"] = events_payload
        await manager.broadcast(payload)

    # --- seeding -----------------------------------------------------------

    @staticmethod
    async def _ensure_seed_data(db: AsyncSession) -> tuple[Cluster, list[Rack]]:
        """Idempotent: reuses the existing cluster/racks if this isn't the
        first time the app has started against this database.
        """
        result = await db.execute(select(Cluster).limit(1))
        cluster = result.scalar_one_or_none()

        if cluster is None:
            cluster = Cluster(name=DEFAULT_CLUSTER_NAME, location=DEFAULT_CLUSTER_LOCATION)
            db.add(cluster)
            await db.flush()  # assign cluster.id without committing yet

            racks = [Rack(cluster_id=cluster.id, name=str(seed["name"])) for seed in RACK_SEEDS]
            db.add_all(racks)
            await db.commit()
            for rack in racks:
                await db.refresh(rack)
            logger.info("Seeded default cluster %r with %d rack(s)", cluster.name, len(racks))
        else:
            result = await db.execute(select(Rack).where(Rack.cluster_id == cluster.id).order_by(Rack.name))
            racks = list(result.scalars().all())

        return cluster, racks

    @staticmethod
    def _initial_rack_state(rack: Rack) -> RackState:
        seed = next((s for s in RACK_SEEDS if s["name"] == rack.name), RACK_SEEDS[0])
        return RackState(
            id=rack.id,
            name=rack.name,
            temperature=_INITIAL_TEMPERATURE_C,
            gpu_utilization=float(seed["baseline_gpu"]),
            cpu_utilization=_INITIAL_CPU_UTILIZATION,
            power_draw=_INITIAL_POWER_KW,
            cooling_efficiency=_INITIAL_COOLING_EFFICIENCY,
            fan_speed=_INITIAL_FAN_SPEED,
            health_score=_INITIAL_HEALTH_SCORE,
            prediction_state="stable",
            running_jobs=int(seed["baseline_jobs"]),
            status=RackStatus.HEALTHY,
        )
