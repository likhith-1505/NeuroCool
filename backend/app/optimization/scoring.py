"""Turns a candidate's simulated projection into a CandidateScore.

Every dimension is *derived* — from the simulated projection, from the
candidate's own driver magnitude (app.execution.manager.EFFECTS, reused
rather than a second set of made-up per-action numbers), or from
app.forecasting.risk's existing thermal risk model — never a fixed literal
assigned per action type. See the objective's "Do not hardcode scores.
Estimate them from the existing simulation and forecasting systems."
"""

from __future__ import annotations

from statistics import fmean

from app.forecasting.risk import compute_risk
from app.optimization.base import CandidateScore
from app.simulation.physics import COMFORTABLE_TEMPERATURE_C, clamp
from app.simulation.state import ClusterState, RackState

# EFFECTS entries carry gpu/fan/cooling bias magnitudes in wildly different
# native units (percent, °C-equivalent, kW) — these weights convert "how
# hard is this driver pushing" into one comparable 0-100-ish cost/
# disruption scale, not a per-action lookup table.
EXECUTION_COST_GPU_WEIGHT = 0.9
EXECUTION_COST_FAN_WEIGHT = 0.6
EXECUTION_COST_COOLING_WEIGHT = 0.8
EXECUTION_COST_PER_RACK = 4.0

DISRUPTION_PER_PRIMARY_RACK = 14.0
DISRUPTION_PER_REDISTRIBUTE_RACK = 5.0
DISRUPTION_GPU_WEIGHT = 0.6

# A repeat of the same remediation recently (see
# app.optimization.planner._recent_attempt_count) means it didn't fully
# resolve things last time — each repeat chips away at confidence rather
# than assuming this attempt will suddenly work as well as a fresh one.
CONFIDENCE_PENALTY_PER_RECENT_REPEAT = 8.0
MIN_CONFIDENCE = 15.0
MAX_CONFIDENCE = 95.0

# Composite weights for overall_score — tuned so a candidate that
# meaningfully reduces risk/temperature clearly outranks one that doesn't,
# while cost/disruption only matter as tie-breakers between similarly
# effective options (never enough to make an ineffective-but-cheap
# candidate outrank an effective one during a real trigger).
WEIGHT_RISK_REDUCTION = 0.45
WEIGHT_TEMPERATURE_REDUCTION = 1.8
WEIGHT_COOLING_IMPROVEMENT = 0.35
WEIGHT_EXECUTION_COST = 0.12
WEIGHT_OPERATIONAL_DISRUPTION = 0.12
WEIGHT_POWER_INCREASE_PENALTY = 0.8
WEIGHT_CONFIDENCE = 0.15
WEIGHT_RECOVERY_TIME_PENALTY = 0.02


def execution_cost(gpu_bias: float, fan_bias: float, cooling_bias: float, affected_rack_count: int) -> float:
    """How large an intervention this is, purely from the magnitude of the
    driver bias it applies (see app.execution.manager.EFFECTS) — a bigger
    lever pulled is a costlier action to carry out, regardless of which
    specific action type it is.
    """
    raw = (
        abs(gpu_bias) * EXECUTION_COST_GPU_WEIGHT
        + abs(fan_bias) * EXECUTION_COST_FAN_WEIGHT
        + abs(cooling_bias) * EXECUTION_COST_COOLING_WEIGHT
        + affected_rack_count * EXECUTION_COST_PER_RACK
    )
    return round(clamp(raw, 0.0, 100.0), 1)


def operational_disruption(gpu_bias: float, primary_count: int, redistribute_count: int) -> float:
    """How many racks/jobs this action visibly touches — migrating or
    rebalancing workload onto other racks is more disruptive than a
    single-rack cooling/fan adjustment that touches nothing else.
    """
    raw = (
        primary_count * DISRUPTION_PER_PRIMARY_RACK
        + redistribute_count * DISRUPTION_PER_REDISTRIBUTE_RACK
        + abs(gpu_bias) * DISRUPTION_GPU_WEIGHT
    )
    return round(clamp(raw, 0.0, 100.0), 1)


def risk_reduction(
    current_racks: list[RackState],
    projected_racks: list[RackState],
    scenario_active: bool,
) -> float:
    """Mean thermal risk (app.forecasting.risk.compute_risk, the same
    model ForecastService already uses) before minus after, across every
    primary rack this candidate affects. Positive = safer, negative = a
    candidate that would make things worse (e.g. NO_ACTION while a
    scenario keeps driving temperature up).
    """
    if not current_racks or not projected_racks:
        return 0.0

    def _risk(rack: RackState) -> float:
        return compute_risk(
            predicted_temperature=rack.temperature,
            temperature_slope_per_sec=0.0,
            gpu_slope_per_sec=0.0,
            power_slope_per_sec=0.0,
            predicted_cooling=rack.cooling_efficiency,
            scenario_active=scenario_active,
            neighbor_trend_hint=0.0,
        )

    current_risk = fmean(_risk(r) for r in current_racks)
    projected_risk = fmean(_risk(r) for r in projected_racks)
    return round(clamp(current_risk - projected_risk, -100.0, 100.0), 1)


def estimated_recovery_seconds(
    trajectory: list[RackState], tick_seconds: float, horizon_seconds: float
) -> float:
    """First tick in the projected trajectory where every rack is back
    under the comfortable baseline — i.e. how long this candidate takes to
    actually resolve the problem, not just how much it eventually helps.
    Uncapped-horizon candidates (never recovers within the simulated
    window) report the full horizon as a floor, an honest "at least this
    long" rather than a fabricated exact number.
    """
    for index, rack in enumerate(trajectory):
        if rack.temperature <= COMFORTABLE_TEMPERATURE_C:
            return round((index + 1) * tick_seconds, 1)
    return round(horizon_seconds, 1)


def confidence(base_confidence: float, recent_repeat_count: int) -> float:
    penalty = recent_repeat_count * CONFIDENCE_PENALTY_PER_RECENT_REPEAT
    return round(clamp(base_confidence - penalty, MIN_CONFIDENCE, MAX_CONFIDENCE), 1)


def score_candidate(
    *,
    temperature_reduction_c: float,
    power_impact_kw: float,
    cooling_improvement_pct: float,
    cost: float,
    disruption: float,
    risk_delta: float,
    recovery_seconds: float,
    candidate_confidence: float,
) -> CandidateScore:
    power_penalty = max(0.0, power_impact_kw) * WEIGHT_POWER_INCREASE_PENALTY
    overall = (
        risk_delta * WEIGHT_RISK_REDUCTION
        + temperature_reduction_c * WEIGHT_TEMPERATURE_REDUCTION
        + cooling_improvement_pct * WEIGHT_COOLING_IMPROVEMENT
        + candidate_confidence * WEIGHT_CONFIDENCE
        - cost * WEIGHT_EXECUTION_COST
        - disruption * WEIGHT_OPERATIONAL_DISRUPTION
        - power_penalty
        - recovery_seconds * WEIGHT_RECOVERY_TIME_PENALTY
    )
    return CandidateScore(
        temperature_reduction_c=round(temperature_reduction_c, 2),
        power_impact_kw=round(power_impact_kw, 2),
        cooling_improvement_pct=round(cooling_improvement_pct, 2),
        execution_cost=round(cost, 1),
        operational_disruption=round(disruption, 1),
        risk_reduction=round(risk_delta, 1),
        estimated_recovery_seconds=round(recovery_seconds, 1),
        confidence=round(candidate_confidence, 1),
        overall_score=round(clamp(overall, 0.0, 100.0), 1),
    )


def cluster_power_delta(current: ClusterState, projected: ClusterState) -> float:
    return round(projected.total_power - current.total_power, 2)
