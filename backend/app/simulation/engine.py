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

Decision orchestration follows the identical pattern: this class owns a
DecisionService (see app.ai.service), asks it to evaluate the cluster every
tick, and folds the lifecycle events it returns into the same broadcast.
Which reasoning strategy DecisionService uses (RuleBasedDecisionEngine
today) is a construction-time detail — see __init__ — not something this
class, the REST API, or the WebSocket endpoint need to know about.

Execution closes the loop: this class also owns an ExecutionService (see
app.execution.service), which POST /api/decisions/{id}/execute triggers via
execute_decision. Every tick, its RackDrivers contribution is combined
(see combine_drivers) with the scenario's and fed into the exact same
physics call — an executed remediation is just another input the physics
engine already knew how to accept, never a special-cased temperature write.

Forecasting runs alongside the simulation the same way: this class owns a
ForecastService (see app.forecasting.service), fed this tick's post-physics
racks every tick, *before* DecisionService.evaluate — so the Decision
Engine consumes the freshest predictions as a plain argument, the same way
it already consumes ClusterState/RackState. The forecasting and decision
engines otherwise stay independent — this class is the only place that
calls both.

The Optimization Engine sits between the two: this class also owns an
OptimizationService (see app.optimization.service), ticked every cycle
right after ForecastService and *before* DecisionService.evaluate, fed the
same forecasts plus recent events. DecisionService consumes its plans
instead of reading forecasts directly (see app.ai.rules._rule_from_
optimization_plan) — every recommendation the Decision Engine now makes
from a forecast-driven trigger has already been through candidate
generation, physics-based simulation, and scoring first.

Lifecycle: `initialize()` (called once from app.main's lifespan) seeds the
database and builds the baseline in-memory digital twin, but deliberately
never starts the tick loop — the app must always boot straight into IDLE
(see app.simulation.state.SimulationStatus), with a human explicitly
starting the simulation via POST /api/simulation/start. `start()`/
`pause()`/`resume()` only ever create or cancel the one tick-loop task;
`reset()` additionally restores the deterministic healthy baseline every
rack was seeded with and clears every dependent service's *active* state
(never its durable history — see each service's own `reset()`). Every
transition is idempotent (see each method's docstring) and broadcasts a
SIMULATION_* WebSocket event over the exact same connection manager the
regular per-tick broadcast already uses — this is the "single
authoritative simulation state" dependent services and the frontend both
read, rather than each independently tracking whether the app is "live".
Forecasting/optimization/decisions/execution never need their own IDLE/
PAUSED awareness at all: they are *only* ever ticked from inside `_tick()`,
so simply not running the tick loop already means none of them do
anything while stopped — no separate guard needed in any of them.
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

from app.ai.rules import RuleBasedDecisionEngine
from app.ai.service import DecisionService
from app.config import settings
from app.db.session import AsyncSessionLocal
from app.execution.service import ExecutionService
from app.forecasting.base import RackPrediction
from app.forecasting.service import ForecastService
from app.forecasting.trend import TrendForecastEngine
from app.models.cluster import Cluster
from app.models.decision import Decision
from app.models.enums import EventSeverity, RackStatus
from app.models.event import Event
from app.models.execution import Execution
from app.models.optimization_plan import OptimizationPlan
from app.models.rack import Rack
from app.models.scenario import Scenario
from app.optimization.planner import SimulationOptimizer
from app.optimization.service import OptimizationService
from app.schemas.event import EventRead
from app.schemas.scenario import ScenarioStatus
from app.schemas.simulation import SimulationStatusRead
from app.schemas.telemetry import TelemetrySnapshot
from app.services.event_service import EventDraft, detect_rack_events, persist_events
from app.simulation.physics import compute_cluster_state, compute_next_rack_state
from app.simulation.scenario_manager import SCENARIOS, ScenarioDefinition, ScenarioManager
from app.simulation.seed import DEFAULT_CLUSTER_LOCATION, DEFAULT_CLUSTER_NAME, RACK_SEEDS
from app.simulation.state import NO_DRIVERS, ClusterState, RackInternals, RackState, SimulationStatus, combine_drivers
from app.utils.time import utcnow
from app.websocket.manager import manager

logger = logging.getLogger(__name__)

# Sane starting point for a freshly-seeded, "just booted" rack — the
# physics chain takes it from here every subsequent tick. Chosen close to
# the physics engine's own equilibrium for a mid-range baseline GPU load
# (worked out from compute_next_rack_state's target formulas) rather than
# an arbitrary round number, so there's only a small settling transient on
# boot instead of a multi-tick "temperature rising, cooling falling" ramp
# that would otherwise look identical to genuine degradation to
# RuleBasedDecisionEngine's trend-based rules (see app.ai.rules).
_INITIAL_TEMPERATURE_C = 64.0
_INITIAL_CPU_UTILIZATION = 41.0
_INITIAL_POWER_KW = 9.3
_INITIAL_COOLING_EFFICIENCY = 58.5
_INITIAL_FAN_SPEED = 36.0
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
        self._decision_service = DecisionService(engine=RuleBasedDecisionEngine())
        self._execution_service = ExecutionService()
        self._forecast_service = ForecastService(engine=TrendForecastEngine())
        self._optimization_service = OptimizationService(engine=SimulationOptimizer(tick_seconds=self.tick_seconds))
        # --- lifecycle state (see app.simulation.state.SimulationStatus) ---
        self._initialized = False
        self._status = SimulationStatus.IDLE
        self._tick_count = 0
        self._started_at: datetime | None = None
        self._paused_at: datetime | None = None

    # --- lifecycle -----------------------------------------------------

    async def initialize(self) -> None:
        """Seed the database (idempotent) and build the baseline in-memory
        digital twin — but never start the tick loop. Called once from
        app.main's lifespan so REST/WebSocket endpoints have real state to
        serve immediately (GET /api/cluster, /api/racks, /ws/telemetry —
        all still work while IDLE), without the "the datacenter immediately
        starts changing telemetry" problem this lifecycle exists to fix.
        Safe to call more than once; only does real work the first time.
        """
        if self._initialized:
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
            self._racks[rack.id] = self._initial_rack_state(rack.id, rack.name)
            self._internals[rack.id] = RackInternals(
                gpu_baseline=self._racks[rack.id].gpu_utilization,
                jobs_baseline=float(self._racks[rack.id].running_jobs),
            )

        self._initialized = True
        logger.info(
            "Simulation initialized: %d rack(s), tick=%.1fs, status=%s",
            len(self._racks), self.tick_seconds, self._status.value,
        )

    async def start(self) -> SimulationStatusRead:
        """Begin the tick loop. Idempotent: a no-op returning the current
        state if already RUNNING; delegates to resume() if PAUSED (there is
        no meaningful difference between "start" and "resume" once telemetry
        already exists, and the frontend never shows both buttons at once —
        see the objective's PAUSED-state control).
        """
        if not self._initialized:
            await self.initialize()

        if self._status == SimulationStatus.RUNNING:
            return self.status
        if self._status == SimulationStatus.PAUSED:
            return await self.resume()

        self._started_at = utcnow()
        self._paused_at = None
        self._tick_count = 0
        self._status = SimulationStatus.RUNNING
        self._task = asyncio.create_task(self._run(), name="simulation-tick-loop")
        logger.info("Simulation running: %d rack(s), tick=%.1fs", len(self._racks), self.tick_seconds)
        await self._broadcast_simulation_event("SIMULATION_STARTED")
        return self.status

    async def pause(self) -> SimulationStatusRead:
        """Stop the tick loop without losing any state. Idempotent: a
        no-op returning the current state unless currently RUNNING.
        """
        if self._status != SimulationStatus.RUNNING:
            return self.status
        await self._cancel_task()
        self._status = SimulationStatus.PAUSED
        self._paused_at = utcnow()
        logger.info("Simulation paused at tick %d", self._tick_count)
        await self._broadcast_simulation_event("SIMULATION_PAUSED")
        return self.status

    async def resume(self) -> SimulationStatusRead:
        """Continue ticking from the exact current state — never resets
        telemetry. Idempotent: a no-op returning the current state unless
        currently PAUSED.
        """
        if self._status != SimulationStatus.PAUSED:
            return self.status
        self._paused_at = None
        self._status = SimulationStatus.RUNNING
        self._task = asyncio.create_task(self._run(), name="simulation-tick-loop")
        logger.info("Simulation resumed at tick %d", self._tick_count)
        await self._broadcast_simulation_event("SIMULATION_RESUMED")
        return self.status

    async def reset(self) -> SimulationStatusRead:
        """Stop the tick loop, clear the active scenario and every
        dependent service's transient/active state, and restore the exact
        deterministic healthy baseline every rack was seeded with — then
        return to IDLE. Safe to call repeatedly (each call independently
        restores the same baseline). Durable history (Cluster/Rack
        identity, Events, Decisions, Executions, OptimizationPlans in the
        database) is never touched or deleted — only the live, in-memory
        state every dependent service reacts to.
        """
        await self._cancel_task()

        # Reuses the *existing* scenario-reset path (persists + broadcasts
        # its own "Scenario Reset" event) rather than duplicating it.
        await self.reset_scenario()

        for rack_id, previous in self._racks.items():
            fresh = self._initial_rack_state(rack_id, previous.name)
            self._racks[rack_id] = fresh
            self._internals[rack_id] = RackInternals(
                gpu_baseline=fresh.gpu_utilization, jobs_baseline=float(fresh.running_jobs)
            )

        if self._cluster is not None:
            self._cluster = ClusterState(
                id=self._cluster.id,
                name=self._cluster.name,
                overall_health=_INITIAL_HEALTH_SCORE,
                average_temperature=_INITIAL_TEMPERATURE_C,
                total_power=_INITIAL_POWER_KW * len(self._racks),
                cooling_efficiency=_INITIAL_COOLING_EFFICIENCY,
                energy_savings=15.0,
                prediction_confidence=90.0,
            )

        self._forecast_service.reset()
        self._optimization_service.reset()
        self._decision_service.reset()
        self._execution_service.reset()

        self._last_tick_at = None
        self._tick_count = 0
        self._started_at = None
        self._paused_at = None
        self._status = SimulationStatus.IDLE
        logger.info("Simulation reset to baseline: %d rack(s)", len(self._racks))
        await self._broadcast_simulation_event("SIMULATION_RESET")
        return self.status

    async def stop(self) -> None:
        """Cleanly cancel the tick loop, if running — called from
        app.main's lifespan shutdown. Safe to call even if the simulation
        was never started, or is already paused/idle. Deliberately doesn't
        change `status`/broadcast anything: the process is exiting, there's
        no client left to notify and nothing to resume into next time (see
        the module docstring — a fresh process always boots into IDLE).
        """
        await self._cancel_task()
        logger.info("Simulation shut down (status was %s)", self._status.value)

    async def _cancel_task(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    # --- read access for the REST API and WebSocket endpoint -----------

    @property
    def status(self) -> SimulationStatusRead:
        return SimulationStatusRead(
            status=self._status, tick=self._tick_count, started_at=self._started_at, paused_at=self._paused_at
        )

    @property
    def cluster_state(self) -> ClusterState:
        if self._cluster is None:
            raise RuntimeError("Simulation has not been initialized yet")
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
            can_replay=manager_.can_replay,
        )

    @staticmethod
    def available_scenarios() -> list[ScenarioDefinition]:
        return ScenarioManager.available_scenarios()

    # --- scenario control (called from the REST API) -----------------------

    async def activate_scenario(self, key: str) -> ScenarioStatus:
        """Raises ValueError (-> 400 at the API layer) for an unknown key,
        or if the simulation isn't RUNNING — a scenario must never
        silently start the simulation loop for the user (see the
        objective); the operator has to press Start first.
        """
        self._require_running_for_scenario()
        definition = self._scenario_manager.activate(key, self.rack_states)
        await self._emit_scenario_event(definition, kind="activate")
        return self.scenario_status

    async def reset_scenario(self) -> ScenarioStatus:
        """Always allowed, regardless of simulation status — resetting the
        active scenario back to normal is itself never destructive, and is
        also reused by SimulationService.reset() while the tick loop is
        already stopped.
        """
        definition = self._scenario_manager.reset(self.rack_states)
        await self._emit_scenario_event(definition, kind="reset")
        return self.scenario_status

    async def replay_scenario(self) -> ScenarioStatus:
        """Raises ValueError (-> 400 at the API layer) if nothing has run
        yet, or if the simulation isn't RUNNING (see activate_scenario).
        """
        self._require_running_for_scenario()
        definition = self._scenario_manager.replay(self.rack_states)
        await self._emit_scenario_event(definition, kind="activate")
        return self.scenario_status

    def _require_running_for_scenario(self) -> None:
        if self._status != SimulationStatus.RUNNING:
            raise ValueError("Start the simulation before running a scenario.")

    # --- decisions (called from the REST API) -------------------------------

    @property
    def active_decisions(self) -> list[Decision]:
        """Currently pending/accepted decisions — what TelemetrySnapshot exposes."""
        return self._decision_service.active_decisions

    @property
    def all_decisions(self) -> list[Decision]:
        return self._decision_service.all_decisions

    def get_decision(self, decision_id: uuid.UUID) -> Decision | None:
        return self._decision_service.get(decision_id)

    async def accept_decision(self, decision_id: uuid.UUID) -> Decision:
        """Raises LookupError (-> 404) or ValueError (-> 400) at the API layer."""
        decision, events = await self._decision_service.accept(decision_id)
        await self._broadcast_decision_events(events)
        return decision

    async def reject_decision(self, decision_id: uuid.UUID) -> Decision:
        decision, events = await self._decision_service.reject(decision_id)
        await self._broadcast_decision_events(events)
        return decision

    async def execute_decision(self, decision_id: uuid.UUID) -> Decision:
        """Marks the decision executed, then hands it to ExecutionService to
        actually begin remediation. Always returns the (now EXECUTED)
        decision — a remediation that couldn't find a viable target is a
        FAILED Execution record, not an HTTP error: the execute *call*
        succeeded either way, see ExecutionService.start.
        """
        decision, decision_events = await self._decision_service.execute(decision_id)
        _, execution_events = await self._execution_service.start(
            decision=decision,
            racks=self.rack_states,
            cluster_db_id=self.cluster_state.id,
            scenario_db_id=self._scenario_db_ids.get(self._scenario_manager.active_key),
            now=utcnow(),
        )
        await self._broadcast_decision_events(decision_events + execution_events)
        return decision

    # --- executions (read access; started only via execute_decision) -------

    @property
    def all_executions(self) -> list[Execution]:
        return self._execution_service.all_executions

    def get_execution(self, execution_id: uuid.UUID) -> Execution | None:
        return self._execution_service.get(execution_id)

    # --- forecasts (read access; recomputed every tick) ---------------------

    @property
    def cluster_forecast(self) -> list[RackPrediction]:
        return self._forecast_service.cluster_forecast

    @property
    def rack_forecasts(self) -> dict[uuid.UUID, list[RackPrediction]]:
        return self._forecast_service.rack_forecasts

    def rack_forecast(self, rack_id: uuid.UUID) -> list[RackPrediction]:
        return self._forecast_service.rack_forecast(rack_id)

    # --- optimization plans (read access) -----------------------------------

    @property
    def active_plans(self) -> list[OptimizationPlan]:
        """Plans whose trigger is still active — what TelemetrySnapshot exposes."""
        return self._optimization_service.active_plans

    @property
    def all_plans(self) -> list[OptimizationPlan]:
        return self._optimization_service.all_plans

    @property
    def latest_plan(self) -> OptimizationPlan | None:
        return self._optimization_service.latest_plan

    def get_plan(self, plan_id: uuid.UUID) -> OptimizationPlan | None:
        return self._optimization_service.get(plan_id)

    async def _broadcast_decision_events(self, events: list[Event]) -> None:
        """Immediate broadcast for a REST-triggered decision transition —
        the tick loop's regular broadcast already covers decisions that
        change as part of a tick (creation, expiry, confidence updates);
        this covers accept/reject/execute, which happen between ticks.
        """
        events_payload = [EventRead.model_validate(event).model_dump(mode="json") for event in events]
        for event in events:
            logger.info("Event: [%s] %s", event.severity.value, event.title)
        await self._broadcast(events_payload)

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
        self._tick_count += 1
        now = utcnow()

        # A duration-bound scenario (e.g. power_surge) can complete on its
        # own between REST calls — nobody has to poll or reset it manually.
        completed = self._scenario_manager.maybe_auto_revert(now)
        if completed is not None:
            await self._emit_scenario_event(completed, kind="auto_complete")

        scenario_drivers = self._scenario_manager.compute_drivers(list(self._racks.values()), now)
        execution_drivers, execution_events = await self._execution_service.tick(now)
        active_scenario_db_id = self._scenario_db_ids.get(self._scenario_manager.active_key)

        drafts: list[EventDraft] = []
        next_racks: dict[uuid.UUID, RackState] = {}
        cluster_id = self._cluster.id

        for rack_id, previous in self._racks.items():
            internals = self._internals[rack_id]
            # An executed remediation influences the same physics tick a
            # scenario does, through the exact same RackDrivers seam —
            # combined here rather than one overriding the other, so a
            # partially-effective remediation against an ongoing scenario
            # is an emergent outcome, not a special case.
            drivers = combine_drivers(
                scenario_drivers.get(rack_id, NO_DRIVERS),
                execution_drivers.get(rack_id, NO_DRIVERS),
            )
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

        persisted: list[Event] = []
        if drafts:
            async with AsyncSessionLocal() as db:
                persisted = await persist_events(db, drafts)
            for event in persisted:
                logger.info("Event: [%s] %s", event.severity.value, event.title)

        # Forecasting runs before decisions so DecisionService can consume
        # the freshest predictions this same tick — the Decision Engine
        # reasons over "current telemetry + forecast", not current
        # telemetry alone. The two engines otherwise never talk to each
        # other directly (see app.forecasting.service's module docstring).
        forecast_events = await self._forecast_service.tick(
            racks=list(self._racks.values()),
            scenario_key=self._scenario_manager.active_key,
            cluster_id=cluster_id,
            scenario_db_id=active_scenario_db_id,
            now=now,
        )
        persisted.extend(forecast_events)

        # Optimization runs after forecasting and before decisions, for the
        # same reason: DecisionService should consume this tick's freshest
        # plans, not last tick's (see app.optimization.service's module
        # docstring for why plans are dedup'd/refreshed in place instead of
        # persisting a new row every tick a trigger keeps holding).
        plans_by_rack, optimization_events = await self._optimization_service.tick(
            cluster=self._cluster,
            racks=list(self._racks.values()),
            scenario_key=self._scenario_manager.active_key,
            forecasts=self._forecast_service.rack_forecasts,
            cluster_db_id=cluster_id,
            scenario_db_id=active_scenario_db_id,
            now=now,
        )
        persisted.extend(optimization_events)

        # The DecisionEngine evaluates every tick, exactly like the physics
        # step — it only ever sees the resulting telemetry (and now the
        # optimization plans), never which scenario (if any) produced it.
        decision_events = await self._decision_service.evaluate(
            cluster=self._cluster,
            racks=list(self._racks.values()),
            scenario_key=self._scenario_manager.active_key,
            scenario_target_rack_id=self._scenario_manager.target_rack_id,
            cluster_db_id=cluster_id,
            scenario_db_id=active_scenario_db_id,
            now=now,
            plans=plans_by_rack,
        )
        persisted.extend(decision_events)
        persisted.extend(execution_events)

        events_payload = [EventRead.model_validate(event).model_dump(mode="json") for event in persisted]
        await self._broadcast(events_payload)

    async def _broadcast(self, events_payload: list[dict]) -> None:
        if manager.connection_count == 0:
            return

        snapshot = TelemetrySnapshot.from_simulation(self, timestamp=self._last_tick_at)
        payload = snapshot.model_dump(mode="json")
        if events_payload:
            payload["events"] = events_payload
        await manager.broadcast(payload)

    async def _broadcast_simulation_event(self, event_type: str) -> None:
        """SIMULATION_STARTED/PAUSED/RESUMED/RESET — reuses the existing
        WebSocket connection manager (no second WebSocket system), the
        exact same "type"-discriminated pattern app.neurocore.actions.
        PendingActionService._broadcast already uses for AI_ACTION_*
        events. Needed as its own broadcast (distinct from the regular
        per-tick one above) specifically *because* IDLE/PAUSED produce no
        tick broadcasts at all — this is the only way a client (including
        one connected from a different tab) learns the lifecycle changed.
        """
        if manager.connection_count == 0:
            return
        payload = {"type": event_type, "simulation": self.status.model_dump(mode="json")}
        try:
            await manager.broadcast(payload)
        except Exception:  # pragma: no cover - defensive, mirrors the tick loop's own broadcast guard
            logger.exception("Failed to broadcast simulation event %s", event_type)

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
    def _initial_rack_state(rack_id: uuid.UUID, name: str) -> RackState:
        """Takes just the id/name (not a full ORM Rack row) so
        SimulationService.reset() can rebuild the exact same baseline for
        an already-in-memory rack without another database round trip.
        """
        seed = next((s for s in RACK_SEEDS if s["name"] == name), RACK_SEEDS[0])
        return RackState(
            id=rack_id,
            name=name,
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
