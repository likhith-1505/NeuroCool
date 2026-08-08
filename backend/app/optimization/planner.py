"""SimulationOptimizer — the initial OptimizationEngine implementation.

For every rack whose telemetry or forecast crosses a planning trigger (see
_find_trigger below), generates every candidate action the objective lists
(Workload Migration, Cooling Increase, Cluster Rebalance, Delay New Jobs,
Fan Override, No Action), projects each one forward through the real
physics engine in an isolated context (app.optimization.simulator), scores
the outcome (app.optimization.scoring), and ranks them — the winner becomes
the plan's recommendation, the rest become alternatives with a derived
rejection reason each.

Trigger thresholds here are deliberately its own, independent set of
constants rather than imports from app.ai.rules — the same "conceptually
parallel, not literally shared" choice app.forecasting already makes
(compare ForecastService.EVENT_WARMUP_TICKS to RuleBasedDecisionEngine.
WARMUP_EVALUATIONS): the Optimization Engine sits *upstream* of the
Decision Engine and must not depend on it, so a future RL-based
optimization engine could be swapped in without ever touching app.ai.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

from app.execution.manager import EFFECTS
from app.models.enums import ExecutionActionType, RackStatus
from app.optimization import scoring
from app.optimization.base import OptimizationCandidate, OptimizationContext, OptimizationPlan
from app.optimization.simulator import PLANNING_HORIZON_TICKS, project_rack
from app.simulation.physics import COMFORTABLE_TEMPERATURE_C, clamp, compute_cluster_state
from app.simulation.state import RackDrivers, RackState, ring_neighbors

if TYPE_CHECKING:
    from app.forecasting.base import RackPrediction
    from app.models.event import Event

# --- Trigger thresholds ----------------------------------------------------

TEMPERATURE_TRIGGER_C = 82.0
GPU_TRIGGER_PCT = 90.0
COOLING_TRIGGER_PCT = 45.0
REBALANCE_TEMPERATURE_TRIGGER_C = 78.0

PROACTIVE_HORIZON_SECONDS = 300
PROACTIVE_TEMPERATURE_TRIGGER_C = 85.0

POWER_SPIKE_HORIZON_SECONDS = 120
POWER_SPIKE_TRIGGER_KW = 4.0

# Floor for *either* forecast-based trigger below. Without it, the handful
# of noisy samples present right after boot (see TrendForecastEngine's own
# MIN_SAMPLES_FOR_TREND) can produce a low-confidence forecast that still
# technically crosses a trigger threshold — the same false-positive class
# ForecastService.EVENT_MIN_CONFIDENCE already guards against on the
# forecasting side; a plan shouldn't get generated (let alone selected)
# from a guess nobody should trust either.
FORECAST_TRIGGER_MIN_CONFIDENCE = 40.0

# The horizon NO_ACTION's projection is read from — the *nearest* forecast
# horizon, since "if we do nothing" is a near-term question; app.forecasting.
# base.FORECAST_HORIZONS_SECONDS always includes 30.
NO_ACTION_HORIZON_SECONDS = 30

DEFAULT_TICK_SECONDS = 1.0

# Which event title (see app.execution.service._TOOK_EFFECT_TITLES, kept in
# sync manually — these are display strings, not shared logic) indicates
# this action type was already attempted recently on a rack. JOB_DELAY has
# no such title in the execution engine's own event list, so a repeat of
# it is simply never detected — a reasonable simplification, not a gap
# anything currently depends on.
_RECENT_ATTEMPT_TITLES: dict[ExecutionActionType, str] = {
    ExecutionActionType.WORKLOAD_MIGRATION: "Migration Completed",
    ExecutionActionType.COOLING_ADJUSTMENT: "Cooling Increased",
    ExecutionActionType.CLUSTER_REBALANCE: "Cluster Rebalanced",
    ExecutionActionType.FAN_OVERRIDE: "Fan Override Engaged",
}
RECENT_EVENTS_CONSIDERED = 20  # how far back into recent_events a repeat is still "recent"

_ACTION_DESCRIPTIONS: dict[ExecutionActionType, str] = {
    ExecutionActionType.WORKLOAD_MIGRATION: "Migrate a portion of {primary}'s workload to a cooler rack.",
    ExecutionActionType.COOLING_ADJUSTMENT: "Increase fan response and cooling capacity on {primary}.",
    ExecutionActionType.CLUSTER_REBALANCE: "Redistribute utilization from {primary} across the cluster.",
    ExecutionActionType.JOB_DELAY: "Hold new job scheduling on {primary} until conditions stabilize.",
    ExecutionActionType.FAN_OVERRIDE: "Override {primary}'s fan curve to push cooling harder.",
    ExecutionActionType.NO_ACTION: "Take no action and continue monitoring {primary}.",
}

_CANDIDATE_ACTION_TYPES: tuple[ExecutionActionType, ...] = (
    ExecutionActionType.WORKLOAD_MIGRATION,
    ExecutionActionType.COOLING_ADJUSTMENT,
    ExecutionActionType.CLUSTER_REBALANCE,
    ExecutionActionType.JOB_DELAY,
    ExecutionActionType.FAN_OVERRIDE,
    ExecutionActionType.NO_ACTION,
)


@dataclass(frozen=True)
class _Trigger:
    reason: str
    base_confidence: float


class SimulationOptimizer:
    """Conforms structurally to app.optimization.base.OptimizationEngine.
    Stateless: every call re-derives everything from the context passed
    in, matching the Protocol's "stateless from the caller's perspective"
    contract.
    """

    def __init__(self, tick_seconds: float = DEFAULT_TICK_SECONDS) -> None:
        self._tick_seconds = tick_seconds

    def plan(self, context: OptimizationContext) -> list[OptimizationPlan]:
        plans: list[OptimizationPlan] = []
        for rack in context.racks:
            trigger = _find_trigger(rack, context.forecasts.get(rack.id, []))
            if trigger is None:
                continue
            plans.append(self._plan_for_rack(context, rack, trigger))
        return plans

    # --- internals -----------------------------------------------------------

    def _plan_for_rack(self, context: OptimizationContext, trigger_rack: RackState, trigger: _Trigger) -> OptimizationPlan:
        racks_by_id = {r.id: r for r in context.racks}
        candidates = [
            self._build_candidate(context, trigger_rack, racks_by_id, action_type, trigger)
            for action_type in _CANDIDATE_ACTION_TYPES
        ]
        candidates.sort(key=lambda c: c.score.overall_score, reverse=True)
        candidates = _assign_rejection_reasons(candidates)

        return OptimizationPlan(
            trigger_key=f"rack_plan:{trigger_rack.id}",
            trigger_rack_id=trigger_rack.id,
            trigger_reason=trigger.reason,
            candidates=candidates,
            now=context.now,
        )

    def _build_candidate(
        self,
        context: OptimizationContext,
        trigger_rack: RackState,
        racks_by_id: dict[uuid.UUID, RackState],
        action_type: ExecutionActionType,
        trigger: _Trigger,
    ) -> OptimizationCandidate:
        repeat_count = _recent_attempt_count(context.recent_events, trigger_rack.id, action_type)
        candidate_confidence = scoring.confidence(trigger.base_confidence, repeat_count)

        if action_type == ExecutionActionType.NO_ACTION:
            return self._build_no_action_candidate(context, trigger_rack, candidate_confidence)

        primary_ids, redistribute_ids = _scope_for_action(action_type, trigger_rack, context.racks)
        primary_racks = [racks_by_id[rid] for rid in primary_ids]
        effect = EFFECTS[action_type]

        trajectories: dict[uuid.UUID, list[RackState]] = {}
        for rack in primary_racks:
            drivers = RackDrivers(
                gpu_bias=effect.primary_gpu_bias, fan_bias=effect.fan_bias, cooling_bias=effect.cooling_bias
            )
            trajectories[rack.id] = project_rack(rack, drivers)
        for rid in redistribute_ids:
            drivers = RackDrivers(gpu_bias=effect.redistribute_gpu_bias)
            trajectories[rid] = project_rack(racks_by_id[rid], drivers)

        projected_racks = [
            trajectories[rack.id][-1] if rack.id in trajectories else rack for rack in context.racks
        ]
        projected_cluster = compute_cluster_state(context.cluster.id, context.cluster.name, projected_racks)

        primary_projected = [trajectories[rid][-1] for rid in primary_ids]
        trigger_trajectory = trajectories[trigger_rack.id]

        temperature_reduction = trigger_rack.temperature - trigger_trajectory[-1].temperature
        cooling_improvement = trigger_trajectory[-1].cooling_efficiency - trigger_rack.cooling_efficiency
        power_impact = scoring.cluster_power_delta(context.cluster, projected_cluster)
        risk_delta = scoring.risk_reduction(primary_racks, primary_projected, context.scenario_active)
        cost = scoring.execution_cost(
            effect.primary_gpu_bias, effect.fan_bias, effect.cooling_bias, len(primary_ids)
        )
        disruption = scoring.operational_disruption(
            effect.primary_gpu_bias, len(primary_ids), len(redistribute_ids)
        )
        recovery = scoring.estimated_recovery_seconds(
            trigger_trajectory, self._tick_seconds, PLANNING_HORIZON_TICKS * self._tick_seconds
        )

        score = scoring.score_candidate(
            temperature_reduction_c=temperature_reduction,
            power_impact_kw=power_impact,
            cooling_improvement_pct=cooling_improvement,
            cost=cost,
            disruption=disruption,
            risk_delta=risk_delta,
            recovery_seconds=recovery,
            candidate_confidence=candidate_confidence,
        )

        primary_names = ", ".join(racks_by_id[rid].name for rid in primary_ids)
        return OptimizationCandidate(
            action_type=action_type,
            description=_ACTION_DESCRIPTIONS[action_type].format(primary=primary_names),
            affected_racks=list(primary_ids),
            redistribute_racks=list(redistribute_ids),
            projected_temperature=trigger_trajectory[-1].temperature,
            projected_cooling=trigger_trajectory[-1].cooling_efficiency,
            projected_power=projected_cluster.total_power,
            score=score,
        )

    def _build_no_action_candidate(
        self, context: OptimizationContext, trigger_rack: RackState, candidate_confidence: float
    ) -> OptimizationCandidate:
        """Projects "if we do nothing" from the rack's own forecast rather
        than a frozen physics replay — see app.optimization.simulator's
        module docstring for why this one candidate is special-cased.
        """
        forecast = _forecast_at(context.forecasts.get(trigger_rack.id, []), NO_ACTION_HORIZON_SECONDS)
        if forecast is not None:
            projected_temperature = forecast.predicted_temperature
            projected_cooling = forecast.predicted_cooling
            projected_power_delta = forecast.predicted_power - trigger_rack.power_draw
        else:
            projected_temperature = trigger_rack.temperature
            projected_cooling = trigger_rack.cooling_efficiency
            projected_power_delta = 0.0

        current = trigger_rack
        projected = replace(trigger_rack, temperature=projected_temperature, cooling_efficiency=projected_cooling)
        risk_delta = scoring.risk_reduction([current], [projected], context.scenario_active)
        recovery = (
            0.0
            if projected_temperature <= COMFORTABLE_TEMPERATURE_C
            else float(NO_ACTION_HORIZON_SECONDS)
        )
        score = scoring.score_candidate(
            temperature_reduction_c=current.temperature - projected_temperature,
            power_impact_kw=projected_power_delta,
            cooling_improvement_pct=projected_cooling - current.cooling_efficiency,
            cost=0.0,
            disruption=0.0,
            risk_delta=risk_delta,
            recovery_seconds=recovery,
            candidate_confidence=candidate_confidence,
        )
        return OptimizationCandidate(
            action_type=ExecutionActionType.NO_ACTION,
            description=_ACTION_DESCRIPTIONS[ExecutionActionType.NO_ACTION].format(primary=trigger_rack.name),
            affected_racks=[trigger_rack.id],
            redistribute_racks=[],
            projected_temperature=projected_temperature,
            projected_cooling=projected_cooling,
            projected_power=context.cluster.total_power + projected_power_delta,
            score=score,
        )


# --- trigger detection -----------------------------------------------------


def _find_trigger(rack: RackState, forecasts: list["RackPrediction"]) -> _Trigger | None:
    if rack.temperature > TEMPERATURE_TRIGGER_C and rack.gpu_utilization > GPU_TRIGGER_PCT:
        margin = rack.temperature - TEMPERATURE_TRIGGER_C
        return _Trigger(
            reason=(
                f"{rack.name} is at {rack.temperature:.1f}°C with GPU utilization at "
                f"{rack.gpu_utilization:.0f}%, both above trigger thresholds "
                f"({TEMPERATURE_TRIGGER_C:.0f}°C / {GPU_TRIGGER_PCT:.0f}%)."
            ),
            base_confidence=_margin_confidence(margin, 15.0),
        )

    if rack.cooling_efficiency < COOLING_TRIGGER_PCT and rack.temperature > COMFORTABLE_TEMPERATURE_C:
        margin = COOLING_TRIGGER_PCT - rack.cooling_efficiency
        return _Trigger(
            reason=(
                f"{rack.name}'s cooling efficiency has fallen to {rack.cooling_efficiency:.0f}% "
                f"while running warm ({rack.temperature:.1f}°C)."
            ),
            base_confidence=_margin_confidence(margin, 10.0),
        )

    proactive = _forecast_at(forecasts, PROACTIVE_HORIZON_SECONDS)
    if (
        proactive is not None
        and proactive.predicted_temperature > PROACTIVE_TEMPERATURE_TRIGGER_C
        and proactive.confidence >= FORECAST_TRIGGER_MIN_CONFIDENCE
    ):
        minutes = PROACTIVE_HORIZON_SECONDS // 60
        return _Trigger(
            reason=(
                f"{rack.name}'s {minutes}-minute forecast projects {proactive.predicted_temperature:.1f}°C "
                f"({proactive.confidence:.0f}% confidence), above the {PROACTIVE_TEMPERATURE_TRIGGER_C:.0f}°C "
                f"planning threshold."
            ),
            base_confidence=proactive.confidence,
        )

    power_forecast = _forecast_at(forecasts, POWER_SPIKE_HORIZON_SECONDS)
    if (
        power_forecast is not None
        and power_forecast.predicted_power - rack.power_draw >= POWER_SPIKE_TRIGGER_KW
        and power_forecast.confidence >= FORECAST_TRIGGER_MIN_CONFIDENCE
    ):
        minutes = POWER_SPIKE_HORIZON_SECONDS // 60
        return _Trigger(
            reason=(
                f"{rack.name}'s power draw is forecast to rise by "
                f"{power_forecast.predicted_power - rack.power_draw:.1f} kW within {minutes} minute(s)."
            ),
            base_confidence=power_forecast.confidence,
        )

    return None


def _margin_confidence(margin: float, span: float) -> float:
    """Same shape as app.ai.rules._confidence_from_margin, independently
    implemented — see this module's docstring for why the two engines each
    keep their own copy rather than sharing one.
    """
    return clamp(55.0 + (max(0.0, margin) / span) * 40.0, 55.0, 95.0)


def _forecast_at(forecasts: list["RackPrediction"], horizon: int) -> "RackPrediction | None":
    return next((f for f in forecasts if f.horizon_seconds == horizon), None)


def _recent_attempt_count(recent_events: list["Event"], rack_id: uuid.UUID, action_type: ExecutionActionType) -> int:
    title = _RECENT_ATTEMPT_TITLES.get(action_type)
    if title is None:
        return 0
    considered = recent_events[:RECENT_EVENTS_CONSIDERED]
    return sum(1 for event in considered if event.rack_id == rack_id and event.title == title)


# --- candidate scoping -------------------------------------------------------


def _scope_for_action(
    action_type: ExecutionActionType, trigger_rack: RackState, racks: list[RackState]
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Which racks a candidate's primary/redistribute driver bias applies
    to. Mirrors ExecutionService.start's own primary/redistribute split
    (see app.execution.service) so a plan's projected outcome matches what
    would really happen if it were executed.
    """
    if action_type == ExecutionActionType.CLUSTER_REBALANCE:
        hot = [r.id for r in racks if r.temperature > REBALANCE_TEMPERATURE_TRIGGER_C and r.id != trigger_rack.id]
        primary_ids = [trigger_rack.id, *hot]
    else:
        primary_ids = [trigger_rack.id]

    if action_type in (ExecutionActionType.WORKLOAD_MIGRATION, ExecutionActionType.CLUSTER_REBALANCE):
        redistribute_ids = _redistribute_targets(trigger_rack, set(primary_ids), racks)
    else:
        redistribute_ids = []

    return primary_ids, redistribute_ids


def _redistribute_targets(
    trigger_rack: RackState, primary_ids: set[uuid.UUID], racks: list[RackState]
) -> list[uuid.UUID]:
    """Prefer ring-neighbors (topology) when they're healthy; fall back to
    any other healthy rack — this is what makes "Rack Topology" a genuine
    input to planning rather than a pass-through of the objective's input
    list. ExecutionService itself doesn't discriminate by topology (it
    spreads onto every healthy rack); the planner can afford to be more
    targeted since it's only ever scoring a hypothesis, not committing to
    one immediately.
    """
    healthy = [r.id for r in racks if r.id not in primary_ids and r.status == RackStatus.HEALTHY]
    if not healthy:
        return []
    neighbor_ids = ring_neighbors(trigger_rack.id, racks)
    preferred = [rid for rid in healthy if rid in neighbor_ids]
    return preferred if preferred else healthy


# --- ranking -----------------------------------------------------------------


def _assign_rejection_reasons(candidates: list[OptimizationCandidate]) -> list[OptimizationCandidate]:
    """candidates is already ranked best-first; every non-winner gets a
    concrete, derived rejection_reason naming the single factor that most
    separated it from the winner — never generic boilerplate.
    """
    if len(candidates) <= 1:
        return candidates
    winner = candidates[0]
    ranked = [winner]
    for candidate in candidates[1:]:
        ranked.append(replace(candidate, rejection_reason=_rejection_reason(winner, candidate)))
    return ranked


def _rejection_reason(winner: OptimizationCandidate, candidate: OptimizationCandidate) -> str:
    w, c = winner.score, candidate.score
    factors: list[tuple[str, float]] = [
        ("less thermal risk reduced", w.risk_reduction - c.risk_reduction),
        ("less temperature relief", w.temperature_reduction_c - c.temperature_reduction_c),
        ("higher execution cost", c.execution_cost - w.execution_cost),
        ("more operational disruption", c.operational_disruption - w.operational_disruption),
        ("lower confidence", w.confidence - c.confidence),
        ("slower estimated recovery", c.estimated_recovery_seconds - w.estimated_recovery_seconds),
    ]
    label, magnitude = max(factors, key=lambda item: item[1])
    if magnitude <= 0:
        return f"Lower overall score ({c.overall_score:.0f} vs {w.overall_score:.0f})."
    return f"{label.capitalize()} than {winner.description} (score {c.overall_score:.0f} vs {w.overall_score:.0f})."
