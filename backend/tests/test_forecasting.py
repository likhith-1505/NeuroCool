"""Unit tests for the forecasting engine — no database/Redis required,
same style as test_physics.py and test_scenario_manager.py.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.forecasting.base import ForecastContext, HistoryPoint
from app.forecasting.history import RETENTION, RackHistory, TelemetryHistory
from app.forecasting.risk import compute_risk
from app.forecasting.trend import MIN_SAMPLES_FOR_TREND, TrendForecastEngine, _linear_fit
from app.models.enums import RackStatus
from app.simulation.state import RackState


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


def _history_points(start: datetime, temps: list[float], step_seconds: float = 1.0) -> list[HistoryPoint]:
    return [
        HistoryPoint(
            timestamp=start + timedelta(seconds=i * step_seconds),
            temperature=temp,
            gpu_utilization=50.0,
            power_draw=9.0,
            cooling_efficiency=60.0,
        )
        for i, temp in enumerate(temps)
    ]


# --- _linear_fit -----------------------------------------------------------


def test_linear_fit_recovers_exact_slope_and_intercept() -> None:
    # y = 2x + 10, noise-free
    points = [(float(x), 2.0 * x + 10.0) for x in range(10)]
    slope, intercept, r_squared = _linear_fit(points)
    assert slope == pytest_approx(2.0)
    assert intercept == pytest_approx(10.0)
    assert r_squared == pytest_approx(1.0)


def test_linear_fit_flat_line_has_zero_slope() -> None:
    points = [(float(x), 42.0) for x in range(5)]
    slope, intercept, r_squared = _linear_fit(points)
    assert slope == 0.0
    assert intercept == 42.0
    assert r_squared == 1.0  # perfectly flat counts as a perfect fit


def test_linear_fit_single_point_has_no_slope() -> None:
    slope, intercept, r_squared = _linear_fit([(0.0, 55.0)])
    assert slope == 0.0
    assert intercept == 55.0
    assert r_squared == 0.0


def pytest_approx(value: float, tol: float = 1e-6):
    class _Approx:
        def __eq__(self, other: object) -> bool:
            return isinstance(other, (int, float)) and abs(other - value) < tol

    return _Approx()


# --- compute_risk ------------------------------------------------------


def test_risk_increases_with_predicted_temperature() -> None:
    cool = compute_risk(60.0, 0.0, 0.0, 0.0, 60.0, False, 0.0)
    hot = compute_risk(95.0, 0.0, 0.0, 0.0, 60.0, False, 0.0)
    assert hot > cool
    assert 0.0 <= cool <= 100.0
    assert 0.0 <= hot <= 100.0


def test_risk_increases_with_rising_trend_and_decreases_with_falling() -> None:
    base = compute_risk(75.0, 0.0, 0.0, 0.0, 60.0, False, 0.0)
    rising = compute_risk(75.0, 0.05, 0.1, 0.02, 60.0, False, 0.0)
    falling = compute_risk(75.0, -0.05, -0.1, -0.02, 60.0, False, 0.0)
    assert rising > base > falling


def test_risk_increases_as_cooling_efficiency_drops() -> None:
    good_cooling = compute_risk(75.0, 0.0, 0.0, 0.0, 80.0, False, 0.0)
    bad_cooling = compute_risk(75.0, 0.0, 0.0, 0.0, 20.0, False, 0.0)
    assert bad_cooling > good_cooling


def test_risk_increases_when_scenario_active() -> None:
    normal = compute_risk(75.0, 0.0, 0.0, 0.0, 60.0, False, 0.0)
    during_scenario = compute_risk(75.0, 0.0, 0.0, 0.0, 60.0, True, 0.0)
    assert during_scenario > normal


def test_risk_increases_with_neighbor_trend_hint() -> None:
    no_neighbors = compute_risk(75.0, 0.0, 0.0, 0.0, 60.0, False, 0.0)
    trending_neighbors = compute_risk(75.0, 0.0, 0.0, 0.0, 60.0, False, 1.0)
    assert trending_neighbors > no_neighbors


def test_risk_is_always_clamped_0_to_100() -> None:
    assert 0.0 <= compute_risk(99.0, 5.0, 5.0, 5.0, 10.0, True, 1.0) <= 100.0
    assert 0.0 <= compute_risk(35.0, -5.0, -5.0, -5.0, 99.0, False, 0.0) <= 100.0


# --- RackHistory / TelemetryHistory -----------------------------------------


def test_rack_history_records_and_returns_points_in_order() -> None:
    history = RackHistory()
    now = datetime.now(UTC)
    for i in range(5):
        history.append(now + timedelta(seconds=i), _make_rack(temperature=60.0 + i))
    points = history.recent()
    assert len(points) == 5
    assert [p.temperature for p in points] == [60.0, 61.0, 62.0, 63.0, 64.0]


def test_rack_history_skips_exact_duplicates() -> None:
    history = RackHistory()
    now = datetime.now(UTC)
    rack = _make_rack(temperature=60.0)
    history.append(now, rack)
    history.append(now + timedelta(seconds=1), rack)  # identical reading
    assert len(history) == 1


def test_rack_history_prunes_beyond_retention() -> None:
    history = RackHistory()
    now = datetime.now(UTC)
    history.append(now, _make_rack(temperature=50.0))
    later = now + RETENTION + timedelta(seconds=1)
    history.append(later, _make_rack(temperature=99.0))
    points = history.recent()
    assert len(points) == 1
    assert points[0].temperature == 99.0


def test_rack_history_recent_window_filters_to_recent_only() -> None:
    history = RackHistory()
    now = datetime.now(UTC)
    history.append(now, _make_rack(temperature=50.0))
    history.append(now + timedelta(seconds=200), _make_rack(temperature=99.0))
    recent = history.recent(window=timedelta(seconds=90))
    assert len(recent) == 1
    assert recent[0].temperature == 99.0


def test_telemetry_history_tracks_multiple_racks_independently() -> None:
    history = TelemetryHistory()
    now = datetime.now(UTC)
    rack_a = _make_rack(temperature=60.0)
    rack_b = _make_rack(temperature=80.0)
    history.append(now, [rack_a, rack_b])
    assert history.for_rack(rack_a.id).recent()[0].temperature == 60.0
    assert history.for_rack(rack_b.id).recent()[0].temperature == 80.0


# --- TrendForecastEngine -----------------------------------------------


def _context(rack: RackState, history: list[HistoryPoint], now: datetime, **overrides: object) -> ForecastContext:
    defaults: dict[str, object] = dict(
        rack=rack, history=history, scenario_key="normal", scenario_active=False,
        neighbor_trend_hint=0.0, now=now,
    )
    defaults.update(overrides)
    return ForecastContext(**defaults)  # type: ignore[arg-type]


def test_forecast_with_insufficient_history_is_flat_and_low_confidence() -> None:
    engine = TrendForecastEngine()
    now = datetime.now(UTC)
    rack = _make_rack(temperature=65.0)
    context = _context(rack, history=[], now=now)

    predictions = engine.forecast(context, (30, 300))
    assert len(predictions) == 2
    for p in predictions:
        assert p.predicted_temperature == rack.temperature
        assert p.confidence == 5.0  # MIN_CONFIDENCE


def test_forecast_produces_one_prediction_per_horizon_in_order() -> None:
    engine = TrendForecastEngine()
    now = datetime.now(UTC)
    history = _history_points(now - timedelta(seconds=10), [65.0] * (MIN_SAMPLES_FOR_TREND + 2))
    rack = _make_rack(temperature=65.0)
    context = _context(rack, history=history, now=now)

    horizons = (30, 60, 120, 300)
    predictions = engine.forecast(context, horizons)
    assert [p.horizon_seconds for p in predictions] == list(horizons)


def test_forecast_extrapolates_rising_temperature_trend() -> None:
    engine = TrendForecastEngine()
    now = datetime.now(UTC)
    start = now - timedelta(seconds=20)
    # +0.5 deg C per second, clearly rising
    history = [
        HistoryPoint(
            timestamp=start + timedelta(seconds=i), temperature=60.0 + i * 0.5,
            gpu_utilization=50.0, power_draw=9.0, cooling_efficiency=60.0,
        )
        for i in range(21)
    ]
    rack = _make_rack(temperature=history[-1].temperature)
    context = _context(rack, history=history, now=now)

    predictions = engine.forecast(context, (30, 300))
    near, far = predictions
    assert far.predicted_temperature > near.predicted_temperature > rack.temperature


def test_forecast_extrapolates_falling_temperature_trend() -> None:
    engine = TrendForecastEngine()
    now = datetime.now(UTC)
    start = now - timedelta(seconds=20)
    history = [
        HistoryPoint(
            timestamp=start + timedelta(seconds=i), temperature=90.0 - i * 0.5,
            gpu_utilization=50.0, power_draw=9.0, cooling_efficiency=60.0,
        )
        for i in range(21)
    ]
    rack = _make_rack(temperature=history[-1].temperature)
    context = _context(rack, history=history, now=now)

    predictions = engine.forecast(context, (30, 300))
    near, far = predictions
    assert far.predicted_temperature < near.predicted_temperature < rack.temperature


def test_forecast_confidence_decreases_as_horizon_increases() -> None:
    engine = TrendForecastEngine()
    now = datetime.now(UTC)
    history = _history_points(now - timedelta(seconds=60), [65.0 + i * 0.1 for i in range(61)])
    rack = _make_rack(temperature=history[-1].temperature)
    context = _context(rack, history=history, now=now)

    predictions = engine.forecast(context, (30, 60, 120, 300))
    confidences = [p.confidence for p in predictions]
    assert confidences == sorted(confidences, reverse=True)
    assert confidences[0] > confidences[-1]


def test_forecast_predicted_values_stay_within_physical_bounds() -> None:
    engine = TrendForecastEngine()
    now = datetime.now(UTC)
    start = now - timedelta(seconds=20)
    # Extreme rising trend to try to push predictions past their clamps.
    history = [
        HistoryPoint(
            timestamp=start + timedelta(seconds=i), temperature=90.0 + i * 3.0,
            gpu_utilization=90.0 + i * 3.0, power_draw=9.0 + i, cooling_efficiency=60.0 - i * 3.0,
        )
        for i in range(21)
    ]
    rack = _make_rack(temperature=history[-1].temperature, gpu_utilization=99.0)
    context = _context(rack, history=history, now=now)

    predictions = engine.forecast(context, (300,))
    p = predictions[0]
    assert 35.0 <= p.predicted_temperature <= 99.0
    assert 0.0 <= p.predicted_gpu_utilization <= 100.0
    assert 30.0 <= p.predicted_cooling <= 99.0
    assert p.predicted_power >= 0.0
    assert 0.0 <= p.predicted_risk <= 100.0
    assert 0.0 <= p.predicted_health <= 100.0


def test_forecast_scenario_active_raises_risk_relative_to_normal() -> None:
    engine = TrendForecastEngine()
    now = datetime.now(UTC)
    history = _history_points(now - timedelta(seconds=10), [70.0] * (MIN_SAMPLES_FOR_TREND + 2))
    rack = _make_rack(temperature=70.0)

    normal_context = _context(rack, history=history, now=now, scenario_active=False)
    scenario_context = _context(rack, history=history, now=now, scenario_active=True)

    normal_risk = engine.forecast(normal_context, (60,))[0].predicted_risk
    scenario_risk = engine.forecast(scenario_context, (60,))[0].predicted_risk
    assert scenario_risk > normal_risk
