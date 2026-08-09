"""Static regression guards over the frontend source, proving the specific
claims this backend's IDLE-by-default lifecycle depends on: nothing in
Frontend/ auto-starts the simulation, generates telemetry locally, or
auto-triggers a replay on mount. See app.simulation.engine.SimulationService
and Frontend/state/TelemetryContext.tsx / Frontend/scenario/ScenarioEngine.tsx.

These are plain source-text assertions, not a JS test run (the frontend has
no configured JS test runner — see package.json) — deliberately narrow and
literal rather than a general-purpose linter, each one pinned to the exact
bug this investigation was about. Skips (rather than fails) if the Frontend/
directory isn't present next to this checkout, mirroring the `db` fixture's
skip-if-unavailable pattern in conftest.py, since the backend's own test
suite must stay runnable standalone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "Frontend"

pytestmark = pytest.mark.skipif(not _FRONTEND_DIR.is_dir(), reason="Frontend/ not present next to this checkout")


def _read(*parts: str) -> str:
    path = _FRONTEND_DIR.joinpath(*parts)
    assert path.is_file(), f"expected frontend file not found: {path}"
    return path.read_text()


def _all_frontend_sources() -> list[Path]:
    return [
        p for p in _FRONTEND_DIR.rglob("*.ts*")
        if "node_modules" not in p.parts and not p.name.endswith(".d.ts")
    ]


def test_no_source_file_calls_start_simulation_outside_scenario_engine() -> None:
    """#4: apiClient.startSimulation must only ever be invoked from the one
    place responsible for it (ScenarioEngine's startSimulation callback,
    itself only reachable from an explicit button click — see
    Frontend/components/SimulationControl.tsx). No mount-time effect, no
    other component, may call it directly.
    """
    callers = []
    for path in _all_frontend_sources():
        if "apiClient.startSimulation" in path.read_text():
            callers.append(path.relative_to(_FRONTEND_DIR).as_posix())
    assert callers == ["scenario/ScenarioEngine.tsx"], (
        f"apiClient.startSimulation referenced from unexpected file(s): {callers}"
    )


def test_telemetry_context_mount_effect_only_connects_the_socket() -> None:
    """#4/#5: the one useEffect that runs on TelemetryProvider mount must
    only call telemetrySocket.connect() (and register listeners) — it must
    never also call any apiClient.*Simulation* method, which would mean
    "the UI opened" silently implied "start the simulation".
    """
    source = _read("state", "TelemetryContext.tsx")
    match = re.search(r"useEffect\(\(\) => \{(.*?)\n  \}, \[\]\);", source, re.DOTALL)
    assert match, "expected TelemetryProvider's mount-time useEffect (empty deps) not found"
    effect_body = match.group(1)
    assert "connect()" in effect_body
    assert "Simulation(" not in effect_body  # no startSimulation/pauseSimulation/... call
    assert "apiClient." not in effect_body


def test_no_setinterval_or_settimeout_writes_telemetry_looking_state() -> None:
    """#5: the only setInterval/setTimeout call sites in the whole frontend
    are the autonomous-mode *scenario* cycler (which calls the real
    backend API, gated on the simulation actually running) and two
    unrelated UI timers (WS reconnect backoff, a hover-flash timeout) —
    none of them locally fabricate temperature/power/gpu/health-shaped
    values. A new setInterval/setTimeout anywhere else in the tree is
    exactly the shape a "fake live telemetry" regression would take.
    """
    allowed = {
        "scenario/ScenarioEngine.tsx",  # autonomous-mode scenario cycling (real API call)
        "lib/wsClient.ts",  # reconnect backoff
        "components/SimulationDock.tsx",  # hover-flash timeout, no state fabrication
    }
    offenders = []
    for path in _all_frontend_sources():
        rel = path.relative_to(_FRONTEND_DIR).as_posix()
        if rel in allowed:
            continue
        text = path.read_text()
        if "setInterval(" in text or "setTimeout(" in text:
            offenders.append(rel)
    assert offenders == [], f"unexpected setInterval/setTimeout usage in: {offenders}"


def test_no_math_random_assigned_to_telemetry_fields() -> None:
    """#5: Math.random() may only appear in the two legitimate, already-
    audited spots (WS reconnect jitter, autonomous-mode scenario pick) —
    never near a temperature/power/gpu/health-shaped value, which is what
    fabricated frontend telemetry would look like.
    """
    allowed = {"lib/wsClient.ts", "scenario/ScenarioEngine.tsx"}
    offenders = []
    for path in _all_frontend_sources():
        rel = path.relative_to(_FRONTEND_DIR).as_posix()
        if rel in allowed:
            continue
        if "Math.random(" in path.read_text():
            offenders.append(rel)
    assert offenders == [], f"unexpected Math.random() usage in: {offenders}"


def test_replay_is_never_called_from_a_mount_effect() -> None:
    """#14: apiClient.replayScenario/triggerReplay must only be reachable
    from an explicit user action (button click / command palette
    selection), never a bare `useEffect(() => { ... }, [])` on mount.
    """
    for rel in ("App.tsx", "scenario/ScenarioEngine.tsx", "components/SimulationDock.tsx"):
        source = _read(*rel.split("/"))
        for match in re.finditer(r"useEffect\(\(\) => \{(.*?)\n  \}, \[\]\);", source, re.DOTALL):
            body = match.group(1)
            assert "triggerReplay" not in body and "replayScenario" not in body, (
                f"{rel}: a mount-time (empty-deps) useEffect calls replay — found:\n{body}"
            )


def test_replay_guarded_by_can_replay_before_the_network_call() -> None:
    """#15: triggerReplay must check canReplay and short-circuit with a
    local, request-free message rather than always making the network call
    — this is what turns "no replay history" into a controlled state
    instead of an inevitable 400 on a fresh cluster.
    """
    source = _read("scenario", "ScenarioEngine.tsx")
    trigger_replay = source[source.index("const triggerReplay = useCallback"):]
    trigger_replay = trigger_replay[: trigger_replay.index("\n  }, [canReplay]);") + 1]
    assert "if (!canReplay)" in trigger_replay
    assert trigger_replay.index("if (!canReplay)") < trigger_replay.index("apiClient")


def test_scenario_status_type_carries_can_replay() -> None:
    """The frontend's ScenarioStatus type must mirror the backend schema's
    can_replay field (app.schemas.scenario.ScenarioStatus) — a silent drift
    here would make canReplay always undefined/falsy at runtime without any
    type error.
    """
    source = _read("lib", "types.ts")
    scenario_status_block = source[source.index("export type ScenarioStatus = {"):]
    scenario_status_block = scenario_status_block[: scenario_status_block.index("};") + 2]
    assert "can_replay: boolean" in scenario_status_block
