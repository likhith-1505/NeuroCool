"""Unit tests for app.neurocore.context.build_context — pure function, no
database or running simulation required (see load_context for the thin,
DB-touching wrapper this deliberately excludes — that's verified live).
"""

import uuid
from datetime import UTC, datetime

from app.neurocore.context import build_context
from app.models.enums import RackStatus
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


def test_build_context_passes_every_field_through_unchanged() -> None:
    racks = [_make_rack()]
    cluster = _make_cluster(racks)
    now = datetime.now(UTC)

    context = build_context(
        cluster=cluster, racks=racks, scenario_key="thermal_spike",
        forecasts={}, cluster_forecast=[], active_plans=[], all_plans=[],
        active_decisions=[], all_decisions=[], all_executions=[], recent_events=[], now=now,
    )

    assert context.cluster is cluster
    assert context.racks is racks
    assert context.scenario_key == "thermal_spike"
    assert context.generated_at == now


def test_build_context_derives_scenario_active_from_scenario_key() -> None:
    racks = [_make_rack()]
    cluster = _make_cluster(racks)

    normal = build_context(
        cluster=cluster, racks=racks, scenario_key="normal", forecasts={}, cluster_forecast=[],
        active_plans=[], all_plans=[], active_decisions=[], all_decisions=[], all_executions=[], recent_events=[],
    )
    incident = build_context(
        cluster=cluster, racks=racks, scenario_key="cooling_failure", forecasts={}, cluster_forecast=[],
        active_plans=[], all_plans=[], active_decisions=[], all_decisions=[], all_executions=[], recent_events=[],
    )

    assert normal.scenario_active is False
    assert incident.scenario_active is True


def test_build_context_defaults_generated_at_to_now() -> None:
    racks = [_make_rack()]
    cluster = _make_cluster(racks)
    before = datetime.now(UTC)

    context = build_context(
        cluster=cluster, racks=racks, scenario_key="normal", forecasts={}, cluster_forecast=[],
        active_plans=[], all_plans=[], active_decisions=[], all_decisions=[], all_executions=[], recent_events=[],
    )

    after = datetime.now(UTC)
    assert before <= context.generated_at <= after
