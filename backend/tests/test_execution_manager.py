"""Unit tests for ExecutionManager and combine_drivers — no database/Redis
required, same style as test_scenario_manager.py.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.execution.manager import (
    HOLD_SECONDS,
    RAMP_SECONDS,
    TOTAL_LIFECYCLE_SECONDS,
    ExecutionManager,
)
from app.models.enums import ExecutionActionType
from app.simulation.state import RackDrivers, combine_drivers


def _now() -> datetime:
    return datetime.now(UTC)


# --- combine_drivers ------------------------------------------------------


def test_combine_drivers_sums_bias_fields() -> None:
    a = RackDrivers(gpu_bias=10.0, power_bias_kw=1.0, fan_bias=5.0, cooling_bias=2.0)
    b = RackDrivers(gpu_bias=-3.0, power_bias_kw=0.5, fan_bias=1.0, cooling_bias=1.0)
    combined = combine_drivers(a, b)
    assert combined.gpu_bias == 7.0
    assert combined.power_bias_kw == 1.5
    assert combined.fan_bias == 6.0
    assert combined.cooling_bias == 3.0


def test_combine_drivers_takes_most_restrictive_ceiling() -> None:
    a = RackDrivers(cooling_ceiling=40.0)
    b = RackDrivers(cooling_ceiling=25.0)
    c = RackDrivers()  # no ceiling at all
    assert combine_drivers(a, b, c).cooling_ceiling == 25.0
    assert combine_drivers(c).cooling_ceiling is None


def test_combine_drivers_with_no_args_is_a_no_op() -> None:
    combined = combine_drivers()
    assert combined == RackDrivers()


# --- ExecutionManager: ramp shape ------------------------------------------


def test_no_drivers_before_any_execution_starts() -> None:
    manager = ExecutionManager()
    result = manager.compute_tick(_now())
    assert result.drivers == {}
    assert result.took_effect == []
    assert result.finished == []


def test_drivers_are_zero_at_the_instant_of_activation() -> None:
    manager = ExecutionManager()
    rack_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    started_at = _now()
    manager.start(execution_id, ExecutionActionType.WORKLOAD_MIGRATION, {rack_id}, set(), started_at)

    result = manager.compute_tick(started_at)
    driver = result.drivers[rack_id]
    assert driver.gpu_bias == 0.0


def test_drivers_ramp_in_linearly() -> None:
    manager = ExecutionManager()
    rack_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    started_at = _now()
    manager.start(execution_id, ExecutionActionType.WORKLOAD_MIGRATION, {rack_id}, set(), started_at)

    halfway = started_at + timedelta(seconds=RAMP_SECONDS / 2)
    result = manager.compute_tick(halfway)
    driver = result.drivers[rack_id]

    full_bias = -30.0  # EFFECTS[WORKLOAD_MIGRATION].primary_gpu_bias
    assert driver.gpu_bias == full_bias * 0.5


def test_drivers_hold_at_full_effect_and_took_effect_fires_once() -> None:
    manager = ExecutionManager()
    rack_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    started_at = _now()
    manager.start(execution_id, ExecutionActionType.WORKLOAD_MIGRATION, {rack_id}, set(), started_at)

    just_after_ramp = started_at + timedelta(seconds=RAMP_SECONDS + 1)
    result = manager.compute_tick(just_after_ramp)
    assert result.took_effect == [execution_id]
    assert result.drivers[rack_id].gpu_bias == -30.0

    mid_hold = started_at + timedelta(seconds=RAMP_SECONDS + HOLD_SECONDS / 2)
    result2 = manager.compute_tick(mid_hold)
    assert result2.took_effect == []  # only fires once, on the transition
    assert result2.drivers[rack_id].gpu_bias == -30.0


def test_drivers_ramp_out_after_hold_and_finished_fires_once() -> None:
    manager = ExecutionManager()
    rack_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    started_at = _now()
    manager.start(execution_id, ExecutionActionType.WORKLOAD_MIGRATION, {rack_id}, set(), started_at)

    ramp_out_midpoint = started_at + timedelta(seconds=RAMP_SECONDS + HOLD_SECONDS + RAMP_SECONDS / 2)
    result = manager.compute_tick(ramp_out_midpoint)
    assert result.drivers[rack_id].gpu_bias == -15.0  # half of -30

    just_after_end = started_at + timedelta(seconds=TOTAL_LIFECYCLE_SECONDS + 1)
    result2 = manager.compute_tick(just_after_end)
    assert result2.finished == [execution_id]
    assert rack_id not in result2.drivers  # released back to organic physics
    assert manager.active_count == 0


def test_finished_only_fires_once() -> None:
    manager = ExecutionManager()
    rack_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    started_at = _now()
    manager.start(execution_id, ExecutionActionType.JOB_DELAY, {rack_id}, set(), started_at)

    after_end = started_at + timedelta(seconds=TOTAL_LIFECYCLE_SECONDS + 1)
    manager.compute_tick(after_end)
    result2 = manager.compute_tick(after_end + timedelta(seconds=1))
    assert result2.finished == []  # already removed, nothing to report again


# --- ExecutionManager: per-action shape -------------------------------------


def test_workload_migration_reduces_primary_and_boosts_redistribute_racks() -> None:
    manager = ExecutionManager()
    primary_id = uuid.uuid4()
    other_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    started_at = _now()
    manager.start(execution_id, ExecutionActionType.WORKLOAD_MIGRATION, {primary_id}, {other_id}, started_at)

    fully_ramped = started_at + timedelta(seconds=RAMP_SECONDS + 1)
    result = manager.compute_tick(fully_ramped)

    assert result.drivers[primary_id].gpu_bias < 0
    assert result.drivers[other_id].gpu_bias > 0
    assert abs(result.drivers[primary_id].gpu_bias) > result.drivers[other_id].gpu_bias


def test_cooling_adjustment_only_touches_fan_and_cooling_not_gpu() -> None:
    manager = ExecutionManager()
    rack_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    started_at = _now()
    manager.start(execution_id, ExecutionActionType.COOLING_ADJUSTMENT, {rack_id}, set(), started_at)

    fully_ramped = started_at + timedelta(seconds=RAMP_SECONDS + 1)
    driver = manager.compute_tick(fully_ramped).drivers[rack_id]

    assert driver.fan_bias > 0
    assert driver.cooling_bias > 0
    assert driver.gpu_bias == 0.0


def test_job_delay_only_reduces_primary_gpu_no_redistribution() -> None:
    manager = ExecutionManager()
    rack_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    started_at = _now()
    manager.start(execution_id, ExecutionActionType.JOB_DELAY, {rack_id}, set(), started_at)

    fully_ramped = started_at + timedelta(seconds=RAMP_SECONDS + 1)
    result = manager.compute_tick(fully_ramped)

    assert result.drivers[rack_id].gpu_bias < 0
    assert result.drivers[rack_id].fan_bias == 0.0


def test_cluster_rebalance_spreads_across_multiple_racks() -> None:
    manager = ExecutionManager()
    hot_ids = {uuid.uuid4(), uuid.uuid4()}
    healthy_ids = {uuid.uuid4(), uuid.uuid4()}
    execution_id = uuid.uuid4()
    started_at = _now()
    manager.start(execution_id, ExecutionActionType.CLUSTER_REBALANCE, hot_ids, healthy_ids, started_at)

    fully_ramped = started_at + timedelta(seconds=RAMP_SECONDS + 1)
    result = manager.compute_tick(fully_ramped)

    for hot_id in hot_ids:
        assert result.drivers[hot_id].gpu_bias < 0
    for healthy_id in healthy_ids:
        assert result.drivers[healthy_id].gpu_bias > 0


def test_two_executions_on_the_same_rack_combine_additively() -> None:
    """E.g. a cooling adjustment and a job delay both active on one rack at
    once — their effects should add, not overwrite each other.
    """
    manager = ExecutionManager()
    rack_id = uuid.uuid4()
    started_at = _now()
    manager.start(uuid.uuid4(), ExecutionActionType.JOB_DELAY, {rack_id}, set(), started_at)
    manager.start(uuid.uuid4(), ExecutionActionType.COOLING_ADJUSTMENT, {rack_id}, set(), started_at)

    fully_ramped = started_at + timedelta(seconds=RAMP_SECONDS + 1)
    driver = manager.compute_tick(fully_ramped).drivers[rack_id]

    assert driver.gpu_bias == -15.0  # from job_delay alone
    assert driver.fan_bias == 25.0  # from cooling_adjustment alone
    assert driver.cooling_bias == 12.0
