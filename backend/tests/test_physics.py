"""Unit tests for the pure simulation math — no database/Redis required."""

import random
import uuid

from app.models.enums import RackStatus
from app.simulation.physics import (
    clamp,
    compute_cluster_state,
    compute_next_rack_state,
    compute_prediction_state,
    ease,
)
from app.simulation.state import RackInternals, RackState


def _make_rack(**overrides: object) -> RackState:
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(),
        name="Rack Test",
        temperature=60.0,
        gpu_utilization=50.0,
        cpu_utilization=40.0,
        power_draw=8.0,
        cooling_efficiency=65.0,
        fan_speed=40.0,
        health_score=90.0,
        prediction_state="stable",
        running_jobs=10,
        status=RackStatus.HEALTHY,
    )
    defaults.update(overrides)
    return RackState(**defaults)  # type: ignore[arg-type]


def test_ease_moves_toward_target_without_overshooting() -> None:
    result = ease(current=50.0, target=100.0, rate=0.2)
    assert 50.0 < result < 100.0


def test_clamp_bounds_values() -> None:
    assert clamp(150.0, 0.0, 100.0) == 100.0
    assert clamp(-10.0, 0.0, 100.0) == 0.0
    assert clamp(42.0, 0.0, 100.0) == 42.0


def test_rack_state_evolves_smoothly_over_many_ticks() -> None:
    """No single tick should move temperature or GPU utilization by a huge
    amount — that's what "avoid random jumps" means in practice.
    """
    rng = random.Random(42)
    rack = _make_rack()
    internals = RackInternals(gpu_baseline=rack.gpu_utilization, jobs_baseline=float(rack.running_jobs))

    for _ in range(200):
        previous = rack
        rack, internals = compute_next_rack_state(previous, internals, rng)

        assert abs(rack.temperature - previous.temperature) < 5.0
        assert abs(rack.gpu_utilization - previous.gpu_utilization) < 15.0
        assert 35.0 <= rack.temperature <= 99.0
        assert 0.0 <= rack.gpu_utilization <= 100.0
        assert 0.0 <= rack.health_score <= 100.0
        assert rack.prediction_state in {"stable", "watch", "at_risk"}


def test_higher_load_and_worse_cooling_produce_higher_temperature_target() -> None:
    """Sanity-checks the causal chain: more power + worse cooling -> hotter."""
    rng = random.Random(7)
    calm = _make_rack(gpu_utilization=20.0, cpu_utilization=20.0, cooling_efficiency=90.0)
    busy = _make_rack(gpu_utilization=95.0, cpu_utilization=90.0, cooling_efficiency=35.0)
    internals = RackInternals(gpu_baseline=50.0, jobs_baseline=10.0)

    calm_next, _ = compute_next_rack_state(calm, internals, random.Random(7))
    busy_next, _ = compute_next_rack_state(busy, internals, random.Random(7))

    assert busy_next.power_draw > calm_next.power_draw
    assert busy_next.temperature > calm_next.temperature
    assert busy_next.health_score < calm_next.health_score


def test_prediction_state_has_hysteresis_at_the_boundary() -> None:
    """A health score hovering right at a threshold should not flap the
    state back and forth every tick.
    """
    # Entering "watch" requires health < 68 while stable...
    assert compute_prediction_state(67.0, previous_state="stable") == "watch"
    # ...but recovering out of "watch" requires climbing past 78, not just
    # back above 68 — this is the hysteresis band.
    assert compute_prediction_state(70.0, previous_state="watch") == "watch"
    assert compute_prediction_state(79.0, previous_state="watch") == "stable"


def test_compute_cluster_state_is_derived_from_racks() -> None:
    cluster_id = uuid.uuid4()
    racks = [
        _make_rack(temperature=60.0, health_score=90.0, power_draw=8.0, cooling_efficiency=70.0),
        _make_rack(temperature=70.0, health_score=80.0, power_draw=10.0, cooling_efficiency=60.0),
    ]

    cluster = compute_cluster_state(cluster_id, "Test Cluster", racks)

    assert cluster.average_temperature == 65.0
    assert cluster.overall_health == 85.0
    assert cluster.total_power == 18.0
    assert 0.0 <= cluster.energy_savings <= 45.0
    assert 0.0 <= cluster.prediction_confidence <= 100.0


def test_compute_cluster_state_handles_empty_cluster() -> None:
    cluster = compute_cluster_state(uuid.uuid4(), "Empty", [])
    assert cluster.overall_health == 100.0
    assert cluster.total_power == 0.0
