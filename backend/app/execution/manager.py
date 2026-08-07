"""ExecutionManager — turns an active execution into per-rack RackDrivers,
ramped smoothly in, held, then ramped back out, exactly like ScenarioManager
does for scenarios (see app.simulation.scenario_manager).

Pure logic: no I/O, no database, no WebSocket. app.execution.service owns
persistence and turns phase transitions into events; SimulationService is
the only place that calls into this every tick and broadcasts.

Every action's effect is expressed purely as bias added to what the
physics engine already computes for gpu_utilization / fan_speed /
cooling_efficiency — never a direct temperature change (see
app.simulation.physics.compute_next_rack_state). A ramp-out (rather than an
indefinite hold) means a remediation's effect naturally fades once it's had
its say — if the underlying cause is still active (e.g. a scenario still
running), the rack will honestly drift back toward however that cause
governs it, rather than staying artificially pinned forever.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.models.enums import ExecutionActionType
from app.simulation.physics import clamp
from app.simulation.state import RackDrivers, combine_drivers

RAMP_SECONDS = 6.0
HOLD_SECONDS = 40.0
TOTAL_LIFECYCLE_SECONDS = 2 * RAMP_SECONDS + HOLD_SECONDS

Phase = Literal["ramping_in", "holding", "ramping_out"]


@dataclass(frozen=True)
class ExecutionEffect:
    """What one action type does to its racks, at full ramp (fraction=1.0)."""

    action_type: ExecutionActionType
    primary_gpu_bias: float = 0.0
    redistribute_gpu_bias: float = 0.0
    fan_bias: float = 0.0
    cooling_bias: float = 0.0


EFFECTS: dict[ExecutionActionType, ExecutionEffect] = {
    ExecutionActionType.WORKLOAD_MIGRATION: ExecutionEffect(
        action_type=ExecutionActionType.WORKLOAD_MIGRATION,
        primary_gpu_bias=-30.0,  # move load off the affected rack
        redistribute_gpu_bias=8.0,  # onto each other healthy rack
    ),
    ExecutionActionType.COOLING_ADJUSTMENT: ExecutionEffect(
        action_type=ExecutionActionType.COOLING_ADJUSTMENT,
        fan_bias=25.0,  # push fans harder than temperature alone would
        cooling_bias=12.0,  # plus a direct cooling-capacity boost
    ),
    ExecutionActionType.JOB_DELAY: ExecutionEffect(
        action_type=ExecutionActionType.JOB_DELAY,
        primary_gpu_bias=-15.0,  # throttle future scheduling, milder than a full migration
    ),
    ExecutionActionType.CLUSTER_REBALANCE: ExecutionEffect(
        action_type=ExecutionActionType.CLUSTER_REBALANCE,
        primary_gpu_bias=-20.0,  # per affected rack
        redistribute_gpu_bias=6.0,  # per other healthy rack — spread thinner than a targeted migration
    ),
}


@dataclass
class _ActiveExecution:
    action_type: ExecutionActionType
    primary_rack_ids: frozenset[uuid.UUID]
    redistribute_rack_ids: frozenset[uuid.UUID]
    started_at: datetime
    phase: Phase = "ramping_in"


@dataclass(frozen=True)
class ExecutionTick:
    """One tick's worth of combined driver contributions, plus which
    executions crossed a phase boundary this tick (for the caller to raise
    lifecycle events from).
    """

    drivers: dict[uuid.UUID, RackDrivers]
    took_effect: list[uuid.UUID]  # ramping_in -> holding (action reached full effect)
    finished: list[uuid.UUID]  # ramping_out -> done (effect fully released)


class ExecutionManager:
    """Tracks every currently-running execution and computes their combined
    per-rack driver contribution each tick.
    """

    def __init__(self) -> None:
        self._active: dict[uuid.UUID, _ActiveExecution] = {}

    def start(
        self,
        execution_id: uuid.UUID,
        action_type: ExecutionActionType,
        primary_rack_ids: set[uuid.UUID],
        redistribute_rack_ids: set[uuid.UUID],
        now: datetime,
    ) -> None:
        self._active[execution_id] = _ActiveExecution(
            action_type=action_type,
            primary_rack_ids=frozenset(primary_rack_ids),
            redistribute_rack_ids=frozenset(redistribute_rack_ids),
            started_at=now,
        )

    def compute_tick(self, now: datetime) -> ExecutionTick:
        contributions: dict[uuid.UUID, list[RackDrivers]] = defaultdict(list)
        took_effect: list[uuid.UUID] = []
        finished: list[uuid.UUID] = []

        for execution_id, active in list(self._active.items()):
            elapsed = (now - active.started_at).total_seconds()
            fraction = self._fraction_for(elapsed)

            if elapsed >= TOTAL_LIFECYCLE_SECONDS:
                finished.append(execution_id)
                del self._active[execution_id]
                continue

            if elapsed < RAMP_SECONDS:
                active.phase = "ramping_in"
            elif elapsed < RAMP_SECONDS + HOLD_SECONDS:
                if active.phase == "ramping_in":
                    took_effect.append(execution_id)
                active.phase = "holding"
            else:
                active.phase = "ramping_out"

            effect = EFFECTS[active.action_type]
            for rack_id in active.primary_rack_ids:
                contributions[rack_id].append(
                    RackDrivers(
                        gpu_bias=effect.primary_gpu_bias * fraction,
                        fan_bias=effect.fan_bias * fraction,
                        cooling_bias=effect.cooling_bias * fraction,
                    )
                )
            for rack_id in active.redistribute_rack_ids:
                contributions[rack_id].append(RackDrivers(gpu_bias=effect.redistribute_gpu_bias * fraction))

        drivers = {rack_id: combine_drivers(*parts) for rack_id, parts in contributions.items()}
        return ExecutionTick(drivers=drivers or {}, took_effect=took_effect, finished=finished)

    @property
    def active_count(self) -> int:
        return len(self._active)

    @staticmethod
    def _fraction_for(elapsed: float) -> float:
        """Linear ramp in over RAMP_SECONDS, full effect through the hold,
        linear ramp back out over the final RAMP_SECONDS — the same
        two-sided shape power_surge already uses conceptually, just fully
        self-contained here instead of relying on an external auto-revert.
        """
        if elapsed < RAMP_SECONDS:
            return clamp(elapsed / RAMP_SECONDS, 0.0, 1.0) if RAMP_SECONDS > 0 else 1.0
        if elapsed < RAMP_SECONDS + HOLD_SECONDS:
            return 1.0
        ramp_out_elapsed = elapsed - RAMP_SECONDS - HOLD_SECONDS
        return clamp(1.0 - ramp_out_elapsed / RAMP_SECONDS, 0.0, 1.0) if RAMP_SECONDS > 0 else 0.0
