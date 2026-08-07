"""Pure math for the telemetry simulation.

No I/O and no hidden randomness — the caller passes in a `random.Random`
instance explicitly, which keeps this module trivial to reason about (and
seedable/deterministic in tests) even though the simulation looks organic.

The tick step (`compute_next_rack_state`) implements one causal chain per
rack, in this order:

    workload (gpu/cpu) -> power draw -> heat
                                            |
                                            v
    fan speed <- temperature -> cooling efficiency (negative feedback)
         |
         v
    temperature (eased toward its heat/cooling-derived target)
         |
         v
    health_score -> prediction_state -> status

Every *visible* value is eased toward a target (see `ease`) rather than
replaced outright, so nothing jumps between ticks. Only small, bounded
random walks (see `wander`) are used, and only to move slow-changing
*targets* — never the values the API/UI read directly.
"""

from __future__ import annotations

import random
import uuid
from statistics import fmean

from app.models.enums import RackStatus
from app.simulation.state import NO_DRIVERS, ClusterState, RackDrivers, RackInternals, RackState

# --- Tunable constants -------------------------------------------------
# Kept in one place so the simulation's "feel" can be adjusted without
# hunting through the logic below.

AMBIENT_TEMPERATURE_C = 21.0
COMFORTABLE_TEMPERATURE_C = 62.0

IDLE_POWER_KW = 3.2
GPU_POWER_COEFFICIENT_KW = 9.5  # extra kW at 100% GPU utilization
CPU_POWER_COEFFICIENT_KW = 2.8  # extra kW at 100% CPU utilization
HEAT_PER_KW = 2.8  # °C of target temperature rise per kW of power, before cooling

BASE_COOLING_EFFICIENCY = 55.0  # cooling_efficiency (%) at idle fan speed
COOLING_GAIN = 0.42  # extra cooling_efficiency per fan_speed point above idle
FAN_IDLE_SPEED = 28.0
FAN_GAIN = 2.1  # fan_speed points added per °C above comfortable temperature

# First-order lag rates: how much of the gap to the target is closed each
# tick. Smaller = slower/smoother. Temperature is deliberately the slowest
# (thermal mass), power the fastest (near-instant electrically).
GPU_EASE_RATE = 0.18
CPU_EASE_RATE = 0.22
POWER_EASE_RATE = 0.30
FAN_EASE_RATE = 0.12
COOLING_EASE_RATE = 0.10
TEMPERATURE_EASE_RATE = 0.08

GPU_BASELINE_WANDER_STEP = 0.6
GPU_JITTER_STEP = 1.8
JOBS_BASELINE_WANDER_STEP = 0.4
JOB_CHANGE_PROBABILITY = 0.18  # most ticks, the job count doesn't change at all

STATUS_BY_PREDICTION: dict[str, RackStatus] = {
    "stable": RackStatus.HEALTHY,
    "watch": RackStatus.WARNING,
    "at_risk": RackStatus.CRITICAL,
}
# RackStatus.OFFLINE is intentionally never produced by the baseline
# simulation — it is reserved for future scenario-driven logic.


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def ease(current: float, target: float, rate: float) -> float:
    """Move `current` a fraction `rate` of the way toward `target`.

    A first-order lag (exponential smoothing) — the standard way to make a
    value change continuously instead of snapping, while still tracking a
    moving target responsively.
    """
    return current + (target - current) * rate


def wander(value: float, step: float, lower: float, upper: float, rng: random.Random) -> float:
    """Nudge `value` by a small bounded random amount (a mean-free random walk).

    Used only for slow-moving *targets* (e.g. a rack's baseline workload) —
    never for values the API/UI read directly. Bounding the step size, not
    just the result, is what prevents any single tick from jumping.
    """
    return clamp(value + rng.uniform(-step, step), lower, upper)


def compute_health_score(temperature: float, cooling_efficiency: float, gpu_utilization: float) -> float:
    """Health is fully derived from current physical state, not evolved
    independently — it stays smooth because its inputs are smooth.
    """
    thermal_penalty = clamp((temperature - COMFORTABLE_TEMPERATURE_C) * 2.6, 0.0, 70.0)
    cooling_bonus = clamp((cooling_efficiency - BASE_COOLING_EFFICIENCY) * 0.25, -10.0, 15.0)
    load_penalty = clamp((gpu_utilization - 85.0) * 0.3, 0.0, 12.0)
    return clamp(100.0 - thermal_penalty + cooling_bonus - load_penalty, 1.0, 100.0)


def compute_prediction_state(health_score: float, previous_state: str) -> str:
    """Three-tier classification with hysteresis.

    The exit threshold from a worse state is always higher than the entry
    threshold into it, so a health score hovering near a boundary doesn't
    flap the state back and forth every tick.
    """
    if previous_state == "at_risk":
        if health_score >= 55.0:
            return "watch" if health_score < 78.0 else "stable"
        return "at_risk"
    if previous_state == "watch":
        if health_score >= 78.0:
            return "stable"
        return "at_risk" if health_score < 45.0 else "watch"
    # previous_state == "stable" (or unrecognized — fail safe into "stable" rules)
    if health_score < 40.0:
        return "at_risk"
    return "watch" if health_score < 68.0 else "stable"


def compute_next_jobs(previous_jobs: int, jobs_baseline: float, rng: random.Random) -> tuple[int, float]:
    """Running job count changes as discrete +1/-1 steps, rarely — a count
    of jobs shouldn't visibly wander every second the way a percentage can.
    """
    next_baseline = wander(jobs_baseline, JOBS_BASELINE_WANDER_STEP, 2.0, 28.0, rng)
    running_jobs = previous_jobs
    if rng.random() < JOB_CHANGE_PROBABILITY:
        delta = 1 if previous_jobs < next_baseline else -1
        running_jobs = int(clamp(previous_jobs + delta, 0, 32))
    return running_jobs, next_baseline


def compute_next_rack_state(
    previous: RackState,
    internals: RackInternals,
    rng: random.Random,
    drivers: RackDrivers = NO_DRIVERS,
) -> tuple[RackState, RackInternals]:
    """Advance one rack by a single simulation tick. See module docstring
    for the causal chain this implements.

    `drivers` is the ScenarioManager's only way to influence this: it can
    bias the workload/power targets and cap the cooling target, but every
    value is still *eased* toward its target exactly as before — the
    scenario changes the destination, never the pace of travel, which is
    what keeps transitions smooth regardless of which scenario is active.
    """
    # 1) Workload: only the *target* utilization is randomized, by a small
    #    bounded step, so gpu_utilization itself moves smoothly toward it.
    #    A scenario's gpu_bias shifts that target directly (e.g. a thermal
    #    spike's chosen rack gets a large positive bias).
    gpu_baseline = wander(internals.gpu_baseline, GPU_BASELINE_WANDER_STEP, 12.0, 92.0, rng)
    gpu_target = clamp(
        gpu_baseline + rng.uniform(-GPU_JITTER_STEP, GPU_JITTER_STEP) + drivers.gpu_bias, 5.0, 100.0
    )
    gpu_utilization = ease(previous.gpu_utilization, gpu_target, GPU_EASE_RATE)

    # 2) CPU load tracks GPU load loosely (scheduling/orchestration
    #    overhead scales with job activity) plus a small baseline of its own.
    cpu_target = clamp(gpu_utilization * 0.55 + 12.0, 5.0, 100.0)
    cpu_utilization = ease(previous.cpu_utilization, cpu_target, CPU_EASE_RATE)

    # 3) Power draw is a direct physical function of compute load, plus a
    #    scenario's own power_bias_kw (e.g. a power surge that isn't
    #    driven by extra compute — a PSU/voltage event, not a GPU one).
    power_target = (
        IDLE_POWER_KW
        + (gpu_utilization / 100.0) * GPU_POWER_COEFFICIENT_KW
        + (cpu_utilization / 100.0) * CPU_POWER_COEFFICIENT_KW
        + drivers.power_bias_kw
    )
    power_draw = ease(previous.power_draw, power_target, POWER_EASE_RATE)

    # 4) Fans respond to *current* temperature — this is what creates the
    #    negative-feedback cooling loop instead of an open one.
    fan_target = clamp(
        FAN_IDLE_SPEED + (previous.temperature - COMFORTABLE_TEMPERATURE_C) * FAN_GAIN,
        FAN_IDLE_SPEED,
        100.0,
    )
    fan_speed = ease(previous.fan_speed, fan_target, FAN_EASE_RATE)

    # 5) Cooling efficiency responds to fan speed, with its own lag —
    #    cooling capacity doesn't appear the instant fans spin up. A
    #    cooling-failure scenario caps this target directly: fan_target
    #    above is untouched (fans still visibly saturate trying to help),
    #    but the efficiency they'd normally produce is capped regardless —
    #    exactly modeling "the fans work, the cooling doesn't."
    cooling_target = clamp(
        BASE_COOLING_EFFICIENCY + (fan_speed - FAN_IDLE_SPEED) * COOLING_GAIN,
        30.0,
        99.0,
    )
    if drivers.cooling_ceiling is not None:
        cooling_target = min(cooling_target, drivers.cooling_ceiling)
    cooling_efficiency = ease(previous.cooling_efficiency, cooling_target, COOLING_EASE_RATE)

    # 6) Temperature rises with power draw and is tempered by cooling
    #    efficiency — weaker cooling lets the same power raise temperature
    #    further. It is the slowest-moving value (thermal mass has to
    #    catch up), hence the smallest ease rate.
    heat_load = power_draw * HEAT_PER_KW
    temperature_target = AMBIENT_TEMPERATURE_C + heat_load * (100.0 / cooling_efficiency)
    temperature = clamp(ease(previous.temperature, temperature_target, TEMPERATURE_EASE_RATE), 35.0, 99.0)

    # 7) Health is fully derived from the physical state above.
    health_score = compute_health_score(temperature, cooling_efficiency, gpu_utilization)
    prediction_state = compute_prediction_state(health_score, previous.prediction_state)
    status = STATUS_BY_PREDICTION[prediction_state]

    running_jobs, jobs_baseline = compute_next_jobs(previous.running_jobs, internals.jobs_baseline, rng)

    next_state = RackState(
        id=previous.id,
        name=previous.name,
        temperature=round(temperature, 2),
        gpu_utilization=round(gpu_utilization, 2),
        cpu_utilization=round(cpu_utilization, 2),
        power_draw=round(power_draw, 2),
        cooling_efficiency=round(cooling_efficiency, 2),
        fan_speed=round(fan_speed, 2),
        health_score=round(health_score, 2),
        prediction_state=prediction_state,
        running_jobs=running_jobs,
        status=status,
    )
    next_internals = RackInternals(gpu_baseline=gpu_baseline, jobs_baseline=jobs_baseline)
    return next_state, next_internals


def compute_cluster_state(cluster_id: uuid.UUID, name: str, racks: list[RackState]) -> ClusterState:
    """Cluster-wide numbers are always derived from current rack telemetry,
    never stored/evolved independently.
    """
    if not racks:
        return ClusterState(
            id=cluster_id,
            name=name,
            overall_health=100.0,
            average_temperature=AMBIENT_TEMPERATURE_C,
            total_power=0.0,
            cooling_efficiency=100.0,
            energy_savings=0.0,
            prediction_confidence=100.0,
        )

    average_temperature = fmean(rack.temperature for rack in racks)
    overall_health = fmean(rack.health_score for rack in racks)
    total_power = sum(rack.power_draw for rack in racks)
    cooling_efficiency = fmean(rack.cooling_efficiency for rack in racks)

    # Energy savings: how much better than a "cooling never adapts, every
    # rack pinned at peak draw" baseline the cluster is currently running —
    # an honest, derived proxy rather than a fabricated number.
    worst_case_power = len(racks) * (IDLE_POWER_KW + GPU_POWER_COEFFICIENT_KW + CPU_POWER_COEFFICIENT_KW)
    energy_savings = clamp((1.0 - total_power / worst_case_power) * 100.0, 0.0, 45.0)

    # Prediction confidence: steadier telemetry (clearly healthy or clearly
    # critical racks, well-cooled) is easier to predict confidently than a
    # cluster hovering at its thresholds.
    prediction_confidence = clamp(overall_health * 0.6 + cooling_efficiency * 0.4, 30.0, 99.0)

    return ClusterState(
        id=cluster_id,
        name=name,
        overall_health=round(overall_health, 2),
        average_temperature=round(average_temperature, 2),
        total_power=round(total_power, 2),
        cooling_efficiency=round(cooling_efficiency, 2),
        energy_savings=round(energy_savings, 2),
        prediction_confidence=round(prediction_confidence, 2),
    )
