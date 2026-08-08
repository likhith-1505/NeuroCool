"""Unit tests for the Optimization Engine — no database/Redis required,
same style as test_decision_rules.py and test_forecasting.py.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.forecasting.base import RackPrediction
from app.models.enums import ExecutionActionType, RackStatus
from app.optimization import scoring
from app.optimization.base import OptimizationContext
from app.optimization.planner import (
    COOLING_TRIGGER_PCT,
    GPU_TRIGGER_PCT,
    POWER_SPIKE_HORIZON_SECONDS,
    POWER_SPIKE_TRIGGER_KW,
    PROACTIVE_HORIZON_SECONDS,
    FORECAST_TRIGGER_MIN_CONFIDENCE,
    PROACTIVE_TEMPERATURE_TRIGGER_C,
    TEMPERATURE_TRIGGER_C,
    SimulationOptimizer,
    _find_trigger,
    _redistribute_targets,
    _rejection_reason,
    _scope_for_action,
)
from app.optimization.simulator import project_rack
from app.simulation.state import ClusterState, RackDrivers, RackState


def _make_rack(**overrides: object) -> RackState:
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(),
        name="Rack Test",
        temperature=65.0,
        gpu_utilization=55.0,
        cpu_utilization=40.0,
        power_draw=9.0,
        cooling_efficiency=60.0,
        fan_speed=40.0,
        health_score=90.0,
        prediction_state="stable",
        running_jobs=10,
        status=RackStatus.HEALTHY,
    )
    defaults.update(overrides)
    return RackState(**defaults)  # type: ignore[arg-type]


def _make_cluster(racks: list[RackState]) -> ClusterState:
    return ClusterState(
        id=uuid.uuid4(),
        name="Test Cluster",
        overall_health=90.0,
        average_temperature=65.0,
        total_power=sum(r.power_draw for r in racks),
        cooling_efficiency=60.0,
        energy_savings=15.0,
        prediction_confidence=90.0,
    )


def _make_context(racks: list[RackState], **overrides: object) -> OptimizationContext:
    defaults: dict[str, object] = dict(
        cluster=_make_cluster(racks),
        racks=racks,
        scenario_key="normal",
        scenario_active=False,
        forecasts={},
        recent_events=[],
        now=datetime.now(UTC),
    )
    defaults.update(overrides)
    return OptimizationContext(**defaults)  # type: ignore[arg-type]


def _make_forecast(horizon_seconds: int, *, predicted_temperature: float, confidence: float, predicted_power: float = 9.0) -> RackPrediction:
    return RackPrediction(
        horizon_seconds=horizon_seconds,
        timestamp=datetime.now(UTC),
        predicted_temperature=predicted_temperature,
        predicted_gpu_utilization=80.0,
        predicted_power=predicted_power,
        predicted_health=70.0,
        predicted_cooling=50.0,
        predicted_risk=60.0,
        confidence=confidence,
    )


# --- trigger detection -------------------------------------------------


def test_no_trigger_for_calm_telemetry() -> None:
    rack = _make_rack(temperature=65.0, gpu_utilization=55.0, cooling_efficiency=60.0)
    assert _find_trigger(rack, []) is None


def test_trigger_on_temperature_and_gpu_threshold() -> None:
    rack = _make_rack(temperature=TEMPERATURE_TRIGGER_C + 1.0, gpu_utilization=GPU_TRIGGER_PCT + 1.0)
    trigger = _find_trigger(rack, [])
    assert trigger is not None
    assert rack.name in trigger.reason


def test_no_trigger_when_only_temperature_crosses() -> None:
    rack = _make_rack(temperature=TEMPERATURE_TRIGGER_C + 5.0, gpu_utilization=50.0)
    assert _find_trigger(rack, []) is None


def test_trigger_on_cooling_degradation() -> None:
    rack = _make_rack(temperature=70.0, cooling_efficiency=COOLING_TRIGGER_PCT - 5.0)
    trigger = _find_trigger(rack, [])
    assert trigger is not None
    assert "cooling" in trigger.reason.lower()


def test_trigger_on_proactive_forecast() -> None:
    rack = _make_rack(temperature=70.0)
    forecasts = [_make_forecast(PROACTIVE_HORIZON_SECONDS, predicted_temperature=PROACTIVE_TEMPERATURE_TRIGGER_C + 1.0, confidence=FORECAST_TRIGGER_MIN_CONFIDENCE + 10.0)]
    trigger = _find_trigger(rack, forecasts)
    assert trigger is not None
    assert trigger.base_confidence == FORECAST_TRIGGER_MIN_CONFIDENCE + 10.0


def test_no_trigger_on_proactive_forecast_below_confidence_floor() -> None:
    rack = _make_rack(temperature=70.0)
    forecasts = [_make_forecast(PROACTIVE_HORIZON_SECONDS, predicted_temperature=PROACTIVE_TEMPERATURE_TRIGGER_C + 1.0, confidence=FORECAST_TRIGGER_MIN_CONFIDENCE - 1.0)]
    assert _find_trigger(rack, forecasts) is None


def test_trigger_on_forecast_power_spike() -> None:
    rack = _make_rack(temperature=70.0, power_draw=9.0)
    forecasts = [_make_forecast(POWER_SPIKE_HORIZON_SECONDS, predicted_temperature=72.0, confidence=80.0, predicted_power=9.0 + POWER_SPIKE_TRIGGER_KW + 1.0)]
    trigger = _find_trigger(rack, forecasts)
    assert trigger is not None
    assert "power" in trigger.reason.lower()


def test_no_trigger_on_forecast_power_spike_below_confidence_floor() -> None:
    """The same post-boot noise problem ForecastService.EVENT_MIN_
    CONFIDENCE guards against — a low-confidence power forecast shouldn't
    trigger planning even if it technically crosses the kW threshold.
    """
    rack = _make_rack(temperature=70.0, power_draw=9.0)
    forecasts = [
        _make_forecast(
            POWER_SPIKE_HORIZON_SECONDS, predicted_temperature=72.0,
            confidence=FORECAST_TRIGGER_MIN_CONFIDENCE - 1.0, predicted_power=9.0 + POWER_SPIKE_TRIGGER_KW + 1.0,
        )
    ]
    assert _find_trigger(rack, forecasts) is None


# --- candidate scoping ---------------------------------------------------


def test_scope_for_workload_migration_is_single_rack() -> None:
    rack = _make_rack()
    others = [_make_rack(status=RackStatus.HEALTHY) for _ in range(3)]
    primary, redistribute = _scope_for_action(ExecutionActionType.WORKLOAD_MIGRATION, rack, [rack, *others])
    assert primary == [rack.id]
    assert set(redistribute) <= {r.id for r in others}
    assert redistribute  # healthy racks are available


def test_scope_for_cluster_rebalance_includes_other_hot_racks() -> None:
    rack = _make_rack(temperature=80.0)
    hot_neighbor = _make_rack(temperature=85.0)
    cool_rack = _make_rack(temperature=60.0)
    primary, _ = _scope_for_action(ExecutionActionType.CLUSTER_REBALANCE, rack, [rack, hot_neighbor, cool_rack])
    assert set(primary) == {rack.id, hot_neighbor.id}


def test_scope_for_cooling_adjustment_has_no_redistribute() -> None:
    rack = _make_rack()
    others = [_make_rack() for _ in range(2)]
    primary, redistribute = _scope_for_action(ExecutionActionType.COOLING_ADJUSTMENT, rack, [rack, *others])
    assert primary == [rack.id]
    assert redistribute == []


def test_redistribute_targets_prefers_ring_neighbors() -> None:
    # 4 racks in a ring: rack is at index 0, neighbors are index 1 and 3.
    racks = [_make_rack() for _ in range(4)]
    trigger_rack = racks[0]
    targets = _redistribute_targets(trigger_rack, {trigger_rack.id}, racks)
    assert set(targets) == {racks[1].id, racks[3].id}


def test_redistribute_targets_falls_back_when_no_healthy_neighbor() -> None:
    racks = [_make_rack() for _ in range(4)]
    trigger_rack = racks[0]
    racks[1] = _make_rack(id=racks[1].id, status=RackStatus.CRITICAL)
    racks[3] = _make_rack(id=racks[3].id, status=RackStatus.CRITICAL)
    targets = _redistribute_targets(trigger_rack, {trigger_rack.id}, racks)
    assert set(targets) == {racks[2].id}  # only remaining healthy rack, not a neighbor


# --- simulator -----------------------------------------------------------


def test_project_rack_is_deterministic() -> None:
    rack = _make_rack(temperature=70.0)
    drivers = RackDrivers(gpu_bias=-20.0)
    trajectory_a = project_rack(rack, drivers, ticks=10)
    trajectory_b = project_rack(rack, drivers, ticks=10)
    assert [r.temperature for r in trajectory_a] == [r.temperature for r in trajectory_b]
    assert [r.gpu_utilization for r in trajectory_a] == [r.gpu_utilization for r in trajectory_b]


def test_project_rack_negative_gpu_bias_lowers_utilization() -> None:
    rack = _make_rack(gpu_utilization=80.0)
    trajectory = project_rack(rack, RackDrivers(gpu_bias=-40.0), ticks=15)
    assert trajectory[-1].gpu_utilization < rack.gpu_utilization


def test_project_rack_cooling_bias_raises_cooling_efficiency() -> None:
    rack = _make_rack(cooling_efficiency=50.0)
    trajectory = project_rack(rack, RackDrivers(fan_bias=25.0, cooling_bias=12.0), ticks=15)
    assert trajectory[-1].cooling_efficiency > rack.cooling_efficiency


# --- scoring ---------------------------------------------------------------


def test_execution_cost_increases_with_bias_magnitude() -> None:
    small = scoring.execution_cost(gpu_bias=-10.0, fan_bias=0.0, cooling_bias=0.0, affected_rack_count=1)
    large = scoring.execution_cost(gpu_bias=-30.0, fan_bias=0.0, cooling_bias=0.0, affected_rack_count=1)
    assert large > small


def test_execution_cost_increases_with_rack_count() -> None:
    one_rack = scoring.execution_cost(gpu_bias=-20.0, fan_bias=0.0, cooling_bias=0.0, affected_rack_count=1)
    three_racks = scoring.execution_cost(gpu_bias=-20.0, fan_bias=0.0, cooling_bias=0.0, affected_rack_count=3)
    assert three_racks > one_rack


def test_operational_disruption_increases_with_redistribute_count() -> None:
    none = scoring.operational_disruption(gpu_bias=-20.0, primary_count=1, redistribute_count=0)
    some = scoring.operational_disruption(gpu_bias=-20.0, primary_count=1, redistribute_count=3)
    assert some > none


def test_risk_reduction_positive_when_cooler_and_better_cooled() -> None:
    current = [_make_rack(temperature=85.0, cooling_efficiency=40.0)]
    projected = [_make_rack(temperature=70.0, cooling_efficiency=60.0)]
    delta = scoring.risk_reduction(current, projected, scenario_active=False)
    assert delta > 0


def test_risk_reduction_zero_for_unchanged_state() -> None:
    racks = [_make_rack(temperature=70.0, cooling_efficiency=60.0)]
    delta = scoring.risk_reduction(racks, racks, scenario_active=False)
    assert delta == 0.0


def test_confidence_decreases_with_recent_repeats() -> None:
    fresh = scoring.confidence(80.0, 0)
    repeated = scoring.confidence(80.0, 2)
    assert repeated < fresh
    assert repeated >= scoring.MIN_CONFIDENCE


def test_score_candidate_rewards_temperature_and_risk_reduction() -> None:
    weak = scoring.score_candidate(
        temperature_reduction_c=1.0, power_impact_kw=0.0, cooling_improvement_pct=1.0,
        cost=20.0, disruption=20.0, risk_delta=5.0, recovery_seconds=20.0, candidate_confidence=70.0,
    )
    strong = scoring.score_candidate(
        temperature_reduction_c=10.0, power_impact_kw=0.0, cooling_improvement_pct=10.0,
        cost=20.0, disruption=20.0, risk_delta=40.0, recovery_seconds=20.0, candidate_confidence=70.0,
    )
    assert strong.overall_score > weak.overall_score
    assert 0.0 <= weak.overall_score <= 100.0
    assert 0.0 <= strong.overall_score <= 100.0


def test_score_candidate_penalizes_cost_and_disruption() -> None:
    cheap = scoring.score_candidate(
        temperature_reduction_c=5.0, power_impact_kw=0.0, cooling_improvement_pct=5.0,
        cost=5.0, disruption=5.0, risk_delta=10.0, recovery_seconds=10.0, candidate_confidence=70.0,
    )
    expensive = scoring.score_candidate(
        temperature_reduction_c=5.0, power_impact_kw=0.0, cooling_improvement_pct=5.0,
        cost=90.0, disruption=90.0, risk_delta=10.0, recovery_seconds=10.0, candidate_confidence=70.0,
    )
    assert cheap.overall_score > expensive.overall_score


# --- rejection reasons -----------------------------------------------------


def test_rejection_reason_names_a_concrete_differentiator() -> None:
    from app.optimization.base import CandidateScore, OptimizationCandidate

    rack_id = uuid.uuid4()

    def _candidate(**score_overrides: float) -> OptimizationCandidate:
        base = dict(
            temperature_reduction_c=5.0, power_impact_kw=0.0, cooling_improvement_pct=5.0,
            execution_cost=20.0, operational_disruption=20.0, risk_reduction=10.0,
            estimated_recovery_seconds=20.0, confidence=70.0, overall_score=60.0,
        )
        base.update(score_overrides)
        return OptimizationCandidate(
            action_type=ExecutionActionType.WORKLOAD_MIGRATION,
            description="Migrate workload.",
            affected_racks=[rack_id],
            redistribute_racks=[],
            projected_temperature=60.0,
            projected_cooling=60.0,
            projected_power=9.0,
            score=CandidateScore(**base),
        )

    winner = _candidate(overall_score=80.0, temperature_reduction_c=10.0)
    loser = _candidate(overall_score=40.0, temperature_reduction_c=1.0)  # much less relief — the standout factor
    reason = _rejection_reason(winner, loser)
    assert "temperature relief" in reason.lower()


# --- end-to-end planning ----------------------------------------------------


def test_plan_generates_all_six_candidate_action_types() -> None:
    engine = SimulationOptimizer()
    rack = _make_rack(temperature=TEMPERATURE_TRIGGER_C + 5.0, gpu_utilization=GPU_TRIGGER_PCT + 5.0)
    others = [_make_rack() for _ in range(3)]
    context = _make_context([rack, *others])

    plans = engine.plan(context)
    assert len(plans) == 1
    plan = plans[0]
    action_types = {c.action_type for c in plan.candidates}
    assert action_types == set(ExecutionActionType)


def test_plan_candidates_are_ranked_best_first() -> None:
    engine = SimulationOptimizer()
    rack = _make_rack(temperature=TEMPERATURE_TRIGGER_C + 5.0, gpu_utilization=GPU_TRIGGER_PCT + 5.0)
    others = [_make_rack() for _ in range(3)]
    plan = engine.plan(_make_context([rack, *others]))[0]

    scores = [c.score.overall_score for c in plan.candidates]
    assert scores == sorted(scores, reverse=True)


def test_plan_alternatives_have_rejection_reasons_but_winner_does_not() -> None:
    engine = SimulationOptimizer()
    rack = _make_rack(temperature=TEMPERATURE_TRIGGER_C + 5.0, gpu_utilization=GPU_TRIGGER_PCT + 5.0)
    others = [_make_rack() for _ in range(3)]
    plan = engine.plan(_make_context([rack, *others]))[0]

    assert plan.winner.rejection_reason is None
    assert all(alt.rejection_reason is not None for alt in plan.alternatives)


def test_plan_uses_forecast_confidence_for_proactive_trigger() -> None:
    engine = SimulationOptimizer()
    rack = _make_rack(temperature=70.0, gpu_utilization=60.0)
    others = [_make_rack() for _ in range(3)]
    forecasts = {
        rack.id: [_make_forecast(PROACTIVE_HORIZON_SECONDS, predicted_temperature=PROACTIVE_TEMPERATURE_TRIGGER_C + 3.0, confidence=77.0)]
    }
    context = _make_context([rack, *others], forecasts=forecasts)
    plans = engine.plan(context)
    assert len(plans) == 1
    # The winning (or any real) candidate's confidence traces back to the
    # forecast's own 77% — not a fixed/fabricated number.
    assert any(c.score.confidence <= 77.0 for c in plans[0].candidates)


def test_plan_no_trigger_produces_no_plans() -> None:
    engine = SimulationOptimizer()
    racks = [_make_rack() for _ in range(4)]
    assert engine.plan(_make_context(racks)) == []


def test_no_action_candidate_reflects_forecast_when_available() -> None:
    engine = SimulationOptimizer()
    rack = _make_rack(temperature=TEMPERATURE_TRIGGER_C + 5.0, gpu_utilization=GPU_TRIGGER_PCT + 5.0)
    others = [_make_rack() for _ in range(3)]
    forecasts = {rack.id: [_make_forecast(30, predicted_temperature=95.0, confidence=80.0)]}
    plan = engine.plan(_make_context([rack, *others], forecasts=forecasts))[0]

    no_action = next(c for c in plan.candidates if c.action_type == ExecutionActionType.NO_ACTION)
    assert no_action.projected_temperature == 95.0
