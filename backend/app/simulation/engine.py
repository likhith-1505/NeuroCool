"""SimulationService — owns the in-memory digital twin and its tick loop.

A single instance is created in app.main's lifespan handler and lives for
the process's lifetime (stored on `app.state.simulation`, not a bare module
global — see app.api.deps.get_simulation). All live telemetry stays in
memory; only significant transitions become durable Event rows (see
app.services.event_service).

Scenario orchestration lives in app.simulation.scenario_manager. This class
is the *only* place that knows about both telemetry and scenarios: it asks
ScenarioManager for this tick's per-rack RackDrivers and folds them into the
same physics call every tick already made, and it is the only place that
turns a scenario transition into a persisted + broadcast Event, exactly as
it already does for ordinary telemetry events.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import replace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models.cluster import Cluster
from app.models.enums import EventSeverity, RackStatus
from app.models.rack import Rack
from app.models.scenario import Scenario
from app.schemas.event import EventRead
from app.schemas.scenario import ScenarioStatus
from app.schemas.telemetry import TelemetrySnapshot
from app.services.event_service import EventDraft, detect_rack_events, persist_events
from app.simulation.physics import compute_cluster_state, compute_next_rack_state
from app.simulation.scenario_manager import SCENARIOS, ScenarioDefinition, ScenarioManager
from app.simulation.seed import DEFAULT_CLUSTER_LOCATION, DEFAULT_CLUSTER_NAME, RACK_SEEDS
from app.simulation.state import NO_DRIVERS, ClusterState, RackInternals, RackState
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

# Titles for the event raised the moment a scenario is activated. "normal"
# only gets this generic title when activated via POST /api/scenario
# directly — the dedicated /reset endpoint always raises "Scenario Reset"
# instead (see SimulationService.reset_scenario), and an automatic
# duration-based revert always raises "Scenario Completed" (see
# SimulationService._tick). Every other key has a specific, more
# descriptive title than the generic fallback.
_SCENARIO_START_TITLES: dict[str, str] = {
    "normal": "Scenario Started",
    "training_burst": "Training Burst Started",
    "thermal_spike": "Thermal Spike Triggered",
    "cooling_failure": "Cooling Failure Detected",
    "power_surge": "Power Surge Detected",
}
_SCENARIO_START_SEVERITY: dict[str, EventSeverity] = {
    "normal": EventSeverity.INFO,
    "training_burst": EventSeverity.INFO,
    "thermal_spike": EventSeverity.WARNING,
    "cooling_failure": EventSeverity.CRITICAL,
    "power_surge": EventSeverity.WARNING,
}


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
        self._scenario_manager = ScenarioManager()
        self._scenario_db_ids: dict[str, uuid.UUID] = {}

    # --- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        """Seed (if needed) and begin the tick loop. Safe to call once."""
        if self._task is not None:
            return

        async with AsyncSessionLocal() as db:
            cluster, racks = await self._ensure_seed_data(db)
            self._scenario_db_ids = await self._ensure_scenario_rows(db)

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

    @property
    def scenario_status(self) -> ScenarioStatus:
        manager_ = self._scenario_manager
        return ScenarioStatus(
            key=manager_.active_key,
            name=SCENARIOS[manager_.active_key].name,
            transition_state=manager_.transition_state,
            target_rack_id=manager_.target_rack_id,
            activated_at=manager_.activated_at,
        )

    @staticmethod
    def available_scenarios() -> list[ScenarioDefinition]:
        return ScenarioManager.available_scenarios()

    # --- scenario control (called from the REST API) -----------------------

    async def activate_scenario(self, key: str) -> ScenarioStatus:
        """Raises ValueError (-> 400 at the API layer) for an unknown key."""
        definition = self._scenario_manager.activate(key, self.rack_states)
        await self._emit_scenario_event(definition, kind="activate")
        return self.scenario_status

    async def reset_scenario(self) -> ScenarioStatus:
        definition = self._scenario_manager.reset(self.rack_states)
        await self._emit_scenario_event(definition, kind="reset")
        return self.scenario_status

    async def replay_scenario(self) -> ScenarioStatus:
        """Raises ValueError (-> 400 at the API layer) if nothing has run yet."""
        definition = self._scenario_manager.replay(self.rack_states)
        await self._emit_scenario_event(definition, kind="activate")
        return self.scenario_status

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
        now = utcnow()

        # A duration-bound scenario (e.g. power_surge) can complete on its
        # own between REST calls — nobody has to poll or reset it manually.
        completed = self._scenario_manager.maybe_auto_revert(now)
        if completed is not None:
            await self._emit_scenario_event(completed, kind="auto_complete")

        drivers_by_rack = self._scenario_manager.compute_drivers(list(self._racks.values()), now)
        active_scenario_db_id = self._scenario_db_ids.get(self._scenario_manager.active_key)

        drafts: list[EventDraft] = []
        next_racks: dict[uuid.UUID, RackState] = {}
        cluster_id = self._cluster.id

        for rack_id, previous in self._racks.items():
            internals = self._internals[rack_id]
            drivers = drivers_by_rack.get(rack_id, NO_DRIVERS)
            current, next_internals = compute_next_rack_state(previous, internals, self._rng, drivers)
            next_racks[rack_id] = current
            self._internals[rack_id] = next_internals
            drafts.extend(detect_rack_events(cluster_id, previous, current))

        # Attribute ordinary telemetry events to whichever scenario was
        # active when they happened, so an incident and the scenario that
        # caused it stay linked (Event.scenario_id).
        if active_scenario_db_id is not None:
            drafts = [replace(draft, scenario_id=active_scenario_db_id) for draft in drafts]

        self._racks = next_racks
        self._cluster = compute_cluster_state(cluster_id, self._cluster.name, list(self._racks.values()))
        self._last_tick_at = now

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

    # --- scenario events -----------------------------------------------------

    async def _emit_scenario_event(self, definition: ScenarioDefinition, *, kind: str) -> None:
        """Persist + immediately broadcast the event for a scenario
        transition. Reuses the exact same event_service/broadcast plumbing
        the tick loop uses for telemetry events — scenario events are not a
        separate system, just a different source of EventDrafts.
        """
        if kind == "reset":
            title = "Scenario Reset"
            severity = EventSeverity.INFO
            message = "Cluster reset to the normal baseline profile."
        elif kind == "auto_complete":
            title = "Scenario Completed"
            severity = EventSeverity.INFO
            message = f"{definition.name} finished its course; cluster reverting to normal."
        else:
            title = _SCENARIO_START_TITLES.get(definition.key, "Scenario Started")
            severity = _SCENARIO_START_SEVERITY.get(definition.key, EventSeverity.INFO)
            message = definition.description

        async with AsyncSessionLocal() as db:
            # Keep the durable Scenario rows' is_active flag in sync with
            # the in-memory ScenarioManager, which remains the source of
            # truth for "what's active right now" (avoids a DB round-trip
            # on every tick just to answer that question).
            await self._sync_active_scenario_flags(db, definition.key)

            draft = EventDraft(
                cluster_id=self.cluster_state.id,
                rack_id=self._scenario_manager.target_rack_id,
                scenario_id=self._scenario_db_ids.get(definition.key),
                severity=severity,
                title=title,
                message=message,
            )
            persisted = await persist_events(db, [draft])

        events_payload = [EventRead.model_validate(event).model_dump(mode="json") for event in persisted]
        for event in persisted:
            logger.info("Event: [%s] %s", event.severity.value, event.title)
        await self._broadcast(events_payload)

    @staticmethod
    async def _sync_active_scenario_flags(db: AsyncSession, active_key: str) -> None:
        result = await db.execute(select(Scenario))
        for row in result.scalars().all():
            row.is_active = row.key == active_key
        # Left uncommitted here — persist_events()'s commit, called right
        # after on the same session, flushes this together with the new
        # event atomically.

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
    async def _ensure_scenario_rows(db: AsyncSession) -> dict[str, uuid.UUID]:
        """Idempotent: upserts one DB row per built-in scenario definition
        and returns a key -> row id map, so events can be linked to the
        scenario that was active when they occurred (Event.scenario_id).
        """
        result = await db.execute(select(Scenario))
        existing = {row.key: row for row in result.scalars().all()}

        created_any = False
        for definition in SCENARIOS.values():
            if definition.key in existing:
                continue
            row = Scenario(
                key=definition.key,
                name=definition.name,
                description=definition.description,
                is_active=(definition.key == "normal"),
            )
            db.add(row)
            existing[definition.key] = row
            created_any = True

        if created_any:
            await db.commit()
            for row in existing.values():
                await db.refresh(row)
            logger.info("Seeded %d scenario definition(s)", len(existing))

        return {key: row.id for key, row in existing.items()}

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
