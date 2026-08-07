"""Unit tests for event detection — pure function, no database required."""

import uuid

from app.models.enums import EventSeverity, RackStatus
from app.services.event_service import detect_rack_events
from app.simulation.state import RackState


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


def test_temperature_warning_threshold_crossing_creates_event() -> None:
    cluster_id = uuid.uuid4()
    rack_id = uuid.uuid4()
    previous = _make_rack(id=rack_id, temperature=78.0)
    current = _make_rack(id=rack_id, temperature=82.0)

    events = detect_rack_events(cluster_id, previous, current)

    assert any(e.severity == EventSeverity.WARNING and "exceeded threshold" in e.title for e in events)


def test_temperature_critical_threshold_crossing_creates_critical_event() -> None:
    cluster_id = uuid.uuid4()
    rack_id = uuid.uuid4()
    previous = _make_rack(id=rack_id, temperature=88.0)
    current = _make_rack(id=rack_id, temperature=92.0)

    events = detect_rack_events(cluster_id, previous, current)

    assert any(e.severity == EventSeverity.CRITICAL for e in events)


def test_no_event_when_nothing_significant_changes() -> None:
    cluster_id = uuid.uuid4()
    rack_id = uuid.uuid4()
    previous = _make_rack(id=rack_id, temperature=60.0, power_draw=8.0)
    current = _make_rack(id=rack_id, temperature=60.3, power_draw=8.1)

    assert detect_rack_events(cluster_id, previous, current) == []


def test_status_degradation_creates_warning_event() -> None:
    cluster_id = uuid.uuid4()
    rack_id = uuid.uuid4()
    previous = _make_rack(id=rack_id, status=RackStatus.HEALTHY)
    current = _make_rack(id=rack_id, status=RackStatus.WARNING, health_score=60.0)

    events = detect_rack_events(cluster_id, previous, current)

    assert any("degraded" in e.title for e in events)


def test_status_recovery_creates_info_event() -> None:
    cluster_id = uuid.uuid4()
    rack_id = uuid.uuid4()
    previous = _make_rack(id=rack_id, status=RackStatus.CRITICAL)
    current = _make_rack(id=rack_id, status=RackStatus.HEALTHY, health_score=95.0)

    events = detect_rack_events(cluster_id, previous, current)

    assert any(e.severity == EventSeverity.INFO and "recovered" in e.title for e in events)


def test_power_spike_creates_event() -> None:
    cluster_id = uuid.uuid4()
    rack_id = uuid.uuid4()
    previous = _make_rack(id=rack_id, power_draw=8.0)
    current = _make_rack(id=rack_id, power_draw=13.0)

    events = detect_rack_events(cluster_id, previous, current)

    assert any("power spike" in e.title.lower() for e in events)


def test_cooling_stabilized_creates_info_event() -> None:
    cluster_id = uuid.uuid4()
    rack_id = uuid.uuid4()
    previous = _make_rack(id=rack_id, cooling_efficiency=45.0)
    current = _make_rack(id=rack_id, cooling_efficiency=55.0)

    events = detect_rack_events(cluster_id, previous, current)

    assert any("cooling stabilized" in e.title.lower() for e in events)
