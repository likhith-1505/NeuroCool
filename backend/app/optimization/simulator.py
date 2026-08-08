"""Isolated what-if simulation — projects a candidate action's effect using
the exact same physics engine the real simulation runs (see
app.simulation.physics), but entirely on private copies of RackState/
RackInternals. Nothing here ever touches SimulationService's live
`self._racks`/`self._internals` dicts or the database — this is the
"isolated planning context" the objective asks for.

Deterministic on purpose: a fixed seed means the same candidate on the
same telemetry always projects to the same outcome, which is what makes
candidate scores *consistent* (see app.optimization.scoring and the
objective's "Scores are consistent" verification point) rather than a
fresh coin-flip every tick. This mirrors app.forecasting.trend's own
"deterministic, not random" principle, applied to a forward simulation
instead of a backward-looking fit.

Simplifying assumption, stated plainly rather than left implicit: a
candidate is projected *ceteris paribus* — its RackDrivers bias applied at
full strength from tick one, with no other concurrent scenario/execution
driver layered in. This is standard "what if we pulled this lever, all
else held equal" planning, not a full counterfactual replay of whatever
else might be happening; app.execution's real ramp-in is much shorter
(RAMP_SECONDS) than the planning horizon below, so the approximation cost
is small. NO_ACTION is the one candidate that intentionally does NOT use
this ceteris-paribus assumption — see app.optimization.planner, which
projects it from the rack's own forecast instead, precisely so "if we do
nothing" still reflects an ongoing scenario/trend rather than a frozen
snapshot.
"""

from __future__ import annotations

import random

from app.simulation.physics import compute_next_rack_state
from app.simulation.state import RackDrivers, RackInternals, RackState

# ~20 physics ticks (about 20 seconds at the default 1s tick) is enough for
# the fastest-reacting values (power, fans) to fully settle and the
# slowest (temperature, thermal mass) to show a clear, meaningful trend —
# without spending so many ticks that scoring six candidates for every
# triggered rack becomes expensive on every tick.
PLANNING_HORIZON_TICKS = 20

# Fixed, not seeded from wall-clock/real entropy — see module docstring.
_SIMULATION_SEED = 1_337


def project_rack(rack: RackState, drivers: RackDrivers, ticks: int = PLANNING_HORIZON_TICKS) -> list[RackState]:
    """Advance a private copy of `rack` forward `ticks` physics steps under
    a constant driver. Returns every intermediate state (oldest first, so
    `result[-1]` is the final projection) — the full trajectory, not just
    the endpoint, is what lets scoring derive estimated_recovery_seconds
    (the first tick a safe state is reached) without re-simulating.
    """
    rng = random.Random(_SIMULATION_SEED)
    internals = RackInternals(gpu_baseline=rack.gpu_utilization, jobs_baseline=float(rack.running_jobs))
    state = rack
    trajectory: list[RackState] = []
    for _ in range(ticks):
        state, internals = compute_next_rack_state(state, internals, rng, drivers)
        trajectory.append(state)
    return trajectory
