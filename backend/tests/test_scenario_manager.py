"""Unit tests for ScenarioManager — no database/Redis required.

`activated_at` is set internally via utcnow(), so tests control elapsed
time by adding a timedelta to `manager.activated_at` rather than mocking
the clock — simple and avoids any monkeypatching.
"""

import uuid
from datetime import timedelta

import pytest

from app.models.enums import RackStatus
from app.simulation.physics import compute_next_rack_state
from app.simulation.scenario_manager import SCENARIOS, ScenarioManager
from app.simulation.state import RackInternals, RackState


def _make_racks(n: int = 4) -> list[RackState]:
    return [
        RackState(
            id=uuid.uuid4(),
            name=f"Rack {i}",
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
        for i in range(n)
    ]


def test_activate_unknown_scenario_raises() -> None:
    manager = ScenarioManager()
    with pytest.raises(ValueError, match="Unknown scenario"):
        manager.activate("does_not_exist", _make_racks())


def test_only_one_scenario_active_at_a_time() -> None:
    manager = ScenarioManager()
    racks = _make_racks()
    manager.activate("thermal_spike", racks)
    assert manager.active_key == "thermal_spike"
    manager.activate("training_burst", racks)
    assert manager.active_key == "training_burst"  # replaced outright, not stacked


def test_cluster_scope_scenario_applies_to_every_rack() -> None:
    manager = ScenarioManager()
    racks = _make_racks()
    manager.activate("training_burst", racks)

    fully_ramped = manager.activated_at + timedelta(seconds=SCENARIOS["training_burst"].ramp_seconds + 1)
    drivers = manager.compute_drivers(racks, fully_ramped)

    assert set(drivers.keys()) == {r.id for r in racks}
    for driver in drivers.values():
        assert driver.gpu_bias == pytest.approx(SCENARIOS["training_burst"].gpu_bias)


def test_single_rack_scope_picks_exactly_one_target_and_marks_neighbors() -> None:
    manager = ScenarioManager()
    racks = _make_racks()
    manager.activate("thermal_spike", racks)

    assert manager.target_rack_id is not None
    assert manager.target_rack_id in {r.id for r in racks}

    fully_ramped = manager.activated_at + timedelta(seconds=SCENARIOS["thermal_spike"].ramp_seconds + 1)
    drivers = manager.compute_drivers(racks, fully_ramped)

    target_driver = drivers[manager.target_rack_id]
    assert target_driver.gpu_bias == pytest.approx(SCENARIOS["thermal_spike"].gpu_bias)

    neighbor_drivers = [d for rid, d in drivers.items() if rid != manager.target_rack_id]
    assert neighbor_drivers, "thermal_spike should give at least one neighbor a smaller bias"
    for d in neighbor_drivers:
        assert 0 < d.gpu_bias < target_driver.gpu_bias


def test_drivers_ramp_linearly_over_time() -> None:
    manager = ScenarioManager()
    racks = _make_racks()
    manager.activate("thermal_spike", racks)
    ramp_seconds = SCENARIOS["thermal_spike"].ramp_seconds

    halfway = manager.activated_at + timedelta(seconds=ramp_seconds / 2)
    drivers = manager.compute_drivers(racks, halfway)
    bias_at_half = drivers[manager.target_rack_id].gpu_bias

    full_bias = SCENARIOS["thermal_spike"].gpu_bias
    assert bias_at_half == pytest.approx(full_bias * 0.5, rel=0.05)


def test_no_drivers_before_activation_ramp_starts() -> None:
    manager = ScenarioManager()
    racks = _make_racks()
    manager.activate("training_burst", racks)

    drivers = manager.compute_drivers(racks, manager.activated_at)  # elapsed == 0
    for driver in drivers.values():
        assert driver.gpu_bias == 0.0


def test_transition_state_flips_to_steady_after_ramp_completes() -> None:
    manager = ScenarioManager()
    racks = _make_racks()
    manager.activate("cooling_failure", racks)
    assert manager.transition_state == "transitioning"

    ramp_seconds = SCENARIOS["cooling_failure"].ramp_seconds
    manager.compute_drivers(racks, manager.activated_at + timedelta(seconds=ramp_seconds + 1))
    assert manager.transition_state == "steady"


def test_normal_scenario_produces_no_drivers() -> None:
    manager = ScenarioManager()
    racks = _make_racks()
    manager.activate("thermal_spike", racks)
    manager.activate("normal", racks)

    later = manager.activated_at + timedelta(seconds=100)
    assert manager.compute_drivers(racks, later) == {}


def test_power_surge_auto_reverts_after_its_duration() -> None:
    manager = ScenarioManager()
    racks = _make_racks()
    manager.activate("power_surge", racks)
    duration = SCENARIOS["power_surge"].duration_seconds
    assert duration is not None

    too_soon = manager.activated_at + timedelta(seconds=duration - 1)
    assert manager.maybe_auto_revert(too_soon) is None
    assert manager.active_key == "power_surge"

    after = manager.activated_at + timedelta(seconds=duration + 1)
    completed = manager.maybe_auto_revert(after)
    assert completed is not None
    assert completed.key == "power_surge"
    assert manager.active_key == "normal"


def test_indefinite_scenarios_never_auto_revert() -> None:
    manager = ScenarioManager()
    racks = _make_racks()
    manager.activate("thermal_spike", racks)
    far_future = manager.activated_at + timedelta(days=1)
    assert manager.maybe_auto_revert(far_future) is None
    assert manager.active_key == "thermal_spike"


def test_replay_without_any_prior_scenario_raises() -> None:
    manager = ScenarioManager()
    with pytest.raises(ValueError, match="No previous scenario"):
        manager.replay(_make_racks())


def test_replay_reactivates_last_non_normal_scenario() -> None:
    manager = ScenarioManager()
    racks = _make_racks()
    manager.activate("cooling_failure", racks)
    manager.reset(racks)
    assert manager.active_key == "normal"

    manager.replay(racks)
    assert manager.active_key == "cooling_failure"


def test_reset_returns_to_normal_scope_cluster() -> None:
    manager = ScenarioManager()
    racks = _make_racks()
    manager.activate("thermal_spike", racks)
    manager.reset(racks)
    assert manager.active_key == "normal"
    assert manager.target_rack_id is None


def test_cooling_failure_drivers_cap_cooling_target_in_physics() -> None:
    """Integration between ScenarioManager's driver and the physics step:
    fan speed should still climb (saturate) while cooling_efficiency stays
    capped low, exactly the "fans work, cooling doesn't" failure mode.
    """
    import random

    manager = ScenarioManager(rng=random.Random(1))
    racks = _make_racks(1)
    manager.activate("cooling_failure", racks)
    ramp_seconds = SCENARIOS["cooling_failure"].ramp_seconds

    rack = racks[0]
    internals = RackInternals(gpu_baseline=rack.gpu_utilization, jobs_baseline=float(rack.running_jobs))
    rng = random.Random(2)

    fully_ramped_time = manager.activated_at + timedelta(seconds=ramp_seconds + 1)
    for _ in range(60):
        drivers = manager.compute_drivers([rack], fully_ramped_time)
        driver = drivers.get(rack.id)
        assert driver is not None
        rack, internals = compute_next_rack_state(rack, internals, rng, driver)

    assert rack.cooling_efficiency <= SCENARIOS["cooling_failure"].cooling_ceiling + 1.0
    assert rack.fan_speed > 90.0  # fans saturate trying to compensate
    assert rack.temperature > 60.0  # temperature still climbs despite that
