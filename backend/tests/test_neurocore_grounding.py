"""Unit tests for app.neurocore.grounding — deterministic retrieval over a
hand-built NeuroCoreContext, no database required.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.forecasting.base import RackPrediction
from app.models.decision import Decision
from app.models.enums import (
    DecisionStatus,
    EventSeverity,
    ExecutionActionType,
    ExecutionStatus,
    OptimizationPlanStatus,
    RackStatus,
)
from app.models.event import Event
from app.models.execution import Execution
from app.models.optimization_plan import OptimizationPlan
from app.neurocore.context import build_context
from app.neurocore.grounding import (
    build_grounding,
    find_latest_decision_for_rack,
    find_latest_execution_for_rack,
    find_latest_plan_for_rack,
    resolve_rack,
)
from app.simulation.state import ClusterState, RackState


def _make_rack(**overrides: object) -> RackState:
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(), name="Rack A1", temperature=65.0, gpu_utilization=55.0,
        cpu_utilization=40.0, power_draw=9.0, cooling_efficiency=60.0, fan_speed=40.0,
        health_score=90.0, prediction_state="stable", running_jobs=10, status=RackStatus.HEALTHY,
    )
    defaults.update(overrides)
    return RackState(**defaults)  # type: ignore[arg-type]


def _make_cluster(racks: list[RackState]) -> ClusterState:
    return ClusterState(
        id=uuid.uuid4(), name="Test Cluster", overall_health=90.0, average_temperature=65.0,
        total_power=sum(r.power_draw for r in racks), cooling_efficiency=60.0,
        energy_savings=15.0, prediction_confidence=90.0,
    )


def _make_forecast(horizon_seconds: int, *, predicted_temperature: float, confidence: float = 70.0) -> RackPrediction:
    return RackPrediction(
        horizon_seconds=horizon_seconds, timestamp=datetime.now(UTC), predicted_temperature=predicted_temperature,
        predicted_gpu_utilization=70.0, predicted_power=10.0, predicted_health=70.0,
        predicted_cooling=55.0, predicted_risk=50.0, confidence=confidence,
    )


def _make_candidate(
    action_type: ExecutionActionType, rack_id: uuid.UUID, *, overall_score: float = 60.0,
    rejection_reason: str | None = None, temperature_reduction_c: float = 5.0, risk_reduction: float = 10.0,
) -> dict:
    return {
        "action_type": action_type.value,
        "description": f"{action_type.value.replace('_', ' ').title()} on the rack.",
        "affected_racks": [str(rack_id)],
        "redistribute_racks": [],
        "projected_temperature": 70.0 - temperature_reduction_c,
        "projected_cooling": 65.0,
        "projected_power": 10.0,
        "score": {
            "temperature_reduction_c": temperature_reduction_c,
            "power_impact_kw": -1.0,
            "cooling_improvement_pct": 5.0,
            "execution_cost": 20.0,
            "operational_disruption": 15.0,
            "risk_reduction": risk_reduction,
            "estimated_recovery_seconds": 20.0,
            "confidence": 70.0,
            "overall_score": overall_score,
        },
        "rejection_reason": rejection_reason,
    }


def _make_plan(rack_id: uuid.UUID, candidates: list[dict], **overrides: object) -> OptimizationPlan:
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(), cluster_id=uuid.uuid4(), scenario_id=None, trigger_rack_id=rack_id,
        trigger_key=f"rack_plan:{rack_id}", trigger_reason="Rack A1 is at 88.0°C.",
        status=OptimizationPlanStatus.COMPLETED, error_message=None, candidates=candidates,
        winner_action_type=ExecutionActionType(candidates[0]["action_type"]) if candidates else None,
        winner_overall_score=candidates[0]["score"]["overall_score"] if candidates else None,
        winner_confidence=candidates[0]["score"]["confidence"] if candidates else None,
        created_at=datetime.now(UTC), completed_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return OptimizationPlan(**defaults)  # type: ignore[arg-type]


def _make_decision(rack_id: uuid.UUID, **overrides: object) -> Decision:
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(), cluster_id=uuid.uuid4(), scenario_id=None, plan_id=None,
        rule_key=f"workload_migration:{rack_id}", severity=EventSeverity.WARNING,
        title="Migrate workload off Rack A1", reasoning="Rack A1 is running hot.",
        recommended_action="Migrate workload to a cooler rack.",
        expected_temperature_reduction=8.0, expected_power_saving=None, confidence=75.0,
        affected_racks=[rack_id], affected_jobs=[], alternative_actions=[],
        status=DecisionStatus.PENDING, timestamp=datetime.now(UTC),
        updated_at=datetime.now(UTC), expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    defaults.update(overrides)
    return Decision(**defaults)  # type: ignore[arg-type]


def _make_execution(rack_id: uuid.UUID, decision_id: uuid.UUID, **overrides: object) -> Execution:
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(), decision_id=decision_id, cluster_id=uuid.uuid4(), scenario_id=None,
        action_type=ExecutionActionType.WORKLOAD_MIGRATION, status=ExecutionStatus.COMPLETED,
        affected_racks=[rack_id], summary="Migrated workload off Rack A1 onto Rack B2.",
        error_message=None, started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Execution(**defaults)  # type: ignore[arg-type]


def _make_event(rack_id: uuid.UUID | None = None, **overrides: object) -> Event:
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(), cluster_id=uuid.uuid4(), rack_id=rack_id, scenario_id=None,
        severity=EventSeverity.WARNING, title="Rack A1 temperature exceeded threshold",
        message="Rack A1 reached 82.0°C.", occurred_at=datetime.now(UTC), created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


def _context(racks, **overrides):
    defaults: dict[str, object] = dict(
        cluster=_make_cluster(racks), racks=racks, scenario_key="normal", forecasts={},
        cluster_forecast=[], active_plans=[], all_plans=[], active_decisions=[],
        all_decisions=[], all_executions=[], recent_events=[],
    )
    defaults.update(overrides)
    return build_context(**defaults)  # type: ignore[arg-type]


# --- resolution helpers ------------------------------------------------


def test_resolve_rack_by_explicit_id() -> None:
    rack = _make_rack()
    context = _context([rack])
    assert resolve_rack(context, message="irrelevant text", rack_id=rack.id) is rack


def test_resolve_rack_by_name_in_message() -> None:
    rack = _make_rack(name="Rack A1")
    other = _make_rack(name="Rack B2")
    context = _context([rack, other])
    resolved = resolve_rack(context, message="Why is Rack A1 at risk?", rack_id=None)
    assert resolved is rack


def test_resolve_rack_returns_none_when_nothing_matches() -> None:
    context = _context([_make_rack(name="Rack A1")])
    assert resolve_rack(context, message="Summarize the cluster.", rack_id=None) is None


def test_find_latest_plan_for_rack() -> None:
    rack = _make_rack()
    plan = _make_plan(rack.id, [_make_candidate(ExecutionActionType.COOLING_ADJUSTMENT, rack.id)])
    context = _context([rack], all_plans=[plan])
    assert find_latest_plan_for_rack(context, rack.id) is plan
    assert find_latest_plan_for_rack(context, uuid.uuid4()) is None


def test_find_latest_decision_for_rack() -> None:
    rack = _make_rack()
    decision = _make_decision(rack.id)
    context = _context([rack], all_decisions=[decision])
    assert find_latest_decision_for_rack(context, rack.id) is decision
    assert find_latest_decision_for_rack(context, uuid.uuid4()) is None


def test_find_latest_execution_for_rack_prefers_matching_decision() -> None:
    rack = _make_rack()
    decision_id = uuid.uuid4()
    other_execution = _make_execution(rack.id, uuid.uuid4())
    matching_execution = _make_execution(rack.id, decision_id)
    context = _context([rack], all_executions=[other_execution, matching_execution])
    assert find_latest_execution_for_rack(context, rack.id, decision_id=decision_id) is matching_execution


# --- grounding: rack-specific --------------------------------------------


def test_grounding_rack_specific_question_includes_all_available_sources() -> None:
    rack = _make_rack(name="Rack A1", temperature=88.0)
    forecast = [_make_forecast(30, predicted_temperature=90.0), _make_forecast(300, predicted_temperature=95.0)]
    winner = _make_candidate(ExecutionActionType.WORKLOAD_MIGRATION, rack.id, overall_score=80.0)
    alt = _make_candidate(ExecutionActionType.COOLING_ADJUSTMENT, rack.id, overall_score=50.0, rejection_reason="Less temperature relief.")
    plan = _make_plan(rack.id, [winner, alt])
    decision = _make_decision(rack.id)
    execution = _make_execution(rack.id, decision.id)
    event = _make_event(rack.id)

    context = _context(
        [rack], forecasts={rack.id: forecast}, all_plans=[plan], all_decisions=[decision],
        all_executions=[execution], recent_events=[event],
    )

    grounding = build_grounding(context, message="Why is Rack A1 at risk?", rack_id=None)

    assert f"rack:{rack.name}" in grounding.sources
    assert f"forecast:{rack.name}" in grounding.sources
    assert f"plan:{plan.id}" in grounding.sources
    assert f"decision:{decision.id}" in grounding.sources
    assert f"execution:{execution.id}" in grounding.sources
    assert f"event:{event.id}" in grounding.sources
    assert "90.0" in grounding.context_block or "90" in grounding.context_block


def test_grounding_states_unavailable_when_no_plan_or_decision_exists() -> None:
    rack = _make_rack(name="Rack A1")
    context = _context([rack])

    grounding = build_grounding(context, message="Why is Rack A1 at risk?", rack_id=None)

    assert "No forecast is currently available for Rack A1" in grounding.context_block
    assert "No optimization plan is currently active for Rack A1" in grounding.context_block
    assert "No decision has been recorded for Rack A1" in grounding.context_block
    assert not any(source.startswith("plan:") for source in grounding.sources)
    assert not any(source.startswith("decision:") for source in grounding.sources)


def test_grounding_unknown_rack_id_is_stated_explicitly() -> None:
    context = _context([_make_rack()])
    unknown_id = uuid.uuid4()

    grounding = build_grounding(context, message="Why is this rack at risk?", rack_id=unknown_id)

    assert str(unknown_id) in grounding.context_block
    assert "does not match any known rack" in grounding.context_block


def test_grounding_what_if_nothing_is_covered_by_the_plans_no_action_candidate() -> None:
    rack = _make_rack(name="Rack A1")
    winner = _make_candidate(ExecutionActionType.WORKLOAD_MIGRATION, rack.id, overall_score=80.0)
    no_action = _make_candidate(
        ExecutionActionType.NO_ACTION, rack.id, overall_score=10.0,
        rejection_reason="Less thermal risk reduced.", temperature_reduction_c=-5.0, risk_reduction=-20.0,
    )
    plan = _make_plan(rack.id, [winner, no_action])
    context = _context([rack], all_plans=[plan])

    grounding = build_grounding(context, message="What happens if we do nothing to Rack A1?", rack_id=None)

    assert "If no action is taken" in grounding.context_block


# --- grounding: cluster-wide -----------------------------------------------


def test_grounding_cluster_wide_question_has_no_rack_source() -> None:
    racks = [_make_rack(name="Rack A1"), _make_rack(name="Rack B2")]
    context = _context(racks)

    grounding = build_grounding(context, message="Summarize the current cluster.", rack_id=None)

    assert not any(source.startswith("rack:") for source in grounding.sources)
    assert "Test Cluster" in grounding.context_block


def test_grounding_cluster_wide_includes_recent_events_and_active_plans() -> None:
    racks = [_make_rack(name="Rack A1")]
    event = _make_event(rack_id=None, title="Thermal Spike Triggered")
    plan = _make_plan(racks[0].id, [_make_candidate(ExecutionActionType.COOLING_ADJUSTMENT, racks[0].id)])
    context = _context(racks, recent_events=[event], active_plans=[plan])

    grounding = build_grounding(context, message="What changed recently?", rack_id=None)

    assert f"event:{event.id}" in grounding.sources
    assert f"plan:{plan.id}" in grounding.sources


# --- confidence --------------------------------------------------------


def test_grounding_confidence_increases_with_available_data() -> None:
    rack = _make_rack(name="Rack A1")
    sparse_context = _context([rack])
    sparse = build_grounding(sparse_context, message="Why is Rack A1 at risk?", rack_id=None)

    forecast = [_make_forecast(30, predicted_temperature=90.0)]
    plan = _make_plan(rack.id, [_make_candidate(ExecutionActionType.WORKLOAD_MIGRATION, rack.id)])
    decision = _make_decision(rack.id)
    rich_context = _context([rack], forecasts={rack.id: forecast}, all_plans=[plan], all_decisions=[decision])
    rich = build_grounding(rich_context, message="Why is Rack A1 at risk?", rack_id=None)

    assert rich.confidence > sparse.confidence
    assert 0.0 <= sparse.confidence <= 100.0
    assert 0.0 <= rich.confidence <= 100.0


def test_grounding_confidence_is_always_in_bounds_cluster_wide() -> None:
    context = _context([_make_rack()])
    grounding = build_grounding(context, message="Summarize the cluster.", rack_id=None)
    assert 0.0 <= grounding.confidence <= 100.0
