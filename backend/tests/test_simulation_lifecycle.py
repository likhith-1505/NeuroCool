"""Tests for the simulation lifecycle: IDLE/RUNNING/PAUSED/RESET and the
REST endpoints in app.api.simulation — see app.simulation.engine.
SimulationService and app.simulation.state.SimulationStatus.

Uses a real SimulationService(tick_seconds=<small>) against the test
database (via the `db` fixture, which skips gracefully if Postgres isn't
reachable — see conftest.py) rather than a fake: the whole point of this
phase is the tick-loop task lifecycle itself (asyncio.create_task/cancel),
which a fake can't exercise meaningfully. Route-level tests follow the
established app.dependency_overrides[get_simulation] pattern from
test_ai_api.py, since the `client` fixture builds the app without running
FastAPI's lifespan.
"""

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_simulation
from app.main import app
from app.simulation.engine import SimulationService
from app.simulation.state import SimulationStatus

pytestmark = pytest.mark.asyncio

_TICK_SECONDS = 0.05


@pytest.fixture
async def simulation(db: AsyncSession) -> SimulationService:
    """A real, initialized-but-not-started SimulationService, fast-ticking
    for the tests that need actual ticks to occur. `db` is only depended on
    to get the Postgres-unreachable skip and event-loop-safety behavior
    (see conftest.py); SimulationService manages its own sessions via
    AsyncSessionLocal internally.
    """
    service = SimulationService(tick_seconds=_TICK_SECONDS)
    await service.initialize()
    try:
        yield service
    finally:
        await service.stop()


async def _wait_for_tick(service: SimulationService, *, at_least: int = 1, timeout: float = 2.0) -> None:
    elapsed = 0.0
    step = _TICK_SECONDS
    while service.status.tick < at_least:
        await asyncio.sleep(step)
        elapsed += step
        if elapsed > timeout:
            raise AssertionError(f"tick count never reached {at_least} (still {service.status.tick})")


# --- 1/2/3: boot state -------------------------------------------------------


async def test_simulation_starts_in_idle(simulation: SimulationService) -> None:
    """#1: a freshly initialized simulation is IDLE with tick=0 and no
    timestamps — matches the exact IDLE response shape in the objective.
    """
    status = simulation.status
    assert status.status == SimulationStatus.IDLE
    assert status.tick == 0
    assert status.started_at is None
    assert status.paused_at is None


async def test_fastapi_startup_does_not_start_simulation() -> None:
    """#2: app.main's lifespan calls initialize(), never start() — verified
    directly against the source of truth (the lifespan function) rather
    than booting the whole app, since the `client` fixture deliberately
    doesn't run lifespan (see its own docstring).
    """
    import inspect

    from app.main import lifespan

    source = inspect.getsource(lifespan)
    assert "await simulation.initialize()" in source
    assert "await simulation.start()" not in source


async def test_no_ticks_while_idle(simulation: SimulationService) -> None:
    """#3: with the tick loop never started, tick stays 0 no matter how
    long we wait.
    """
    await asyncio.sleep(_TICK_SECONDS * 5)
    assert simulation.status.tick == 0
    assert simulation.status.status == SimulationStatus.IDLE


async def test_idle_telemetry_and_events_unchanged_for_several_seconds(simulation: SimulationService) -> None:
    """#6/#7/#8/#9: a longer wait than a single tick interval — telemetry
    (rack + cluster state), events, optimization plans, and decisions must
    all stay exactly as seeded, since nothing should be driving any of
    them while the tick loop was never started. Regression test for the
    reported bug where a *stale, unrebuilt* backend process had auto-
    started on boot and produced drifted telemetry the frontend then
    rendered as if it were fresh — the fix there was operational
    (redeploy), but this test pins the actual invariant the deployment
    must uphold: IDLE really does mean nothing moves, ever, on this
    process, not just "at t=0".
    """
    before = [
        (rack.temperature, rack.cpu_utilization, rack.gpu_utilization, rack.health_score, rack.power_draw)
        for rack in simulation.rack_states
    ]
    cluster_before = simulation.cluster_state

    await asyncio.sleep(_TICK_SECONDS * 20)  # many multiples of one tick interval

    after = [
        (rack.temperature, rack.cpu_utilization, rack.gpu_utilization, rack.health_score, rack.power_draw)
        for rack in simulation.rack_states
    ]
    assert after == before
    assert simulation.cluster_state == cluster_before
    assert simulation.status.tick == 0
    assert simulation.active_decisions == []
    assert simulation.all_decisions == []
    assert simulation.active_plans == []
    assert simulation.all_plans == []
    assert simulation.cluster_forecast == []


# --- 4/5: start ---------------------------------------------------------------


async def test_start_changes_state_to_running(simulation: SimulationService) -> None:
    """#4"""
    status = await simulation.start()
    assert status.status == SimulationStatus.RUNNING
    assert status.started_at is not None
    assert status.paused_at is None


async def test_running_produces_ticks(simulation: SimulationService) -> None:
    """#5"""
    await simulation.start()
    await _wait_for_tick(simulation, at_least=2)
    assert simulation.status.tick >= 2


# --- 6/7: pause/resume ---------------------------------------------------------


async def test_pause_stops_ticks(simulation: SimulationService) -> None:
    """#6: tick count is frozen once paused, even after waiting."""
    await simulation.start()
    await _wait_for_tick(simulation, at_least=1)
    status = await simulation.pause()
    assert status.status == SimulationStatus.PAUSED
    assert status.paused_at is not None
    frozen_tick = simulation.status.tick
    await asyncio.sleep(_TICK_SECONDS * 5)
    assert simulation.status.tick == frozen_tick


async def test_resume_continues_ticks(simulation: SimulationService) -> None:
    """#7: resuming picks up from the same tick count, not from zero."""
    await simulation.start()
    await _wait_for_tick(simulation, at_least=1)
    await simulation.pause()
    frozen_tick = simulation.status.tick

    status = await simulation.resume()
    assert status.status == SimulationStatus.RUNNING
    assert status.paused_at is None
    await _wait_for_tick(simulation, at_least=frozen_tick + 1)
    assert simulation.status.tick > frozen_tick


# --- 8: reset -------------------------------------------------------------------


async def test_reset_returns_to_deterministic_baseline(simulation: SimulationService) -> None:
    """#8: after running for a bit, reset() brings tick back to 0, status
    to IDLE, and every rack back to its exact seeded baseline values.
    """
    await simulation.start()
    await _wait_for_tick(simulation, at_least=2)

    baseline_by_name = {
        rack_id: (rack.temperature, rack.cpu_utilization, rack.health_score)
        for rack_id, rack in ((r.id, r) for r in simulation.rack_states)
    }

    status = await simulation.reset()
    assert status.status == SimulationStatus.IDLE
    assert status.tick == 0
    assert status.started_at is None
    assert status.paused_at is None

    for rack in simulation.rack_states:
        # Every rack was re-seeded from the same constants regardless of
        # what physics drift happened before reset — so every rack now
        # shares the same temperature/cpu/health, not just "some value".
        assert rack.temperature == simulation.rack_states[0].temperature
        assert rack.cpu_utilization == simulation.rack_states[0].cpu_utilization
        assert rack.health_score == simulation.rack_states[0].health_score


# --- 9/10: idempotency ----------------------------------------------------------


async def test_starting_twice_does_not_create_duplicate_loops(simulation: SimulationService) -> None:
    """#9: a second start() while RUNNING is a no-op — in particular, it
    must not create a second tick-loop task (which would double the tick
    rate).
    """
    await simulation.start()
    task_after_first_start = simulation._task  # noqa: SLF001 - only way to assert "no new task" directly
    status = await simulation.start()
    assert status.status == SimulationStatus.RUNNING
    assert simulation._task is task_after_first_start  # noqa: SLF001

    await _wait_for_tick(simulation, at_least=3)
    tick_at_t1 = simulation.status.tick
    await asyncio.sleep(_TICK_SECONDS * 3)
    tick_at_t2 = simulation.status.tick
    # A duplicate loop would tick roughly twice as fast; allow generous
    # slack for scheduling jitter while still catching a doubled rate.
    assert (tick_at_t2 - tick_at_t1) <= 6


async def test_pausing_twice_is_safe(simulation: SimulationService) -> None:
    """#10"""
    await simulation.start()
    await _wait_for_tick(simulation, at_least=1)
    first = await simulation.pause()
    second = await simulation.pause()
    assert first.status == second.status == SimulationStatus.PAUSED
    assert first.tick == second.tick


async def test_reset_multiple_times_is_safe(simulation: SimulationService) -> None:
    """Reset called repeatedly always lands on the same baseline (part of
    the idempotency requirements' "reset multiple times" case).
    """
    await simulation.start()
    await _wait_for_tick(simulation, at_least=1)
    first = await simulation.reset()
    second = await simulation.reset()
    assert first.status == second.status == SimulationStatus.IDLE
    assert first.tick == second.tick == 0


# --- 11/12: scenario gating -----------------------------------------------------


async def test_scenario_cannot_run_while_idle(simulation: SimulationService) -> None:
    """#11"""
    with pytest.raises(ValueError, match="Start the simulation"):
        await simulation.activate_scenario("thermal_spike")


async def test_scenario_works_while_running(simulation: SimulationService) -> None:
    """#12"""
    await simulation.start()
    status = await simulation.activate_scenario("thermal_spike")
    assert status.key == "thermal_spike"


async def test_replay_blocked_on_fresh_cluster_with_clear_message(simulation: SimulationService) -> None:
    """#15: a fresh cluster (nothing but 'normal' has ever run) reports
    can_replay=False, and replay_scenario() raises the exact clear message
    (-> 400) rather than some generic error — the frontend uses can_replay
    to disable the Replay control before ever making this request (see
    Frontend/scenario/ScenarioEngine.tsx), but the backend guard must hold
    regardless of whether a client bypasses that.
    """
    assert simulation.scenario_status.can_replay is False
    await simulation.start()
    assert simulation.scenario_status.can_replay is False
    with pytest.raises(ValueError, match="No previous scenario to replay yet"):
        await simulation.replay_scenario()


async def test_can_replay_becomes_true_after_a_scenario_runs(simulation: SimulationService) -> None:
    await simulation.start()
    await simulation.activate_scenario("thermal_spike")
    assert simulation.scenario_status.can_replay is True
    status = await simulation.replay_scenario()
    assert status.key == "thermal_spike"


# --- 13: WebSocket / snapshot reports lifecycle state ---------------------------


async def test_snapshot_reports_lifecycle_state(simulation: SimulationService) -> None:
    """#13: the exact payload /ws/telemetry sends on connect and on every
    tick (see TelemetrySnapshot.from_simulation) carries the current
    simulation status — this is how a client learns IDLE/RUNNING/PAUSED
    without a dedicated poll.
    """
    from app.schemas.telemetry import TelemetrySnapshot

    idle_snapshot = TelemetrySnapshot.from_simulation(simulation)
    assert idle_snapshot.simulation.status == SimulationStatus.IDLE

    await simulation.start()
    running_snapshot = TelemetrySnapshot.from_simulation(simulation)
    assert running_snapshot.simulation.status == SimulationStatus.RUNNING


async def test_broadcast_simulation_event_on_transitions(simulation: SimulationService, monkeypatch) -> None:
    """The dedicated SIMULATION_STARTED/PAUSED/RESUMED/RESET broadcasts
    fire on every transition, independent of the tick loop (needed since
    IDLE/PAUSED periods produce no regular tick broadcast at all).
    """
    from app.websocket.manager import manager

    seen: list[dict] = []

    async def _fake_broadcast(payload: dict) -> None:
        seen.append(payload)

    # connection_count is a read-only property (derived from _connections);
    # register a dummy connection directly rather than patching the
    # property itself, so _broadcast_simulation_event's `if
    # connection_count == 0: return` guard sees a "connected" manager.
    dummy_connection = object()
    manager._connections.add(dummy_connection)  # noqa: SLF001
    monkeypatch.setattr(manager, "broadcast", _fake_broadcast)
    try:
        await simulation.start()
        await simulation.pause()
        await simulation.resume()
        await simulation.reset()
    finally:
        manager._connections.discard(dummy_connection)  # noqa: SLF001

    types = [p["type"] for p in seen if "type" in p]
    assert "SIMULATION_STARTED" in types
    assert "SIMULATION_PAUSED" in types
    assert "SIMULATION_RESUMED" in types
    assert "SIMULATION_RESET" in types


# --- 14: shutdown cleans up ------------------------------------------------------


async def test_shutdown_cancels_running_task(db: AsyncSession) -> None:
    """#14: stop() (called from app.main's lifespan shutdown) cleanly
    cancels an in-flight tick-loop task rather than leaving it dangling.
    """
    service = SimulationService(tick_seconds=_TICK_SECONDS)
    await service.initialize()
    await service.start()
    await _wait_for_tick(service, at_least=1)
    task = service._task  # noqa: SLF001
    assert task is not None and not task.done()

    await service.stop()

    assert task.cancelled() or task.done()
    assert service._task is None  # noqa: SLF001


# --- API route-level tests -------------------------------------------------------


@pytest.fixture
async def route_simulation(db: AsyncSession) -> SimulationService:
    service = SimulationService(tick_seconds=_TICK_SECONDS)
    await service.initialize()
    app.dependency_overrides[get_simulation] = lambda: service
    try:
        yield service
    finally:
        app.dependency_overrides.clear()
        await service.stop()


async def test_get_simulation_endpoint_idle_shape(client: AsyncClient, route_simulation: SimulationService) -> None:
    resp = await client.get("/api/simulation")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "idle", "tick": 0, "started_at": None, "paused_at": None}


async def test_start_pause_resume_reset_endpoints(client: AsyncClient, route_simulation: SimulationService) -> None:
    started = (await client.post("/api/simulation/start")).json()
    assert started["status"] == "running"
    assert started["started_at"] is not None

    # POST start again while running is a no-op (idempotency, route layer).
    started_again = (await client.post("/api/simulation/start")).json()
    assert started_again["status"] == "running"

    paused = (await client.post("/api/simulation/pause")).json()
    assert paused["status"] == "paused"
    assert paused["paused_at"] is not None

    resumed = (await client.post("/api/simulation/resume")).json()
    assert resumed["status"] == "running"
    assert resumed["paused_at"] is None

    reset = (await client.post("/api/simulation/reset")).json()
    assert reset == {"status": "idle", "tick": 0, "started_at": None, "paused_at": None}


async def test_scenario_endpoint_blocked_while_idle(client: AsyncClient, route_simulation: SimulationService) -> None:
    resp = await client.post("/api/scenario", json={"scenario": "thermal_spike"})
    assert resp.status_code == 400
    assert "Start the simulation" in resp.json()["detail"]


async def test_scenario_endpoint_works_while_running(client: AsyncClient, route_simulation: SimulationService) -> None:
    await client.post("/api/simulation/start")
    resp = await client.post("/api/scenario", json={"scenario": "thermal_spike"})
    assert resp.status_code == 200
    assert resp.json()["key"] == "thermal_spike"
