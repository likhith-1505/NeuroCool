"""ScenarioManager — the orchestration layer that turns named scenarios
into per-rack physics inputs.

This module deliberately contains no I/O (no database, no WebSocket): it
only tracks which scenario is active and computes the RackDrivers each
rack's physics tick should apply that tick. SimulationService is
responsible for calling into this every tick and for turning scenario
transitions into persisted/broadcast events — the same separation of
concerns already used for telemetry events (see app.services.event_service).

Smoothness note: a scenario's bias is *ramped in* linearly over its
`ramp_seconds` (computed from elapsed wall-clock time since activation, not
a counter that needs advancing), and the physics engine still eases every
value toward whatever target that bias produces. Two independent layers of
smoothing — the ramp, and the physics engine's own easing — is what makes
"Rapidly increase workload" (a short ramp) and "Increase workload
gradually" (a long ramp) both feel continuous rather than instant, without
ScenarioManager needing to know anything about how temperature is computed.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.simulation.physics import clamp
from app.simulation.state import RackDrivers, RackState, ring_neighbors
from app.utils.time import utcnow

TransitionState = Literal["transitioning", "steady"]
ScenarioScope = Literal["cluster", "single_rack"]


@dataclass(frozen=True)
class ScenarioDefinition:
    """A built-in scenario's static profile — what it targets and how fast."""

    key: str
    name: str
    description: str
    scope: ScenarioScope
    ramp_seconds: float
    duration_seconds: float | None  # None = persists until changed; set = auto-reverts
    gpu_bias: float = 0.0
    neighbor_gpu_bias: float = 0.0
    cooling_ceiling: float | None = None
    power_bias_kw: float = 0.0


SCENARIOS: dict[str, ScenarioDefinition] = {
    d.key: d
    for d in [
        ScenarioDefinition(
            key="normal",
            name="Normal",
            description="Healthy cluster: balanced GPU usage, normal cooling, stable temperatures.",
            scope="cluster",
            ramp_seconds=6.0,
            duration_seconds=None,
        ),
        ScenarioDefinition(
            key="training_burst",
            name="Training Burst",
            description=(
                "Cluster-wide workload rises gradually — GPU and power climb together while "
                "cooling compensates as heat builds."
            ),
            scope="cluster",
            ramp_seconds=25.0,
            duration_seconds=None,
            gpu_bias=22.0,
        ),
        ScenarioDefinition(
            key="thermal_spike",
            name="Thermal Spike",
            description=(
                "One rack's workload rises rapidly, driving up its temperature and degrading health; "
                "neighboring racks feel a small thermal influence."
            ),
            scope="single_rack",
            ramp_seconds=8.0,
            duration_seconds=None,
            gpu_bias=45.0,
            neighbor_gpu_bias=10.0,
            # gpu_bias alone saturates gpu_utilization near 100% quickly,
            # after which more of it barely raises heat further — so without
            # this, cooling's negative feedback loop keeps equilibrium
            # temperature hovering just under the workload-migration
            # decision rule's 82°C threshold more often than not. A modest
            # extra power draw (PSU/VRM losses under sustained peak load,
            # not modeled by GPU utilization alone) reliably pushes it over.
            power_bias_kw=3.0,
        ),
        ScenarioDefinition(
            key="cooling_failure",
            name="Cooling Failure",
            description=(
                "One rack's cooling efficiency is capped low. Fans saturate trying to compensate, but "
                "temperature keeps climbing anyway."
            ),
            scope="single_rack",
            ramp_seconds=10.0,
            duration_seconds=None,
            cooling_ceiling=32.0,
        ),
        ScenarioDefinition(
            key="power_surge",
            name="Power Surge",
            description=(
                "A short-lived spike in one rack's power draw. The system stabilizes automatically once "
                "the surge subsides."
            ),
            scope="single_rack",
            ramp_seconds=2.0,
            duration_seconds=12.0,
            power_bias_kw=9.0,
        ),
    ]
}


@dataclass
class _ActiveScenario:
    definition: ScenarioDefinition
    target_rack_id: uuid.UUID | None
    neighbor_rack_ids: frozenset[uuid.UUID]
    activated_at: datetime
    transition_state: TransitionState = "transitioning"


class ScenarioManager:
    """Tracks the single active scenario and computes per-rack drivers.

    Only one scenario is ever active at a time — activating a new one
    simply replaces `_active` outright, there is no queue or stacking.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self._active = _ActiveScenario(
            definition=SCENARIOS["normal"],
            target_rack_id=None,
            neighbor_rack_ids=frozenset(),
            activated_at=utcnow(),
            transition_state="steady",  # boots already at rest; nothing to ramp from
        )
        self._last_non_normal_key: str | None = None

    # --- introspection -----------------------------------------------------

    @staticmethod
    def available_scenarios() -> list[ScenarioDefinition]:
        return list(SCENARIOS.values())

    @property
    def active_key(self) -> str:
        return self._active.definition.key

    @property
    def transition_state(self) -> TransitionState:
        return self._active.transition_state

    @property
    def target_rack_id(self) -> uuid.UUID | None:
        return self._active.target_rack_id

    @property
    def activated_at(self) -> datetime:
        return self._active.activated_at

    # --- control -------------------------------------------------------------

    def activate(self, key: str, racks: list[RackState]) -> ScenarioDefinition:
        """Switch to a new scenario. Raises ValueError for an unknown key."""
        definition = SCENARIOS.get(key)
        if definition is None:
            available = ", ".join(sorted(SCENARIOS))
            raise ValueError(f"Unknown scenario '{key}'. Available scenarios: {available}.")

        target_rack_id, neighbor_ids = self._select_racks(definition, racks)

        self._active = _ActiveScenario(
            definition=definition,
            target_rack_id=target_rack_id,
            neighbor_rack_ids=neighbor_ids,
            activated_at=utcnow(),
            transition_state="transitioning",
        )
        if definition.key != "normal":
            self._last_non_normal_key = definition.key
        return definition

    def reset(self, racks: list[RackState]) -> ScenarioDefinition:
        """Return to the normal profile. Replay history is kept intact —
        resetting doesn't forget what was last run.
        """
        return self.activate("normal", racks)

    def replay(self, racks: list[RackState]) -> ScenarioDefinition:
        """Re-activate the most recently active non-normal scenario."""
        if self._last_non_normal_key is None:
            raise ValueError("No previous scenario to replay yet.")
        return self.activate(self._last_non_normal_key, racks)

    def maybe_auto_revert(self, now: datetime) -> ScenarioDefinition | None:
        """If the active scenario has a fixed duration and it has elapsed,
        fall back to normal automatically (e.g. a power surge "stabilizes
        automatically"). Returns the definition that just completed, so the
        caller can raise a "Scenario Completed" event — or None if nothing
        changed this tick.
        """
        duration = self._active.definition.duration_seconds
        if duration is None:
            return None
        elapsed = (now - self._active.activated_at).total_seconds()
        if elapsed < duration:
            return None

        completed = self._active.definition
        self._active = _ActiveScenario(
            definition=SCENARIOS["normal"],
            target_rack_id=None,
            neighbor_rack_ids=frozenset(),
            activated_at=now,
            transition_state="transitioning",
        )
        return completed

    # --- per-tick computation ------------------------------------------------

    def compute_drivers(self, racks: list[RackState], now: datetime) -> dict[uuid.UUID, RackDrivers]:
        """Return this tick's per-rack bias, linearly ramped since activation."""
        definition = self._active.definition
        elapsed = (now - self._active.activated_at).total_seconds()
        ramp = 1.0 if definition.ramp_seconds <= 0 else clamp(elapsed / definition.ramp_seconds, 0.0, 1.0)

        if self._active.transition_state == "transitioning" and ramp >= 1.0:
            self._active.transition_state = "steady"

        if definition.key == "normal" or ramp <= 0.0:
            return {}

        if definition.scope == "cluster":
            return {
                rack.id: RackDrivers(
                    gpu_bias=definition.gpu_bias * ramp,
                    cooling_ceiling=definition.cooling_ceiling,
                    power_bias_kw=definition.power_bias_kw * ramp,
                )
                for rack in racks
            }

        # single_rack scope
        drivers: dict[uuid.UUID, RackDrivers] = {}
        target_id = self._active.target_rack_id
        if target_id is not None:
            drivers[target_id] = RackDrivers(
                gpu_bias=definition.gpu_bias * ramp,
                cooling_ceiling=definition.cooling_ceiling,
                power_bias_kw=definition.power_bias_kw * ramp,
            )
        for neighbor_id in self._active.neighbor_rack_ids:
            drivers[neighbor_id] = RackDrivers(gpu_bias=definition.neighbor_gpu_bias * ramp)
        return drivers

    # --- rack selection --------------------------------------------------

    def _select_racks(
        self, definition: ScenarioDefinition, racks: list[RackState]
    ) -> tuple[uuid.UUID | None, frozenset[uuid.UUID]]:
        if definition.scope != "single_rack" or not racks:
            return None, frozenset()

        target = self._rng.choice(racks)
        return target.id, ring_neighbors(target.id, racks)
